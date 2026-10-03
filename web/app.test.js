import assert from "node:assert/strict";
import test from "node:test";
test("planner status and memory normalizers preserve authoritative state", async () => {
  const { normalizeRoadmapSummary, normalizePlannerMemory, plannerTaskStatusClass } = await import("./planner.js");
  const summary = normalizeRoadmapSummary({
    roadmap: {
      roadmap_id: "pasi-frontend",
      version: 4,
      revision: 7,
      canonical_sha256: "digest",
      phases: [],
      tasks: [],
    },
    ready_task_ids: ["FE-P2.1"],
    blocked_reasons: {"FE-P2.2": "waiting_on:FE-P2.1"},
  });
  assert.equal(summary.roadmap.revision, 7);
  assert.deepEqual(summary.readyTaskIds, ["FE-P2.1"]);
  assert.equal(summary.blockedReasons["FE-P2.2"], "waiting_on:FE-P2.1");
  assert.equal(plannerTaskStatusClass("completed"), "completed");

  const memory = normalizePlannerMemory([{
    memory_id: "learn-1",
    scope: "project:pasi",
    kind: "learned",
    content: "Verified.",
    provenance_refs: ["evidence://1"],
    confidence: 0.9,
    status: "active",
    revision: 3,
  }]);
  assert.equal(memory[0].kind, "learned");
  assert.deepEqual(memory[0].provenance_refs, ["evidence://1"]);
});
