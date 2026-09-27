import json
from pathlib import Path


def test_durable_event_schema_is_valid_and_versioned():
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "schemas" / "durable-event-v1.json")
        .read_text(encoding="utf-8")
    )
    assert schema["$id"].endswith("durable-event-v1.json")
    assert schema["properties"]["schema_version"]["const"] == 1
    assert "payload_sha256" in schema["required"]
