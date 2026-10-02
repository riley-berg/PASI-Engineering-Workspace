import pytest

from pasi.core.roadmap import (
    ROADMAP_SCHEMA_VERSION,
    Roadmap,
    RoadmapError,
)


def legacy_payload(version: int) -> dict:
    task = {
        "id": "P2.1",
        "title": "Legacy roadmap task",
        "phase_id": "P2",
        "depends_on": [],
        "acceptance_requirements": ["acceptance"],
        "evidence_requirements": ["evidence"],
        "status": "planned",
        "revision": 0,
    }
    if version >= 3:
        task["parent_task_id"] = ""
    return {
        "roadmap_id": "legacy-roadmap",
        "version": version,
        "revision": 0,
        "phases": [
            {
                "id": "P2",
                "title": "Planner",
                "depends_on": [],
                "status": "active",
                "revision": 0,
            }
        ],
        "tasks": [task],
    }


@pytest.mark.parametrize("version", [2, 3])
def test_legacy_roadmap_inputs_are_normalized_to_canonical_v4(version):
    roadmap = Roadmap.from_mapping(legacy_payload(version))

    assert roadmap.version == ROADMAP_SCHEMA_VERSION == 4
    assert roadmap.task("P2.1").parent_task_id == ""


def test_canonical_roadmap_objects_cannot_retain_legacy_schema_versions():
    with pytest.raises(RoadmapError, match="canonical schema version 4"):
        Roadmap(
            roadmap_id="invalid-v2-object",
            version=2,
            revision=0,
            phases=(),
            tasks=(),
        )


@pytest.mark.parametrize("version", [1, 5])
def test_unsupported_roadmap_input_versions_are_rejected(version):
    with pytest.raises(RoadmapError, match="unsupported roadmap schema version"):
        Roadmap.from_mapping(legacy_payload(version))
