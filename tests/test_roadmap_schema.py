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
    with pytest.raises(RoadmapError, match="canonical schema version 4"):
        Roadmap(
            roadmap_id="invalid-v3-object",
            version=3,
            revision=0,
            phases=(),
            tasks=(),
        )


@pytest.mark.parametrize("version", [1, 5])
def test_unsupported_roadmap_input_versions_are_rejected(version):
    with pytest.raises(RoadmapError, match="unsupported roadmap schema version"):
        Roadmap.from_mapping(legacy_payload(version))

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_schema(version: int) -> dict:
    return json.loads(
        (ROOT / "schemas" / f"roadmap-v{version}.json").read_text(encoding="utf-8")
    )


def test_legacy_schema_required_fields_match_current_compatibility_defaults():
    for version in (2, 3):
        schema = load_schema(version)
        assert schema["required"] == ["roadmap_id", "version", "phases", "tasks"]
        assert schema["properties"]["phases"]["items"]["required"] == ["id", "title"]
        assert schema["properties"]["tasks"]["items"]["required"] == [
            "id",
            "title",
            "phase_id",
            "acceptance_requirements",
            "evidence_requirements",
        ]


def test_legacy_schemas_retain_legacy_properties_without_dead_schema_sections():
    for version in (2, 3):
        schema = load_schema(version)
        phase_properties = schema["properties"]["phases"]["items"]["properties"]
        task_properties = schema["properties"]["tasks"]["items"]["properties"]

        assert "canonical_sha256" in schema["properties"]
        assert "revision" in schema["properties"]
        for name in ("depends_on", "status", "revision"):
            assert name in phase_properties
            assert name in task_properties

        if version == 2:
            assert "parent_task_id" not in task_properties
        else:
            assert "parent_task_id" in task_properties

        assert "definitions" not in schema
        assert "$defs" not in schema
        assert "examples" not in schema


@pytest.mark.parametrize("version", [2, 3])
def test_legacy_payload_without_defaulted_fields_still_loads(version):
    payload = legacy_payload(version)
    payload.pop("revision")
    payload.pop("canonical_sha256", None)
    payload["phases"][0].pop("depends_on")
    payload["phases"][0].pop("status")
    payload["phases"][0].pop("revision")
    payload["tasks"][0].pop("depends_on")
    payload["tasks"][0].pop("status")
    payload["tasks"][0].pop("revision")
    if version == 3:
        payload["tasks"][0].pop("parent_task_id")

    roadmap = Roadmap.from_mapping(payload)

    assert roadmap.version == 4
    assert roadmap.revision == 0
    assert roadmap.phase("P2").depends_on == ()
    assert roadmap.phase("P2").status.value == "planned"
    assert roadmap.task("P2.1").depends_on == ()
    assert roadmap.task("P2.1").status.value == "planned"
    assert roadmap.canonical_sha256
