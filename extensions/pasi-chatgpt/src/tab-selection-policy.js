/* global globalThis */

(function () {
  const POLICY_VERSION = "pasi-tab-selection-v1";
  const CHATGPT_URL = /^https:\/\/(?:www\.)?chatgpt\.com\//i;

  function toSet(value) {
    if (value instanceof Set) return value;
    if (Array.isArray(value)) return new Set(value);
    return new Set();
  }

  function isSupportedChatGPTTab(tab) {
    return Boolean(
      tab &&
      Number.isInteger(tab.id) &&
      tab.id > 0 &&
      CHATGPT_URL.test(String(tab.url || ""))
    );
  }

  function classifyTab(tab, context = {}) {
    const reasons = [];
    if (!isSupportedChatGPTTab(tab)) {
      return {eligible: false, score: Number.NEGATIVE_INFINITY, reasons: ["unsupported_tab"]};
    }

    const busyTabIds = toSet(context.busyTabIds);
    if (busyTabIds.has(tab.id)) {
      return {eligible: false, score: Number.NEGATIVE_INFINITY, reasons: ["active_pasi_work"]};
    }

    if (tab.pinned === true && context.allowPinned !== true) {
      return {eligible: false, score: Number.NEGATIVE_INFINITY, reasons: ["pinned_tab"]};
    }

    let score = 0;
    if (tab.id === context.preferredTabId) {
      score += 1000;
      reasons.push("preferred_tab");
    }
    if (tab.id === context.currentTabId) {
      score += 500;
      reasons.push("current_tab");
    }

    if (tab.active === true) {
      score += context.preferActive === true ? 120 : 0;
      if (context.preferActive === true) reasons.push("active_tab");
    } else {
      score += 40;
      reasons.push("inactive_reuse");
    }

    if (tab.status === "complete") {
      score += 25;
      reasons.push("document_complete");
    } else {
      score -= 50;
      reasons.push("document_not_complete");
    }

    if (
      context.lastFocusedWindowId != null &&
      tab.windowId === context.lastFocusedWindowId
    ) {
      score += 10;
      reasons.push("same_focused_window");
    }

    return {eligible: true, score, reasons};
  }

  function selectReusableTab(tabs, context = {}) {
    const candidates = Array.isArray(tabs) ? tabs : [];
    let selected = null;
    let selectedClassification = null;

    const considered = candidates.map((tab, index) => {
      const classification = classifyTab(tab, context);
      const candidate = {
        tab_id: Number.isInteger(tab?.id) ? tab.id : null,
        eligible: classification.eligible,
        score: classification.score,
        reasons: classification.reasons,
        index,
      };
      if (
        classification.eligible &&
        (
          !selectedClassification ||
          classification.score > selectedClassification.score ||
          (
            classification.score === selectedClassification.score &&
            index < selectedClassification.index
          )
        )
      ) {
        selected = tab;
        selectedClassification = {...classification, index};
      }
      return candidate;
    });

    if (!selected || !selectedClassification) {
      return {
        policy_version: POLICY_VERSION,
        selected_tab_id: null,
        reused_existing: false,
        reason: "no_safe_existing_tab",
        candidates: considered,
      };
    }

    return {
      policy_version: POLICY_VERSION,
      selected_tab_id: selected.id,
      reused_existing: true,
      reason: selectedClassification.reasons[0] || "safe_existing_tab",
      candidates: considered,
    };
  }

  globalThis.PASI_TAB_SELECTION_POLICY = Object.freeze({
    POLICY_VERSION,
    isSupportedChatGPTTab,
    classifyTab,
    selectReusableTab,
  });
})();
