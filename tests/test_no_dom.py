from pathlib import Path


def test_clean_workspace_has_no_legacy_dom_automation_tree():
    root = Path(__file__).resolve().parents[1]
    forbidden = [
        root / "automation" / "chromium",
        root / "automation" / "legacy",
    ]
    assert all(not path.exists() for path in forbidden)
