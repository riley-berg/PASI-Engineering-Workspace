from pathlib import Path

from scripts import pasi_engineering_executor as executor
def test_complete_contract_parser():
    response="""PASI_RESULT_STATUS: complete
PASI_RESULT_SUMMARY: implemented and verified the change
PASI_RESULT_REQUIREMENTS: complete
PASI_RESULT_LIMITATIONS: none
PASI_RESULT_RESEARCH: not_applicable
PASI_RESULT_UX: not_applicable
PASI_RESULT_BACKEND: verified
PASI_RESULT_EVIDENCE: pytest and frontend tests passed with clean repository evidence
PASI_RESULT_REPOSITORY_PROGRESS: changed
PASI_RESULT_ALLOW_DELETE: false
PASI_RESULT_PATCH_BEGIN
diff --git a/example.txt b/example.txt
new file mode 100644
PASI_RESULT_PATCH_END
"""
    status,values,patch=executor.parse(response)
    values["status"]=status
    executor.validate(values)
    assert "diff --git" in patch


def test_executor_uses_canonical_chat_guard():
    source = Path(executor.__file__).read_text(encoding="utf-8")
    assert "pasi_chat_guard.py" in source
    assert "pasi_engineering_chat_guard.py" not in source


def test_executor_runs_chat_guard_as_workspace_module():
    source = Path(executor.__file__).read_text(encoding="utf-8")
    assert '"-m","scripts.pasi_chat_guard"' in source
