import json
from pathlib import Path


def test_roadmap_schema_is_valid_and_versioned():
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "schemas" / "roadmap-v2.json")
        .read_text(encoding="utf-8")
    )
    assert schema["$id"].endswith("roadmap-v2.json")
    assert schema["properties"]["version"]["const"] == 2
    assert "acceptance_requirements" in str(schema)
