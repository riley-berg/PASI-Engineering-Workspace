const DEFAULT_API_BASE = window.location.origin;

export function healthTone(status) {
  switch (status) {
    case "connected": return "success";
    case "degraded":
    case "recovering": return "warning";
    case "disconnected": return "danger";
    default: return "neutral";
  }
}

export function buildViewModel(projection) {
  const operation = projection?.operation ?? {};
  const health = projection?.health ?? {};
  const events = Array.isArray(projection?.events) ? projection.events : [];
  const evidence = Array.isArray(projection?.evidence_refs) ? projection.evidence_refs : [];
  const metadata = operation.metadata ?? {};

  const milestoneState = (name) => {
    const value = metadata[`acceptance_${name}`] ?? metadata[`milestone_${name}`];
    if (typeof value === "string" && value.trim()) return value;
    const matching = events.find((event) =>
      String(event.event_type || "").toLowerCase().includes(`acceptance.${name}`)
    );
    return matching?.payload?.status || "not reported";
  };

  const sequence = Number(metadata.long_run_sequence || 0);
  const target = Number(metadata.long_run_target || 168);

  return {
    operation,
    health,
    events,
    evidence,
    runtime: projection?.runtime ?? {},
    lineage: Array.isArray(projection?.lineage) ? projection.lineage : [],
    milestones: {
      m0: milestoneState("m0"),
      m1: milestoneState("m1"),
      m2: milestoneState("m2"),
      longrun: milestoneState("longrun"),
    },
    longRun: {
      available: Number.isFinite(sequence) && sequence > 0,
      sequence: sequence > 0 ? sequence : null,
      target: target > 0 ? target : 168,
    },
  };
}

function qs(selector) {
  return document.querySelector(selector);
}

function setText(selector, value) {
  const node = qs(selector);
  if (node) node.textContent = value == null || value === "" ? "—" : String(value);
}

function setPill(selector, text, tone = "neutral") {
  const node = qs(selector);
  if (!node) return;
  node.textContent = text;
  node.className = `pill ${tone}`;
}

function humanTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function renderHealth(health) {
  const status = health.connection_status || "unknown";
  setText("#health-status", status);
  setPill("#health-pill", status, healthTone(status));
  setText("#health-heartbeat", humanTime(health.heartbeat_at));
  setText("#health-recovery", health.recovery_phase);
  setText("#health-reason", health.degraded_reason);
  setText("#health-code", health.code_head);
  setText("#health-controller", health.controller_version);
  setText("#health-runner", health.runner_version);
  setText("#health-provider", health.provider_version);
  setText("#health-revision", health.revision);
}

function renderOperation(vm) {
  const op = vm.operation;
  setText("#operation-title", op.operation_id || "No operation selected");
  setPill("#operation-pill", op.status || "unavailable", healthTone(
    op.status === "completed" ? "connected" :
    op.status === "failed" ? "disconnected" : "degraded"
  ));
  setText("#operation-task", op.task_id);
  setText("#operation-run", op.run_id);
  setText("#operation-phase", op.phase);
  setText("#operation-provider", op.provider);
  setText("#operation-status", op.status);
  setText("#operation-revision", op.state_revision);
  setText("#operation-verification", op.verification_status);
  setText("#operation-failure", op.failure_signature);
  const input = qs("#operation-id");
  if (input && op.operation_id) input.value = op.operation_id;
}

function renderAcceptance(vm) {
  const node = qs("#acceptance-grid");
  if (!node) return;
  node.replaceChildren();
  const entries = [
    ["M0", vm.milestones.m0],
    ["M1", vm.milestones.m1],
    ["M2", vm.milestones.m2],
    ["168h", vm.milestones.longrun],
  ];
  for (const [name, value] of entries) {
    const card = document.createElement("div");
    card.className = "status-card";
    const strong = document.createElement("strong");
    strong.textContent = name;
    const span = document.createElement("span");
    span.textContent = value;
    card.append(strong, span);
    node.append(card);
  }

  const list = qs("#evidence-list");
  list.replaceChildren();
  if (!vm.evidence.length) {
    list.className = "list empty";
    const item = document.createElement("li");
    item.textContent = "No evidence references reported.";
    list.append(item);
    return;
  }
  list.className = "list";
  for (const ref of vm.evidence) {
    const item = document.createElement("li");
    const link = document.createElement("a");
    link.href = ref;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = ref;
    item.append(link);
    list.append(item);
  }
}

function renderTimeline(vm) {
  const timeline = qs("#timeline");
  timeline.replaceChildren();
  const events = vm.events;
  setText("#event-count", `${events.length} event${events.length === 1 ? "" : "s"}`);
  if (!events.length) {
    const item = document.createElement("li");
    item.className = "empty";
    item.textContent = "No durable events reported for this operation.";
    timeline.append(item);
    return;
  }
  for (const event of events) {
    const item = document.createElement("li");
    const meta = document.createElement("div");
    meta.className = "event-meta";
    const sequence = document.createElement("span");
    sequence.textContent = `#${event.sequence ?? "?"} · ${event.source || "unknown"}`;
    const when = document.createElement("span");
    when.textContent = humanTime(event.occurred_at);
    meta.append(sequence, when);
    const title = document.createElement("div");
    title.className = "event-title";
    title.textContent = event.event_type || "runtime event";
    const body = document.createElement("div");
    body.className = "event-body";
    const payload = event.payload && typeof event.payload === "object"
      ? JSON.stringify(event.payload)
      : "";
    body.textContent = payload;
    item.append(meta, title, body);
    timeline.append(item);
  }
}

function renderLongRun(vm) {
  const progress = qs("#longrun-progress");
  const sequence = qs("#longrun-sequence");
  const target = qs("#longrun-target");
  const note = qs("#longrun-note");
  if (!vm.longRun.available) {
    progress.style.width = "0%";
    sequence.textContent = "Sequence: unavailable";
    target.textContent = "Target: unavailable";
    note.textContent = "Progress is shown only when authoritative operation metadata reports long-run sequence data.";
    return;
  }
  const pct = Math.min(100, Math.max(0, (vm.longRun.sequence / vm.longRun.target) * 100));
  progress.style.width = `${pct}%`;
  sequence.textContent = `Sequence: ${vm.longRun.sequence}`;
  target.textContent = `Target: ${vm.longRun.target}`;
  note.textContent = `Authoritative sequence progress: ${Math.round(pct)}%.`;
}

async function getJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { Accept: "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Request failed: ${response.status}`);
  }
  return payload;
}

async function loadHealth(apiBase) {
  return getJson(`${apiBase}/v1/runtime/health`);
}

async function loadOperation(apiBase, operationId) {
  return getJson(`${apiBase}/v1/runtime/operations/${encodeURIComponent(operationId)}`);
}

async function control(apiBase, action, operationId, revision, token) {
  if (!token) throw new Error("A runtime token is required for mutating controls.");
  return getJson(`${apiBase}/v1/runtime/controls`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      "Idempotency-Key": crypto.randomUUID(),
    },
    body: JSON.stringify({
      operation_id: operationId,
      action,
      expected_revision: revision,
    }),
  });
}

async function refresh() {
  const apiBase = document.documentElement.dataset.apiBase || DEFAULT_API_BASE;
  const banner = qs("#banner");
  banner.className = "banner loading";
  banner.textContent = "Refreshing authoritative runtime state…";
  try {
    const healthPayload = await loadHealth(apiBase);
    renderHealth(healthPayload.health || {});
    const operationId = qs("#operation-id").value.trim() ||
      new URLSearchParams(location.search).get("operation_id") || "";
    if (!operationId) {
      renderOperation(buildViewModel({}));
      renderAcceptance(buildViewModel({}));
      renderTimeline(buildViewModel({}));
      renderLongRun(buildViewModel({}));
      banner.className = "banner ready";
      banner.textContent = "Runtime API is healthy. Select an operation to inspect.";
      qs("#last-refresh").textContent = `Refreshed ${new Date().toLocaleTimeString()}`;
      return;
    }
    const projection = await loadOperation(apiBase, operationId);
    const vm = buildViewModel(projection);
    renderOperation(vm);
    renderAcceptance(vm);
    renderTimeline(vm);
    renderLongRun(vm);
    banner.className = "banner ready";
    banner.textContent = "Authoritative runtime state loaded.";
    qs("#last-refresh").textContent = `Refreshed ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    banner.className = "banner error";
    banner.textContent = error instanceof Error ? error.message : String(error);
  }
}

function bindControls() {
  for (const button of document.querySelectorAll("[data-action]")) {
    button.addEventListener("click", async () => {
      const action = button.dataset.action;
      const operationId = qs("#operation-id").value.trim();
      const revision = Number(qs("#operation-revision").textContent);
      const token = qs("#token").value.trim();
      if (!operationId || !Number.isInteger(revision) || revision < 0) {
        qs("#control-result").textContent = "Load a live operation before using controls.";
        return;
      }
      button.disabled = true;
      try {
        const apiBase = document.documentElement.dataset.apiBase || DEFAULT_API_BASE;
        const result = await control(apiBase, action, operationId, revision, token);
        qs("#control-result").textContent =
          `${action} accepted at state revision ${result.state_revision}.`;
        await refresh();
      } catch (error) {
        qs("#control-result").textContent =
          error instanceof Error ? error.message : String(error);
      } finally {
        button.disabled = false;
      }
    });
  }
}

function boot() {
  qs("#refresh").addEventListener("click", refresh);
  qs("#operation-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const operationId = qs("#operation-id").value.trim();
    const url = new URL(location.href);
    if (operationId) url.searchParams.set("operation_id", operationId);
    else url.searchParams.delete("operation_id");
    history.replaceState({}, "", url);
    refresh();
  });
  bindControls();
  refresh();
}

if (typeof document !== "undefined") {
  boot();
}
