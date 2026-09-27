import json
from pathlib import Path


def test_p2_visible_backend_schema_is_versioned():
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "schemas" / "p2-visible-backend-v1.json")
        .read_text(encoding="utf-8")
    )
    assert schema["$id"].endswith("p2-visible-backend-v1.json")
    assert set(schema["required"]) == {
        "dependency_graph",
        "task_detail",
        "ranking_explanation",
        "workspace_preference",
        "search_result",
        "project_sync_state",
        "notification",
    }
