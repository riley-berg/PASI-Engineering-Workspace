import json
from pathlib import Path

import pytest

from pasi.core.child_tasks import BoundedChildTaskGenerator, ChildTaskGenerationError
from pasi.core.roadmap import PhaseStatus, Roadmap, RoadmapPhase, RoadmapTask


class FakeModel:
    def __init__(self, payload: str):
        self.payload = payload

    def complete(self, prompt: str) -> str:
        return self.payload


def make_parent() -> Roadmap:
    return Roadmap(
        roadmap_id="pasi-main",
        version=4,
        revision=0,
        phases=(RoadmapPhase("P2", "Planner", status=PhaseStatus.ACTIVE),),
        tasks=(
            RoadmapTask(
                "P2.1",
                "Roadmap lifecycle manager",
                "P2",
                acceptance_requirements=("must have real integration proof",),
                evidence_requirements=("durable acceptance evidence",),
            ),
        ),
    )


def test_valid_child_generation_preserves_parent_contract():
    roadmap = make_parent()
    model = FakeModel(
        json.dumps(
            {
                "children": [
                    {
                        "id": "P2.1.1",
                        "title": "Build dependency validator",
                        "depends_on": ["P2.1"],
                        "acceptance_requirements": [
                            "must have real integration proof",
                            "validator rejects missing dependencies",
                        ],
                        "evidence_requirements": [
                            "durable acceptance evidence",
                            "dependency validation evidence",
                        ],
                    }
                ]
            }
        )
    )

    children = BoundedChildTaskGenerator(model).generate(
        roadmap,
        parent_task_id="P2.1",
    )
    assert len(children) == 1
    assert children[0].parent_task_id == "P2.1"
    assert children[0].phase_id == "P2"


@pytest.mark.parametrize(
    "payload",
    [
        {"children": []},
        {"children": [{"id": "P2.1.1", "title": "bad", "depends_on": ["missing"]}]},
        {
            "children": [
                {
                    "id": "other.1",
                    "title": "out of namespace",
                    "acceptance_requirements": ["must have real integration proof"],
                    "evidence_requirements": ["durable acceptance evidence"],
                }
            ]
        },
        {
            "children": [
                {
                    "id": "P2.1.1",
                    "title": "weak",
                    "acceptance_requirements": ["weakened"],
                    "evidence_requirements": ["durable acceptance evidence"],
                }
            ]
        },
    ],
)
def test_invalid_child_generation_is_rejected(payload):
    roadmap = make_parent()
    with pytest.raises(ChildTaskGenerationError):
        BoundedChildTaskGenerator(FakeModel(json.dumps(payload))).generate(
            roadmap,
            parent_task_id="P2.1",
        )


def test_child_count_is_bounded():
    roadmap = make_parent()
    payload = {
        "children": [
            {
                "id": f"P2.1.{index}",
                "title": f"child {index}",
                "acceptance_requirements": ["must have real integration proof"],
                "evidence_requirements": ["durable acceptance evidence"],
            }
            for index in range(1, 7)
        ]
    }
    with pytest.raises(ChildTaskGenerationError, match="maximum"):
        BoundedChildTaskGenerator(FakeModel(json.dumps(payload))).generate(
            roadmap,
            parent_task_id="P2.1",
        )


def test_roadmap_v3_schema_has_parent_task_id():
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "schemas" / "roadmap-v3.json")
        .read_text(encoding="utf-8")
    )
    assert schema["properties"]["version"]["const"] == 3
    assert "parent_task_id" in str(schema)
