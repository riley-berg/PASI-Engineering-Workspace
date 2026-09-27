import json
from pathlib import Path


def test_roadmap_v4_schema_preserves_issue_source_metadata():
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "schemas" / "roadmap-v4.json")
        .read_text(encoding="utf-8")
    )
    assert schema["properties"]["version"]["const"] == 4
    task_schema = schema["properties"]["tasks"]["items"]["properties"]
    assert task_schema["source_issue_number"]["type"] == ["integer", "null"]
    assert "source_url" in task_schema
    assert "source_title" in task_schema
