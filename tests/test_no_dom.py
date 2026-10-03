from pathlib import Path
import subprocess


def test_clean_workspace_has_no_legacy_dom_automation_tree():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "automation" / "legacy").exists()
    tracked = subprocess.run(
        ["git", "ls-files", "--", "automation/chromium/pasi-chatgpt"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked.returncode == 0
    assert not tracked.stdout.strip()
