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


def test_chat_prompt_contains_valid_literal_capability_json():
    from scripts import pasi_chat as chat

    rendered = chat.prompt("Task", "P0", "P0.4", "108")
    assert '{"request_id":"read-1"' in rendered
    assert '"parameters":{"query":"relevant_symbol_or_text","limit":10}' in rendered


def test_chat_reuses_usable_live_conversation_after_connection_interruption():
    from scripts import pasi_chat as chat

    live = {
        "chat_url": "https://chatgpt.com/c/current",
        "chat_exhausted": False,
        "usage_limited": False,
        "connection_failure": True,
    }
    assert chat.select_chat_mode({}, live) == "reuse"
    assert chat.select_chat_mode({}, {
        "chat_url": None,
        "chat_exhausted": False,
        "usage_limited": False,
        "connection_failure": True,
    }) == "recover"


def test_chat_only_creates_replacement_for_context_exhaustion():
    from scripts import pasi_chat as chat

    # A raw/stale exhaustion flag is never sufficient to authorize a new chat.
    assert chat.select_chat_mode({}, {
        "chat_url": "https://chatgpt.com/c/current",
        "chat_exhausted": True,
        "usage_limited": False,
    }) == "reuse"
    assert chat.select_chat_mode({}, {
        "chat_url": "https://chatgpt.com/c/current",
        "chat_exhausted": True,
        "chat_exhaustion_confirmed": True,
        "usage_limited": False,
    }) == "new_chat"
    assert chat.select_chat_mode({}, {
        "chat_url": None,
        "chat_exhausted": False,
        "usage_limited": False,
    }) == "recover"
    assert chat.select_chat_mode({}, {
        "chat_url": "https://chatgpt.com/c/current",
        "chat_exhausted": False,
        "usage_limited": True,
    }) == "blocked"


def test_exhaustion_proof_requires_fresh_current_chat_state():
    from datetime import datetime, timezone

    from scripts import pasi_chat as chat

    class FakeAdapter:
        def __init__(self, observation):
            self.observation = observation

        def read_browser_observation(self):
            return self.observation

    fresh = datetime.now(timezone.utc).isoformat()
    observation = {
        "captured_at": fresh,
        "data": {
            "kind": "chatgpt_state",
            "chat_url": "https://chatgpt.com/c/current",
            "conversation_context_exhausted": True,
            "chat_exhausted": True,
            "conversation_signature": "sig-current-42",
            "active_operation_id": "op-42",
        },
    }
    proof = chat.confirm_current_chat_exhaustion(
        FakeAdapter(observation),
        expected_chat_url="https://chatgpt.com/c/current",
        expected_operation_id="op-42",
        timeout=0.5,
    )
    assert proof is not None
    assert proof["chat_exhaustion_confirmed"] is True
    assert proof["chat_url"] == "https://chatgpt.com/c/current"
    assert proof["conversation_signature"] == "sig-current-42"
    assert proof["freshness_window_seconds"] == {"min": -5.0, "max": 30.0}
    assert proof["observed_age_seconds"] is not None
    assert proof["exhaustion_evidence"] == {
        "kind": "chatgpt_state",
        "chat_url": "https://chatgpt.com/c/current",
        "conversation_signature": "sig-current-42",
        "captured_at": fresh,
        "active_operation_id": "op-42",
        "conversation_context_exhausted": True,
        "chat_exhausted": True,
        "normalized_chat_exhausted": True,
    }


def test_new_chat_decision_is_durable_and_contains_authorizing_proof(tmp_path, monkeypatch):
    from scripts import pasi_chat as chat
    from datetime import datetime, timezone

    monkeypatch.setattr(chat, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(chat, "STATE_PATH", tmp_path / "chat-session.json")
    monkeypatch.setattr(chat, "NEW_CHAT_DECISIONS_PATH", tmp_path / "new-chat-decisions.jsonl")

    captured = datetime.now(timezone.utc).isoformat()
    proof = {
        "chat_exhaustion_confirmed": True,
        "chat_url": "https://chatgpt.com/c/current",
        "conversation_signature": "sig-current-42",
        "freshness_window_seconds": {"min": -5.0, "max": 30.0},
        "observed_age_seconds": 1.25,
        "exhaustion_evidence": {
            "kind": "chatgpt_state",
            "chat_url": "https://chatgpt.com/c/current",
            "conversation_signature": "sig-current-42",
            "captured_at": captured,
            "active_operation_id": "op-42",
            "conversation_context_exhausted": True,
            "chat_exhausted": True,
            "normalized_chat_exhausted": True,
        },
    }

    decision = chat.record_new_chat_decision(
        proof,
        reason="current_chat_exhausted_after_response_without_valid_contract",
        source_operation_id="op-42",
    )

    records = (tmp_path / "new-chat-decisions.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(records) == 1
    record = __import__("json").loads(records[0])
    assert record == decision
    assert record["decision"] == "create_new_chat"
    assert record["observed_conversation_url"] == "https://chatgpt.com/c/current"
    assert record["conversation_signature"] == "sig-current-42"
    assert record["freshness_window_seconds"] == {"min": -5.0, "max": 30.0}
    assert record["exhaustion_evidence"]["captured_at"] == captured
    assert record["exhaustion_evidence"]["conversation_context_exhausted"] is True
    assert record["exhaustion_evidence"]["chat_exhausted"] is True
    assert record["source_operation_id"] == "op-42"

    state = __import__("json").loads((tmp_path / "chat-session.json").read_text(encoding="utf-8"))
    assert state["last_new_chat_decision"] == decision
    assert state["new_chat_decision_count"] == 1


def test_replay_validation_accepts_and_replays_durable_decision_record(tmp_path):
    import json
    from scripts import pasi_chat as chat
    from datetime import datetime, timezone

    chat_path=tmp_path / "new-chat-decisions.jsonl"
    captured=datetime.now(timezone.utc).isoformat()
    record={
        "decision_id":"decision-1",
        "decision":"create_new_chat",
        "reason":"current_chat_exhausted_after_response_without_valid_contract",
        "recorded_at":datetime.now(timezone.utc).isoformat(),
        "observed_conversation_url":"https://chatgpt.com/c/current",
        "conversation_signature":"sig-current-42",
        "freshness_window_seconds":{"min":-5.0,"max":30.0},
        "observed_age_seconds":1.5,
        "source_operation_id":"op-42",
        "exhaustion_evidence":{
            "kind":"chatgpt_state",
            "chat_url":"https://chatgpt.com/c/current",
            "conversation_signature":"sig-current-42",
            "captured_at":captured,
            "active_operation_id":"op-42",
            "conversation_context_exhausted":True,
            "chat_exhausted":True,
            "normalized_chat_exhausted":True,
        },
    }
    chat_path.write_text(json.dumps(record)+"\n",encoding="utf-8")

    result=chat.replay_new_chat_decisions(chat_path)
    assert result["valid"] is True
    assert result["records"] == 1
    assert result["errors"] == []


def test_replay_validation_rejects_tampered_conversation_identity(tmp_path):
    import json
    from scripts import pasi_chat as chat
    from datetime import datetime, timezone

    captured=datetime.now(timezone.utc).isoformat()
    record={
        "decision":"create_new_chat",
        "recorded_at":captured,
        "observed_conversation_url":"https://chatgpt.com/c/current",
        "conversation_signature":"sig-top",
        "freshness_window_seconds":{"min":-5.0,"max":30.0},
        "observed_age_seconds":1.0,
        "exhaustion_evidence":{
            "kind":"chatgpt_state",
            "chat_url":"https://chatgpt.com/c/current",
            "conversation_signature":"sig-evidence",
            "captured_at":captured,
            "conversation_context_exhausted":True,
            "chat_exhausted":True,
            "normalized_chat_exhausted":True,
        },
    }
    path=tmp_path / "new-chat-decisions.jsonl"
    path.write_text(json.dumps(record)+"\n",encoding="utf-8")

    result=chat.replay_new_chat_decisions(path)
    assert result["valid"] is False
    assert any("evidence conversation_signature does not match" in error for error in result["errors"])


def test_replay_validation_rejects_false_exhaustion_and_bad_freshness(tmp_path):
    import json
    from scripts import pasi_chat as chat
    from datetime import datetime, timezone

    captured=datetime.now(timezone.utc).isoformat()
    record={
        "decision":"create_new_chat",
        "recorded_at":captured,
        "observed_conversation_url":"https://chatgpt.com/c/current",
        "conversation_signature":"sig-current",
        "freshness_window_seconds":{"min":-5.0,"max":30.0},
        "observed_age_seconds":45.0,
        "exhaustion_evidence":{
            "kind":"chatgpt_state",
            "chat_url":"https://chatgpt.com/c/current",
            "conversation_signature":"sig-current",
            "captured_at":captured,
            "conversation_context_exhausted":False,
            "chat_exhausted":True,
            "normalized_chat_exhausted":False,
        },
    }
    path=tmp_path / "new-chat-decisions.jsonl"
    path.write_text(json.dumps(record)+"\n",encoding="utf-8")

    result=chat.replay_new_chat_decisions(path)
    assert result["valid"] is False
    assert any("observed_age_seconds falls outside" in error for error in result["errors"])
    assert any("conversation_context_exhausted evidence is not true" in error for error in result["errors"])
    assert any("normalized_chat_exhausted evidence is not true" in error for error in result["errors"])


def test_replay_validation_rejects_malformed_json_line(tmp_path):
    from scripts import pasi_chat as chat

    path=tmp_path / "new-chat-decisions.jsonl"
    path.write_text("{not-json}\n",encoding="utf-8")

    result=chat.replay_new_chat_decisions(path)
    assert result["valid"] is False
    assert result["records"] == 1
    assert "invalid JSON" in result["errors"][0]


def test_replay_validator_cli_is_repository_tool():
    source=Path(__file__).parents[1].joinpath("scripts","replay_new_chat_decisions.py").read_text(encoding="utf-8")
    assert "replay_new_chat_decisions" in source
    assert 'return 0 if result["valid"] else 1' in source


def test_new_chat_requires_durable_exhaustion_proof():
    from scripts import pasi_chat as chat

    try:
        chat.record_new_chat_decision({
            "chat_exhaustion_confirmed": False,
        }, reason="invalid", source_operation_id=None)
    except ValueError as exc:
        assert "confirmed current-conversation exhaustion proof" in str(exc)
    else:
        raise AssertionError("new-chat decision without proof was accepted")


def test_exhaustion_false_positive_stale_observation_is_rejected():
    from datetime import datetime, timedelta, timezone

    from scripts import pasi_chat as chat

    class FakeAdapter:
        def read_browser_observation(self):
            return {
                "captured_at": (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat(),
                "data": {
                    "kind": "chatgpt_state",
                    "chat_url": "https://chatgpt.com/c/current",
                    "conversation_context_exhausted": True,
                    "chat_exhausted": True,
                    "conversation_signature": "sig-stale",
                },
            }

    assert chat.confirm_current_chat_exhaustion(
        FakeAdapter(),
        expected_chat_url="https://chatgpt.com/c/current",
        timeout=0.35,
    ) is None


def test_exhaustion_false_positive_wrong_conversation_is_rejected():
    from datetime import datetime, timezone

    from scripts import pasi_chat as chat

    class FakeAdapter:
        def read_browser_observation(self):
            return {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "data": {
                    "kind": "chatgpt_state",
                    "chat_url": "https://chatgpt.com/c/other",
                    "conversation_context_exhausted": True,
                    "chat_exhausted": True,
                    "conversation_signature": "sig-other",
                },
            }

    assert chat.confirm_current_chat_exhaustion(
        FakeAdapter(),
        expected_chat_url="https://chatgpt.com/c/current",
        timeout=0.35,
    ) is None


def test_exhaustion_false_positive_missing_signature_is_rejected():
    from datetime import datetime, timezone

    from scripts import pasi_chat as chat

    class FakeAdapter:
        def read_browser_observation(self):
            return {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "data": {
                    "kind": "chatgpt_state",
                    "chat_url": "https://chatgpt.com/c/current",
                    "conversation_context_exhausted": True,
                    "chat_exhausted": True,
                },
            }

    assert chat.confirm_current_chat_exhaustion(
        FakeAdapter(),
        expected_chat_url="https://chatgpt.com/c/current",
        timeout=0.35,
    ) is None


def test_exhaustion_false_positive_generic_error_flag_is_rejected():
    from datetime import datetime, timezone

    from scripts import pasi_chat as chat

    class FakeAdapter:
        def read_browser_observation(self):
            return {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "data": {
                    "kind": "chatgpt_health",
                    "chat_url": "https://chatgpt.com/c/current",
                    "conversation_context_exhausted": True,
                    "chat_exhausted": True,
                    "conversation_signature": "sig-health",
                },
            }

    assert chat.confirm_current_chat_exhaustion(
        FakeAdapter(),
        expected_chat_url="https://chatgpt.com/c/current",
        timeout=0.35,
    ) is None


def test_interrupted_or_timeout_response_keeps_active_operation():
    from scripts import pasi_chat as chat

    assert chat.should_clear_active_operation(SimpleNamespace(completion="complete")) is True
    assert chat.should_clear_active_operation(SimpleNamespace(completion="interrupted")) is False
    assert chat.should_clear_active_operation(SimpleNamespace(completion="error")) is False
    assert chat.should_clear_active_operation(SimpleNamespace(completion="timeout")) is False


def test_active_operation_is_recovered_before_new_chat_selection():
    from scripts import pasi_chat as chat

    source = Path(chat.__file__).read_text(encoding="utf-8")
    assert "active=state.get(\"active_operation_id\")" in source
    assert "response=wait_existing_operation(op)" in source
    assert "active_task_id=state.get(\"active_task_id\")" in source
    assert "refusing to submit a second prompt" in source
    assert 'state["active_operation_id"]=None' in source
    assert "should_clear_active_operation(response)" in source


def test_p0_4_recovery_wait_does_not_cancel_the_existing_operation():
    from scripts import pasi_chat as chat

    source = Path(chat.__file__).read_text(encoding="utf-8")
    assert "cancel_on_timeout=False" in source


def test_new_chat_creation_preserves_its_recovery_operation():
    from scripts import pasi_chat as chat

    adapter_source = Path(chat.__file__).parents[1].joinpath("automation","computer_use","chatgpt.py").read_text(encoding="utf-8")
    assert "cancel_on_timeout=False" in adapter_source
    assert "self.current_operation_id" in adapter_source


def test_chat_session_identity_is_persisted_for_idempotent_restart():
    from scripts import pasi_chat as chat

    source = Path(chat.__file__).read_text(encoding="utf-8")
    assert 'STATE_PATH=RUNTIME_DIR/"chat-session.json"' in source
    assert 'session_id=state.get("session_id")' in source
    assert 'session_id=session_id' in source


def test_task_history_context_tracks_previous_committed_action():
    from scripts import pasi_168h_acceptance as acceptance

    rendered = acceptance.task_history_context({
        "recent_tasks": [{
            "task_id": "P1.1",
            "phase": "P1",
            "title": "Unified operation state",
            "commit": "abcdef1234567890",
        }]
    })
    assert "P1.1: Unified operation state" in rendered
    assert "commit abcdef123456" in rendered
    assert "latest committed action" in rendered


def test_runner_uses_configurable_tested_source_ref_for_new_worktrees():
    runner = Path(acceptance.__file__).read_text(encoding="utf-8")
    assert 'source_ref = os.environ.get("PASI_168H_SOURCE_REF", "HEAD")' in runner
    assert '"worktree", "add", "-B", branch, str(worktree), source_ref' in runner


def test_long_run_launcher_uses_repository_virtualenv_python():
    launcher = Path(acceptance.__file__).with_name("run_p0_4_168h.sh").read_text(encoding="utf-8")
    assert 'PYTHON_BIN="${PASI_PYTHON:-$REPO_ROOT/.venv/bin/python}"' in launcher
    assert '"$PYTHON_BIN" scripts/pasi_168h_acceptance.py' in launcher
    assert 'PASI_ENGINEERING_EXECUTOR_CMD="${PASI_ENGINEERING_EXECUTOR_CMD:-$PYTHON_BIN scripts/pasi_engineering_executor.py}"' in launcher


def test_p0_4_acceptance_is_run_level_gate_not_model_task():
    runner = Path(acceptance.__file__).read_text(encoding="utf-8")
    assert 'task.task_id != "P0.4"' in runner
    assert 'p0_4_status' in runner
    assert 'mark_checked(p0_4)' in runner
    assert 'def ensure_evidence_pr' in runner


def test_p0_4_runner_recovers_branch_and_worktree_from_durable_state():
    runner = Path(acceptance.__file__).read_text(encoding="utf-8")
    assert 'existing.get("branch")' in runner
    assert 'existing.get("worktree")' in runner
    assert 'existing.get("run_id")' in runner


def test_chat_recovers_terminal_empty_context_exhaustion():
    from scripts import pasi_chat as chat

    source = Path(chat.__file__).read_text(encoding="utf-8")
    assert 'exhaustion_candidate=response.chat_exhausted' in source
    assert 'confirm_current_chat_exhaustion(' in source
    assert 'exhausted_without_contract=bool(' in source


def test_p0_4_branch_selection_uses_cli_branch_on_new_run():
    runner = Path(acceptance.__file__).read_text(encoding="utf-8")
    assert 'branch = str(args.branch or existing.get("branch")' in runner


def test_executor_feeds_failures_back_into_bounded_repair_prompt():
    runner = Path(acceptance.__file__).with_name("pasi_engineering_executor.py").read_text(encoding="utf-8")
    assert 'MAX_MODEL_REPAIR_ATTEMPTS=int(os.environ.get("PASI_MODEL_REPAIR_ATTEMPTS","4"))' in runner
    assert 'PREVIOUS EXECUTION FEEDBACK:' in runner
    assert 'cleanup_failed_attempt(root)' in runner
    assert 'Completion-contract or patch validation failed:' in runner
    assert 'Patch application failed.' in runner
    assert 'Python verification failed after applying the patch.' in runner
    assert 'Frontend verification failed after applying the patch.' in runner


def test_executor_repair_prompt_requires_resolution_not_explanation():
    from scripts import pasi_engineering_executor as executor

    task = "Acceptance evidence registry"
    feedback = "missing/duplicate markers: summary, evidence"
    rendered = executor.repair_feedback(task, feedback, 2)
    assert "CURRENT" not in rendered
    assert "PREVIOUS EXECUTION FEEDBACK:" in rendered
    assert feedback in rendered
    assert "Resolve the reported failure in the next attempt" in rendered
    assert "do not merely explain it" in rendered


def test_p0_4_task_discovery_cache_avoids_repeated_github_reads(monkeypatch):
    bodies = {
        108: "- [ ] **P0.1 — M0 live task acceptance** — prove M0\n",
        109: "- [ ] **P1.1 — Unified operation state** — prove P1 state\n",
    }
    phases = (
        acceptance.Phase("P0", 108, "Q3-2026", "2026-09-22", "2026-10-04"),
        acceptance.Phase("P1", 109, "Q4-2026", "2026-10-04", "2026-10-12"),
    )
    calls = []
    monkeypatch.setattr(acceptance, "schedule", lambda: phases)
    def fake_github(url, *, method="GET", body=None):
        calls.append((url, method))
        if method == "PATCH":
            return {}
        issue = int(url.rstrip("/").split("/")[-1])
        return {"body": bodies[issue]}
    monkeypatch.setattr(acceptance, "github", fake_github)
    monkeypatch.setattr(acceptance, "_TASK_CACHE", acceptance.TaskDiscoveryCache())

    first = acceptance.all_tasks()
    second = acceptance.all_tasks()

    assert first == second
    assert len([item for item in calls if item[1] == "GET"]) == 2

    acceptance.mark_checked(first[0])
    third = acceptance.all_tasks()
    assert any(task.task_id == "P0.1" and task.checked for task in third)
    assert len([item for item in calls if item[1] == "GET"]) == 3


def test_p0_4_cached_evidence_pr_avoids_repeat_lookup(tmp_path, monkeypatch):
    monkeypatch.setattr(acceptance, "state_dir", lambda: tmp_path)
    (tmp_path / "state.json").write_text(
        '{"evidence_pr":{"number":142,"url":"https://github.com/th3-st0v3/PASI-Engineering-Workspace/pull/142"}}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(acceptance, "token", lambda: "test-token")
    monkeypatch.setattr(
        acceptance,
        "github",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("GitHub lookup should be skipped")),
    )

    assert acceptance.ensure_evidence_pr("pasi/test", "run-1")["number"] == 142


def test_p0_4_commit_reconciliation_uses_argv_not_shell():
    source = Path(acceptance.__file__).read_text(encoding="utf-8")
    assert "subprocess.getstatusoutput(" not in source
    executor = Path(acceptance.__file__).with_name("pasi_engineering_executor.py").read_text(encoding="utf-8")
    assert "from pathlib import Path\nimport subprocess" not in executor
    assert "subprocess" in executor


def test_extension_background_has_one_side_panel_initializer():
    background = Path(acceptance.__file__).parents[1] / "extensions" / "pasi-chatgpt" / "src" / "background.js"
    source = background.read_text(encoding="utf-8")
    assert source.count("setPanelBehavior({ openPanelOnActionClick: true })") == 1


def test_executor_prompt_context_excludes_completed_roadmap_tasks():
    from scripts import pasi_engineering_executor as executor

    context = executor.unfinished_issue_context(
        """- [x] **P0.1 — M0 live task acceptance** — completed
- [x] **P0.2 — M1 twenty-operation chain** — completed
- [ ] **P0.4 — 168-hour long-run acceptance** — still open
- [ ] **P0.5 — Acceptance evidence registry** — still open
Completion rule
Only unfinished roadmap work should be presented."""
    )
    assert "P0.1" not in context
    assert "P0.2" not in context
    assert "P0.4" in context
    assert "P0.5" in context
    assert "Completion rule" in context


def test_executor_canonical_context_uses_unfinished_task_filter():
    source = Path(__file__).parents[1].joinpath("scripts", "pasi_engineering_executor.py").read_text(encoding="utf-8")
    assert 'return unfinished_issue_context(str(body or ""))[:30000]' in source
    assert 'match.group(1).lower()=="x"' in source
