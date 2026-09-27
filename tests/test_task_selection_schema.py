import json
from pathlib import Path


def test_task_selection_schema_is_versioned():
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "schemas" / "task-selection-v1.json")
        .read_text(encoding="utf-8")
    )
    assert schema["$id"].endswith("task-selection-v1.json")
    assert "roadmap_revision" in schema["required"]
