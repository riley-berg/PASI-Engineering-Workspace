import json

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_operation_state_schema_is_valid_json_and_versioned():
    schema = json.loads(
        (ROOT / "schemas" / "operation-state-v1.json").read_text(encoding="utf-8")
    )
    assert schema["$id"].endswith("operation-state-v1.json")
    assert schema["properties"]["schema_version"]["const"] == 1
    assert "state_revision" in schema["required"]
