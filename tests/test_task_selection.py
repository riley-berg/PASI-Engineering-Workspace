import pytest

from pasi.core.roadmap import PhaseStatus, Roadmap, RoadmapPhase, RoadmapTask, TaskStatus
from pasi.core.selection_store import SQLiteSelectionStore
from pasi.core.task_selection import EvidenceAwareTaskSelector, EvidenceSnapshot, StaleSelection


def make_roadmap() -> Roadmap:
    return Roadmap(
        roadmap_id="pasi-main",
        version=4,
        revision=0,
        phases=(RoadmapPhase("P2", "Planner", status=PhaseStatus.ACTIVE),),
        tasks=(
            RoadmapTask("P2.1", "First task", "P2",
                        acceptance_requirements=("accept",),
                        evidence_requirements=("evidence",),
                        status=TaskStatus.PLANNED),
            RoadmapTask("P2.2", "Dependent task", "P2", depends_on=("P2.1",),
                        acceptance_requirements=("accept-2",),
                        evidence_requirements=("evidence-2",),
                        status=TaskStatus.PLANNED),
            RoadmapTask("P2.3", "Independent task", "P2",
                        acceptance_requirements=("accept-3",),
                        evidence_requirements=("evidence-3",),
                        status=TaskStatus.PLANNED),
        ),
    )


def test_selector_never_promotes_blocked_tasks():
    roadmap = make_roadmap()
    decision = EvidenceAwareTaskSelector().select(
        roadmap,
        evidence={},
        advisory_scores={"P2.2": 9999.0, "P2.3": 1.0, "P2.1": 2.0},
    )
    assert decision.selected_task_id == "P2.1"
    assert "P2.2" not in decision.eligible_task_ids


def test_selector_requires_current_verified_dependency_evidence():
    roadmap = make_roadmap()
    active = roadmap.transition_task("P2.1", TaskStatus.ACTIVE, expected_revision=0)
    completed = active.transition_task(
        "P2.1",
        TaskStatus.COMPLETED,
        expected_revision=1,
        evidence_refs=("evidence://P2.1",),
    )
    decision = EvidenceAwareTaskSelector().select(
        completed,
        evidence={
            "P2.1": EvidenceSnapshot(
                task_id="P2.1",
                roadmap_revision=completed.revision,
                status="verified",
                refs=("evidence://P2.1",),
            )
        },
        advisory_scores={"P2.2": 10.0, "P2.3": 1.0},
    )
    assert decision.selected_task_id == "P2.2"

    stale = EvidenceAwareTaskSelector().select(
        completed,
        evidence={
            "P2.1": EvidenceSnapshot(
                task_id="P2.1",
                roadmap_revision=0,
                status="verified",
                refs=("evidence://P2.1",),
            )
        },
        advisory_scores={"P2.2": 10.0},
    )
    assert stale.selected_task_id == "P2.3"
    assert "stale evidence" in stale.excluded_reasons["P2.2"]


def test_selector_rejects_invalid_scores_and_tiebreaks_deterministically():
    roadmap = make_roadmap()
    decision = EvidenceAwareTaskSelector().select(
        roadmap,
        evidence={},
        advisory_scores={"P2.1": float("nan"), "P2.3": 1.0, "P2.2": 100.0},
    )
    assert decision.selected_task_id == "P2.3"
    assert decision.excluded_reasons["P2.1"] == "invalid advisory score"


def test_selection_is_bound_to_roadmap_revision_and_persists(tmp_path):
    roadmap = make_roadmap()
    decision = EvidenceAwareTaskSelector().select(roadmap, evidence={})
    changed = roadmap.transition_task("P2.1", TaskStatus.ACTIVE, expected_revision=0)

    with pytest.raises(StaleSelection):
        decision.assert_fresh(changed)

    store = SQLiteSelectionStore(tmp_path / "selection.db")
    record_id = store.record(decision)
    assert record_id == 1
    assert store.latest("pasi-main", 0) == decision
