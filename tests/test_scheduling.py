from pathlib import Path

import pytest

from pasi.core.schedule_store import SQLiteScheduleStore
from pasi.core.scheduling import (
    CostAwareScheduler,
    ResourceEstimate,
    ScheduleError,
    SchedulerCapacity,
)
from pasi.core.roadmap import PhaseStatus, Roadmap, RoadmapPhase, RoadmapTask, TaskStatus


def make_roadmap() -> Roadmap:
    return Roadmap(
        roadmap_id="pasi-main",
        version=4,
        revision=0,
        phases=(RoadmapPhase("P2", "Planner", status=PhaseStatus.ACTIVE),),
        tasks=(
            RoadmapTask(
                "P2.1", "A", "P2",
                acceptance_requirements=("accept",),
                evidence_requirements=("evidence",),
                status=TaskStatus.PLANNED,
            ),
            RoadmapTask(
                "P2.2", "B", "P2",
                acceptance_requirements=("accept",),
                evidence_requirements=("evidence",),
                status=TaskStatus.PLANNED,
            ),
            RoadmapTask(
                "P2.3", "C", "P2",
                acceptance_requirements=("accept",),
                evidence_requirements=("evidence",),
                status=TaskStatus.PLANNED,
            ),
        ),
    )


def test_scheduler_only_considers_authoritative_ready_tasks():
    roadmap = make_roadmap()
    capacity = SchedulerCapacity(2000, 4096, 1000)
    estimates = {
        task_id: ResourceEstimate(500, 512, 100)
        for task_id in ("P2.1", "P2.2", "P2.3")
    }
    decision = CostAwareScheduler().schedule(
        roadmap,
        estimates=estimates,
        capacity=capacity,
        advisory_priorities={"P2.2": 100.0, "P2.1": 1.0, "P2.3": 2.0},
    )
    assert [item.task_id for item in decision.scheduled] == ["P2.2", "P2.3", "P2.1"]


def test_scheduler_enforces_capacity_and_deterministic_tie_break():
    roadmap = make_roadmap()
    capacity = SchedulerCapacity(1000, 4096, 500)
    estimates = {
        task_id: ResourceEstimate(600, 512, 100)
        for task_id in ("P2.1", "P2.2", "P2.3")
    }
    decision = CostAwareScheduler().schedule(
        roadmap,
        estimates=estimates,
        capacity=capacity,
        advisory_priorities={"P2.1": 1.0, "P2.2": 1.0, "P2.3": 1.0},
    )
    assert [item.task_id for item in decision.scheduled] == ["P2.1"]
    assert decision.excluded["P2.2"] == "capacity exceeded"
    assert decision.excluded["P2.3"] == "capacity exceeded"


def test_scheduler_rejects_invalid_inputs():
    with pytest.raises(ScheduleError):
        ResourceEstimate(0, 512, 10)

    with pytest.raises(ScheduleError):
        SchedulerCapacity(1000, 4096, float("nan"))

    roadmap = make_roadmap()
    decision = CostAwareScheduler().schedule(
        roadmap,
        estimates={
            "P2.1": ResourceEstimate(500, 512, 100),
            "P2.2": ResourceEstimate(500, 512, 100),
            "P2.3": ResourceEstimate(500, 512, 100),
        },
        capacity=SchedulerCapacity(2000, 4096, 1000),
        advisory_priorities={"P2.1": float("nan"), "P2.2": 1.0},
    )
    assert decision.excluded["P2.1"] == "invalid advisory priority"


def test_schedule_store_round_trip_and_revision_binding(tmp_path: Path):
    roadmap = make_roadmap()
    decision = CostAwareScheduler().schedule(
        roadmap,
        estimates={
            "P2.1": ResourceEstimate(500, 512, 100),
            "P2.2": ResourceEstimate(500, 512, 100),
            "P2.3": ResourceEstimate(500, 512, 100),
        },
        capacity=SchedulerCapacity(2000, 4096, 1000),
    )
    store = SQLiteScheduleStore(tmp_path / "schedule.db")
    assert store.record(decision) == 1
    assert store.latest("pasi-main", 0) == decision
