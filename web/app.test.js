import { test } from "node:test";
import assert from "node:assert/strict";
import { buildViewModel, healthTone, normalizeFailures, normalizeLedger, normalizeNotifications, normalizeRecoveryDecision } from "./app.js";
import { normalizePlannerMemory, normalizeRoadmapSummary, plannerTaskStatusClass } from "./planner.js";

test("health tone maps authoritative runtime states", () => {
  assert.equal(healthTone("connected"), "success");
  assert.equal(healthTone("degraded"), "warning");
  assert.equal(healthTone("recovering"), "warning");
  assert.equal(healthTone("disconnected"), "danger");
});

test("view model preserves operation identity, evidence, events, and long-run metadata", () => {
  const vm = buildViewModel({
    operation: {
      operation_id: "op-1",
      task_id: "P0.5",
      metadata: {
        acceptance_m1: "PASS",
        long_run_sequence: "17",
        long_run_target: "168",
      },
    },
    health: { connection_status: "connected" },
    runtime: { code_head: "abc" },
    lineage: [{ operation_id: "op-1" }],
    evidence_refs: ["evidence://1"],
    events: [
      {
        sequence: 1,
        event_type: "operation.queued",
        source: "runner",
        occurred_at: "2026-01-01T00:00:00Z",
        payload: { status: "queued" },
      },
    ],
  });
  assert.equal(vm.operation.operation_id, "op-1");
  assert.deepEqual(vm.evidence, ["evidence://1"]);
  assert.equal(vm.events.length, 1);
  assert.equal(vm.milestones.m1, "PASS");
  assert.equal(vm.longRun.sequence, 17);
  assert.equal(vm.longRun.target, 168);
});

test("missing acceptance metadata stays explicitly unavailable", () => {
  const vm = buildViewModel({ operation: {}, events: [] });
  assert.equal(vm.milestones.m0, "not reported");
  assert.equal(vm.longRun.available, false);
});


test("recovery console distinguishes authoritative classifications and keeps missing decisions unavailable", () => {
  const vm = buildViewModel({
    operation: { operation_id: "op-recover" },
    events: [
      {
        event_type: "recovery.decision",
        payload: {
          classification: "recoverable",
          reason_code: "controller_connection_lost",
          action: "reconnect_same_operation",
          max_attempts: 1,
          attempts: 1,
          preserve_operation_identity: true,
          evidence_refs: ["evidence://recover"],
          planner_handoff: "return_to_runner",
        },
      },
    ],
  });
  const decision = normalizeRecoveryDecision(vm);
  assert.equal(decision.available, true);
  assert.equal(decision.classification, "recoverable");
  assert.equal(decision.preserve_operation_identity, true);
  assert.equal(decision.planner_handoff, "return_to_runner");

  const empty = normalizeRecoveryDecision(buildViewModel({ operation: {}, events: [] }));
  assert.equal(empty.available, false);
  assert.equal(empty.classification, "not reported");
});

test("ledger and failure normalizers preserve provenance and affected-operation links", () => {
  assert.deepEqual(
    normalizeLedger([
      {
        operation_id: "op-1",
        task_id: "P1.2",
        run_id: "run-1",
        provider: "local",
        branch: "pasi/main",
        pr_number: 22,
        outcome: "failed",
      },
    ]),
    [
      {
        operation_id: "op-1",
        task_id: "P1.2",
        run_id: "run-1",
        provider: "local",
        branch: "pasi/main",
        pr_number: 22,
        outcome: "failed",
        parent_operation_id: "",
        created_at: "",
      },
    ],
  );

  const failures = normalizeFailures([
    {
      signature_id: "sig-1",
      subsystem: "controller",
      failure_family: "connection",
      failure_code: "connection_lost",
      occurrence_count: 3,
      affected_operations: ["op-1", "op-2"],
      evidence_ref: "evidence://failure-1",
      current_code_head: "abc123",
    },
  ]);
  assert.deepEqual(failures[0].affected_operations, ["op-1", "op-2"]);
  assert.equal(failures[0].current_code_head, "abc123");
});


test("notification normalizer keeps durable identity and acknowledgement revision", () => {
  const items = normalizeNotifications([
    {
      notification_id: "ntf-1",
      scope: "runtime",
      source_event_id: "event-1",
      severity: "warning",
      message: "Attention",
      entity_type: "operation",
      entity_id: "op-1",
      occurred_at: "2026-01-01T00:00:00Z",
      acknowledged: false,
      revision: 2,
    },
  ]);
  assert.equal(items[0].notification_id, "ntf-1");
  assert.equal(items[0].revision, 2);
  assert.equal(items[0].acknowledged, false);
});


test("planner view models preserve roadmap authority and memory provenance", () => {
  const vm = normalizeRoadmapSummary({
    roadmap: {
      roadmap_id: "pasi-frontend",
      version: 4,
      revision: 3,
      canonical_sha256: "abc",
      phases: [{ id: "FE-P2", status: "active" }],
      tasks: [{ id: "FE-P2.1", title: "Roadmap manager", phase_id: "FE-P2", status: "active" }],
    },
    ready_task_ids: [],
    blocked_reasons: { "FE-P2.2": "dependency FE-P2.1 is not completed" },
  });
  assert.equal(vm.roadmap.roadmap_id, "pasi-frontend");
  assert.equal(vm.roadmap.revision, 3);
  assert.equal(vm.readyTaskIds.length, 0);
  assert.equal(vm.blockedReasons["FE-P2.2"], "dependency FE-P2.1 is not completed");
  assert.equal(plannerTaskStatusClass("active"), "active");

  const memories = normalizePlannerMemory([
    {
      memory_id: "mem-1",
      scope: "task:FE-P2.1",
      kind: "decision",
      content: "Dependency graph is authoritative.",
      provenance_refs: ["evidence://1"],
      confidence: 0.9,
      status: "active",
      revision: 2,
    },
  ]);
  assert.equal(memories[0].memory_id, "mem-1");
  assert.deepEqual(memories[0].provenance_refs, ["evidence://1"]);
});
