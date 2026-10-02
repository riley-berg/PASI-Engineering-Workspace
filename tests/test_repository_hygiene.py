from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class RepositoryHygieneTests(unittest.TestCase):
    def run_audit(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "pasi_repository_hygiene.py"), *args], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)

    def test_hygiene_audit_defaults_to_non_destructive_mode(self) -> None:
        result = self.run_audit("--json")
        self.assertIn('"strict": false', result.stdout)
        self.assertTrue((ROOT / "extensions" / "pasi-chatgpt" / "manifest.json").is_file())

    def test_repository_has_no_embedded_git_repositories_or_submodules(self) -> None:
        payload = json.loads(self.run_audit("--json").stdout)
        self.assertEqual(payload["checks"]["repository_boundaries"], "passed")
        self.assertFalse((ROOT / ".gitmodules").exists())

    def test_nested_git_metadata_is_detected_even_when_untracked(self) -> None:
        from scripts.pasi_repository_hygiene import nested_git_metadata
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / ".git").mkdir()
            nested_dir = root / "nested-dir"; nested_dir.mkdir(); (nested_dir / ".git").mkdir()
            nested_file = root / "nested-worktree"; nested_file.mkdir()
            (nested_file / ".git").write_text("gitdir: ../.git/worktrees/nested\n", encoding="utf-8")
            findings = nested_git_metadata(root)
        self.assertEqual({path.relative_to(root) for path in findings}, {Path("nested-dir/.git"), Path("nested-worktree/.git")})

    def test_canonical_cdp_controller_contains_no_legacy_dom_authority(self) -> None:
        source = (ROOT / "extensions" / "pasi-chatgpt" / "src" / "cdp-network-controller.js").read_text(encoding="utf-8")
        self.assertNotIn("MutationObserver", source); self.assertNotIn("Runtime.evaluate", source); self.assertNotIn("document.querySelector(", source); self.assertNotIn("chrome.scripting.executeScript", source)

    def test_retired_extension_paths_and_script_level_tests_are_absent(self) -> None:
        tracked = {str(path) for path in ROOT.rglob("*") if path.is_file() and ".git" not in path.parts}
        self.assertNotIn("extensions/pasi-chatgpt/src/protocol.js", tracked)
        self.assertNotIn("extensions/pasi-chatgpt/src/copy-api.js", tracked)
        self.assertFalse(any(path.startswith("scripts/test_") and path.endswith(".py") for path in tracked))
        self.assertTrue((ROOT / "tests" / "test_cleanup_duplicate_branches.py").is_file()); self.assertTrue((ROOT / "tests" / "test_github_project_v2.py").is_file()); self.assertTrue((ROOT / "tests" / "test_workflow_contracts.py").is_file())

if __name__ == "__main__": unittest.main()
