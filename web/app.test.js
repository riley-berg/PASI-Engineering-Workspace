import { test } from "node:test";
import assert from "node:assert/strict";
import { buildViewModel, healthTone } from "./app.js";

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
