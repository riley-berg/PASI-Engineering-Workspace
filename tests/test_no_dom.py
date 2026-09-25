from pathlib import Path

def test_clean_workspace_has_no_dom_or_frontend_tree():
    root = Path(__file__).resolve().parents[1]
    forbidden = [
        root / "automation" / "chromium",
        root / "automation" / "legacy",
        root / "web",
        root / "frontend",
    ]
    assert all(not path.exists() for path in forbidden)
