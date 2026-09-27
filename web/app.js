const DEFAULT_API_BASE = typeof window !== "undefined" ? window.location.origin : "";

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
    recoveryEvents: events.filter((event) => /recovery|reconnect|connection_lost|retry/i.test(String(event.event_type || "")) || Boolean(event.payload?.recovery_phase)),
    longRun: {
      available: Number.isFinite(sequence) && sequence > 0,
      sequence: sequence > 0 ? sequence : null,
      target: target > 0 ? target : 168,
    },
  };
}

export function normalizeRecoveryDecision(vm) {
  const candidates = vm.recoveryEvents
    .map((event) => event.payload || {})
    .filter((payload) =>
      typeof payload.classification === "string" ||
      typeof payload.reason_code === "string" ||
      typeof payload.action === "string"
    );
  const latest = candidates.length ? candidates[candidates.length - 1] : null;
  if (!latest) {
    return {
      available: false,
      classification: "not reported",
      condition: "",
      action: "",
      max_attempts: null,
      attempts: null,
      preserve_operation_identity: null,
      evidence_preserved: null,
      planner_handoff: "",
    };
  }
  return {
    available: true,
    classification: latest.classification || "not reported",
    condition: latest.reason_code || latest.failure_code || "",
    action: latest.action || "",
    max_attempts: Number.isFinite(Number(latest.max_attempts)) ? Number(latest.max_attempts) : null,
    attempts: Number.isFinite(Number(latest.attempts ?? latest.retry_count))
      ? Number(latest.attempts ?? latest.retry_count)
      : null,
    preserve_operation_identity:
      typeof latest.preserve_operation_identity === "boolean"
        ? latest.preserve_operation_identity
        : null,
    evidence_preserved: Array.isArray(latest.evidence_refs)
      ? latest.evidence_refs.length > 0
      : null,
    planner_handoff: typeof latest.planner_handoff === "string"
      ? latest.planner_handoff
      : "",
  };
}

export function normalizeLedger(entries) {
  if (!Array.isArray(entries)) return [];
  return entries.map((entry) => ({
    operation_id: entry.operation_id || "",
    task_id: entry.task_id || "",
    run_id: entry.run_id || "",
    provider: entry.provider || "",
    branch: entry.branch || "",
    pr_number: entry.pr_number ?? null,
    outcome: entry.outcome || "",
    parent_operation_id: entry.parent_operation_id || "",
    created_at: entry.created_at || "",
  }));
}

export function normalizeFailures(signatures) {
  if (!Array.isArray(signatures)) return [];
  return signatures.map((signature) => ({
    signature_id: signature.signature_id || "",
    subsystem: signature.subsystem || "",
    failure_code: signature.failure_code || "",
    failure_family: signature.failure_family || "",
    occurrence_count: Number(signature.occurrence_count || 0),
    latest_operation_id: signature.latest_operation_id || "",
    evidence_ref: signature.evidence_ref || "",
    affected_operations: Array.isArray(signature.affected_operations)
      ? signature.affected_operations
      : [],
    current_code_head: signature.current_code_head || "",
  }));
}

export function normalizeNotifications(notifications) {
  if (!Array.isArray(notifications)) return [];
  return notifications.map((notification) => ({
    notification_id: notification.notification_id || "",
    scope: notification.scope || "",
    source_event_id: notification.source_event_id || "",
    severity: notification.severity || "info",
    message: notification.message || "",
    entity_type: notification.entity_type || "",
    entity_id: notification.entity_id || "",
    occurred_at: notification.occurred_at || "",
    acknowledged: Boolean(notification.acknowledged),
    revision: Number(notification.revision || 0),
  }));
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

function renderRecovery(vm) {
  const decision = normalizeRecoveryDecision(vm);
  const pill = qs("#recovery-classification");
  pill.textContent = decision.classification;
  pill.className = `pill ${decision.classification === "terminal" || decision.classification === "human_required" ? "danger" : decision.available ? "warning" : "neutral"}`;
  setText("#recovery-condition", decision.condition);
  setText("#recovery-action", decision.action);
  setText("#recovery-budget", decision.max_attempts == null ? "" : decision.max_attempts);
  setText("#recovery-attempts", decision.attempts == null ? "" : decision.attempts);
  setText(
    "#recovery-identity",
    decision.preserve_operation_identity == null
      ? ""
      : decision.preserve_operation_identity ? "preserved" : "not preserved",
  );
  setText(
    "#recovery-evidence",
    decision.evidence_preserved == null
      ? ""
      : decision.evidence_preserved ? "preserved" : "not reported",
  );
  qs("#recovery-handoff").textContent = decision.planner_handoff || (
    decision.available
      ? "Authoritative recovery decision loaded from durable event state."
      : "No persisted recovery decision metadata reported."
  );

  const list = qs("#recovery-list");
  if (!list) return;
  list.replaceChildren();
  if (!vm.recoveryEvents.length) {
    list.className = "list empty";
    const item = document.createElement("li");
    item.textContent = "No recovery events reported.";
    list.append(item);
    return;
  }

  list.className = "list";
  for (const event of vm.recoveryEvents) {
    const item = document.createElement("li");
    item.textContent = [
      event.event_type || "recovery",
      event.payload?.phase || event.payload?.recovery_phase || event.payload?.status || "",
      event.payload?.result || "",
    ].filter(Boolean).join(" · ");
    list.append(item);
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

function renderMigration(payload) {
  const migration = payload || {};
  setText("#migration-component", migration.component);
  setText("#migration-stored", migration.stored_version);
  setText("#migration-current", migration.current_version);
  setText("#migration-required", migration.migration_required == null ? "" : String(migration.migration_required));
  setText("#migration-safe", migration.migration_safe == null ? "" : String(migration.migration_safe));
  setText("#migration-updated", humanTime(migration.updated_at));
  const pill = qs("#migration-pill");
  const warning = migration.migration_required || migration.migration_safe === false;
  pill.textContent = warning ? "attention" : "ready";
  pill.className = `pill ${warning ? "warning" : "success"}`;
}

function renderLedger(entries) {
  const list = normalizeLedger(entries);
  setText("#ledger-count", `${list.length} entr${list.length === 1 ? "y" : "ies"}`);
  const root = qs("#ledger-table");
  root.replaceChildren();
  if (!list.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No ledger entries match these filters.";
    root.append(empty);
    return;
  }
  const table = document.createElement("table");
  const headers = ["Operation", "Task", "Run", "Provider", "Branch", "PR", "Outcome"];
  const thead = document.createElement("thead");
  const headerRow = document.createElement("tr");
  for (const header of headers) {
    const th = document.createElement("th");
    th.textContent = header;
    headerRow.append(th);
  }
  thead.append(headerRow);
  const tbody = document.createElement("tbody");
  for (const entry of list) {
    const tr = document.createElement("tr");
    const operationCell = document.createElement("td");
    const link = document.createElement("a");
    link.className = "table-link";
    const url = new URL(location.href);
    url.searchParams.set("operation_id", entry.operation_id);
    link.href = url.toString();
    link.textContent = entry.operation_id || "—";
    operationCell.append(link);
    tr.append(operationCell);
    for (const value of [
      entry.task_id,
      entry.run_id,
      entry.provider,
      entry.branch,
      entry.pr_number == null ? "" : String(entry.pr_number),
      entry.outcome,
    ]) {
      const td = document.createElement("td");
      td.textContent = value || "—";
      tr.append(td);
    }
    tbody.append(tr);
  }
  table.append(thead, tbody);
  root.append(table);
}

function renderFailures(signatures) {
  const list = normalizeFailures(signatures);
  setText("#failure-count", `${list.length} signature${list.length === 1 ? "" : "s"}`);
  const root = qs("#failure-list");
  root.replaceChildren();
  if (!list.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No failure signatures reported.";
    root.append(empty);
    return;
  }
  for (const signature of list) {
    const card = document.createElement("article");
    card.className = "signature-card";
    const head = document.createElement("div");
    head.className = "signature-head";
    const title = document.createElement("strong");
    title.textContent = `${signature.failure_family || "failure"} · ${signature.failure_code || "unknown"}`;
    const count = document.createElement("span");
    count.className = "pill warning";
    count.textContent = `${signature.occurrence_count} occurrence${signature.occurrence_count === 1 ? "" : "s"}`;
    head.append(title, count);

    const meta = document.createElement("div");
    meta.className = "signature-meta";
    for (const value of [signature.subsystem, signature.current_code_head]) {
      if (value) {
        const span = document.createElement("span");
        span.textContent = value;
        meta.append(span);
      }
    }

    const links = document.createElement("div");
    links.className = "operation-links";
    for (const operationId of signature.affected_operations) {
      const link = document.createElement("a");
      link.className = "operation-link";
      const url = new URL(location.href);
      url.searchParams.set("operation_id", operationId);
      link.href = url.toString();
      link.textContent = operationId;
      links.append(link);
    }
    if (signature.evidence_ref) {
      const evidence = document.createElement("a");
      evidence.className = "operation-link";
      evidence.href = signature.evidence_ref;
      evidence.target = "_blank";
      evidence.rel = "noreferrer";
      evidence.textContent = "evidence";
      links.append(evidence);
    }
    card.append(head, meta, links);
    root.append(card);
  }
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

async function loadLedger(apiBase, filters = {}) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, value);
  }
  return getJson(`${apiBase}/v1/runtime/ledger?${params.toString()}`);
}

async function loadFailures(apiBase) {
  return getJson(`${apiBase}/v1/runtime/failures`);
}

async function loadMigrations(apiBase) {
  return getJson(`${apiBase}/v1/runtime/migrations`);
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
    const [healthPayload, ledgerPayload, failurePayload, migrationPayload] = await Promise.all([
      loadHealth(apiBase),
      loadLedger(apiBase),
      loadFailures(apiBase).catch(() => ({ signatures: [] })),
      loadMigrations(apiBase),
    ]);
    renderHealth(healthPayload.health || {});
    renderLedger(ledgerPayload.entries || []);
    renderFailures(failurePayload.signatures || []);
    renderMigration(migrationPayload || {});

    const operationId = qs("#operation-id").value.trim() ||
      new URLSearchParams(location.search).get("operation_id") || "";
    if (!operationId) {
      const emptyVm = buildViewModel({});
      renderOperation(emptyVm);
      renderAcceptance(emptyVm);
      renderTimeline(emptyVm);
      renderRecovery(emptyVm);
      renderLongRun(emptyVm);
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
    renderRecovery(vm);
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

  qs("#ledger-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const apiBase = document.documentElement.dataset.apiBase || DEFAULT_API_BASE;
    try {
      const payload = await loadLedger(apiBase, {
        task_id: qs("#ledger-task").value.trim(),
        run_id: qs("#ledger-run").value.trim(),
        provider: qs("#ledger-provider").value.trim(),
        outcome: qs("#ledger-outcome").value.trim(),
      });
      renderLedger(payload.entries || []);
    } catch (error) {
      const root = qs("#ledger-table");
      root.replaceChildren();
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = error instanceof Error ? error.message : String(error);
      root.append(empty);
    }
  });

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
