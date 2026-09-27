import json
from pathlib import Path


def test_168h_schedule_is_engineering_workspace():
    payload = json.loads((Path(__file__).parents[1] / "roadmap" / "p0-p22-168h.json").read_text())
    assert payload["source_repository"] == "th3-st0v3/PASI-Engineering-Workspace"
    assert payload["duration_hours"] == 168
    assert len(payload["phases"]) == 23
    assert payload["phases"][0]["issue"] == 108
    assert payload["phases"][-1]["issue"] == 32
