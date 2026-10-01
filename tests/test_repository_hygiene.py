from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositoryHygieneTests(unittest.TestCase):
    def run_audit(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "pasi_repository_hygiene.py"), *args],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def test_hygiene_audit_defaults_to_non_destructive_mode(self) -> None:
        result = self.run_audit("--json")
        self.assertIn('"strict": false', result.stdout)
        self.assertTrue((ROOT / "extensions" / "pasi-chatgpt" / "manifest.json").is_file())

    def test_canonical_cdp_controller_contains_no_legacy_dom_authority(self) -> None:
        source = (ROOT / "extensions" / "pasi-chatgpt" / "src" / "cdp-network-controller.js").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("MutationObserver", source)
        self.assertNotIn("Runtime.evaluate", source)
        self.assertNotIn("document.querySelector(", source)
        self.assertNotIn("chrome.scripting.executeScript", source)


if __name__ == "__main__":
    unittest.main()
