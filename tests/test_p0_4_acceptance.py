from pathlib import Path
from types import SimpleNamespace

from scripts import pasi_168h_acceptance as acceptance


def test_ensure_worktree_creates_missing_branch(monkeypatch, tmp_path):
    root = tmp_path / "root"
    worktree = tmp_path / "worktree"
    root.mkdir()
    worktree.mkdir()
    (worktree / ".git").mkdir()

    calls = []

    def fake_git(cwd: Path, *args: str, **kwargs):
        calls.append(args)
        if args[:2] == ("branch", "--show-current"):
            return "existing-branch"
        if args[:2] == ("status", "--porcelain"):
            return ""
        return ""

    monkeypatch.setattr(acceptance, "git", fake_git)

    def fake_run(cmd, **kwargs):
        assert cmd[:3] == ["git", "show-ref", "--verify"]
        return SimpleNamespace(returncode=1, stdout="", stderr="missing branch")

    monkeypatch.setattr(acceptance.subprocess, "run", fake_run)

    branch = "pasi/p0-4-test-missing-branch"
    acceptance.ensure_worktree(root, worktree, branch)

    assert ("checkout", "--detach", "origin/main") in calls
    assert ("checkout", "-b", branch) in calls
    assert ("checkout", branch) not in calls


def test_runner_uses_canonical_handoff_path_and_immediate_task_handoff():
    chat = Path(acceptance.__file__).with_name("pasi_chat.py").read_text(encoding="utf-8")
    runner = Path(acceptance.__file__).read_text(encoding="utf-8")
    assert '"extensions"/"pasi-chatgpt"' in chat
    assert '"automation"/"chromium"/"pasi-chatgpt"' not in chat
    assert 'event": "next_task_ready"' in runner
    assert 'next_task_id' in runner
    assert 'remaining = [next_task for next_task in all_tasks() if not next_task.checked]' in runner


def test_chat_prompt_is_task_specific_and_no_scheduled_delay():
    chat = Path(acceptance.__file__).with_name("pasi_chat.py").read_text(encoding="utf-8")
    assert 'def prompt(task:str,phase:str,task_id:str,issue:str)' in chat
    assert 'Task ID: {task_id}' in chat
    assert 'time.sleep(3600' not in chat
