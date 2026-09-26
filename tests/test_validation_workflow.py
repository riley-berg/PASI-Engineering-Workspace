from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestValidationWorkflow(unittest.TestCase):
    def test_full_repository_entrypoint_uses_authoritative_gate(self) -> None:
        script = (ROOT / "scripts" / "test_full_repo.sh").read_text(encoding="utf-8")
        self.assertIn('exec bash "$SCRIPT_DIR/check_all.sh"', script)

    def test_check_all_enforces_zero_quality_diagnostics(self) -> None:
        script = (ROOT / "scripts" / "check_all.sh").read_text(encoding="utf-8")
        for required in (
            "python -W error -m pytest -q",
            "--junitxml=.runtime/pytest.xml",
            '"failures"',
            '"errors"',
            "pyright@1.1.411",
            "--outputjson",
            '"generalDiagnostics"',
            '"error": 0',
            '"warning": 0',
            '"information": 0',
            "ALL AUTHORITATIVE VALIDATION PASSED: 0 errors, 0 warnings, 0 failed tests",
            "git ls-files -z",
        ):
            self.assertIn(required, script)

    def test_github_workflow_runs_full_gate_on_hosted_runner(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-latest", workflow)
        self.assertIn("bash scripts/test_full_repo.sh", workflow)
        self.assertNotIn("self-hosted", workflow)
        self.assertNotIn("check_fast.sh", workflow)


if __name__ == "__main__":
    unittest.main()
