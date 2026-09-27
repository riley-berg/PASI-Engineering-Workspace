import pytest

from pasi.core.roadmap import (
    LifecycleTransitionError,
    PhaseStatus,
    Roadmap,
    RoadmapError,
    RoadmapTask,
    RoadmapPhase,
    StaleRoadmapRevision,
    TaskStatus,
)
from pasi.core.roadmap_store import SQLiteRoadmapStore


def make_roadmap() -> Roadmap:
    return Roadmap(
        roadmap_id="pasi-main",
        version=2,
        revision=0,
        phases=(
            RoadmapPhase(
                id="P2",
                title="Planner",
                status=PhaseStatus.ACTIVE,
            ),
        ),
        tasks=(
            RoadmapTask(
                id="P2.1",
                title="Roadmap lifecycle manager",
                phase_id="P2",
                acceptance_requirements=("must validate dependencies",),
                evidence_requirements=("durable test evidence",),
            ),
            RoadmapTask(
                id="P2.2",
                title="Child tasks",
                phase_id="P2",
                depends_on=("P2.1",),
                acceptance_requirements=("must preserve parent identity",),
                evidence_requirements=("parent/child evidence",),
            ),
        ),
    )


def test_ready_and_blocked_tasks_are_deterministic():
    roadmap = make_roadmap()
    assert [task.id for task in roadmap.ready_tasks()] == ["P2.1"]
    assert [task.id for task in roadmap.blocked_tasks()] == ["P2.2"]

    activated = roadmap.transition_task(
        "P2.1",
        TaskStatus.ACTIVE,
        expected_revision=0,
    )
    completed = activated.transition_task(
        "P2.1",
        TaskStatus.COMPLETED,
        expected_revision=1,
        evidence_refs=("evidence://P2.1",),
    )
    assert [task.id for task in completed.ready_tasks()] == ["P2.2"]


def test_completion_requires_evidence_and_eligibility():
    roadmap = make_roadmap()

    with pytest.raises(LifecycleTransitionError):
        roadmap.transition_task("P2.2", TaskStatus.ACTIVE, expected_revision=0)

    active = roadmap.transition_task("P2.1", TaskStatus.ACTIVE, expected_revision=0)
    with pytest.raises(LifecycleTransitionError):
        active.transition_task(
            "P2.1",
            TaskStatus.COMPLETED,
            expected_revision=1,
            evidence_refs=(),
        )


def test_stale_revision_and_dependency_cycle_are_rejected():
    roadmap = make_roadmap()
    with pytest.raises(StaleRoadmapRevision):
        roadmap.transition_task("P2.1", TaskStatus.ACTIVE, expected_revision=3)

    with pytest.raises(RoadmapError, match="task dependency cycle"):
        Roadmap(
            roadmap_id="cycle",
            version=2,
            revision=0,
            phases=(RoadmapPhase("P", "Phase", status=PhaseStatus.ACTIVE),),
            tasks=(
                RoadmapTask(
                    "A", "A", "P", depends_on=("B",),
                    acceptance_requirements=("a",), evidence_requirements=("e",)
                ),
                RoadmapTask(
                    "B", "B", "P", depends_on=("A",),
                    acceptance_requirements=("b",), evidence_requirements=("e",)
                ),
            ),
        )


def test_missing_dependency_is_rejected():
    with pytest.raises(RoadmapError, match="missing tasks"):
        Roadmap(
            roadmap_id="missing",
            version=2,
            revision=0,
            phases=(RoadmapPhase("P", "Phase"),),
            tasks=(
                RoadmapTask(
                    "A", "A", "P", depends_on=("missing",),
                    acceptance_requirements=("a",), evidence_requirements=("e",)
                ),
            ),
        )


def test_roadmap_persists_across_restart_and_rejects_stale_writes(tmp_path):
    path = tmp_path / "roadmap.db"
    store = SQLiteRoadmapStore(path)
    original = make_roadmap()
    store.create(original)

    active = original.transition_task(
        "P2.1",
        TaskStatus.ACTIVE,
        expected_revision=0,
    )
    store.save(active, expected_revision=0)

    restarted = SQLiteRoadmapStore(path)
    assert restarted.get("pasi-main") == active

    with pytest.raises(StaleRoadmapRevision):
        restarted.save(active, expected_revision=0)

    assert restarted.get("pasi-main").canonical_sha256 == active.canonical_sha256
