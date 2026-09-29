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


def test_recover_acceptance_worktree_cleans_stale_startup_state(monkeypatch, tmp_path):
    calls = []
    dirty = True

    def fake_git(cwd: Path, *args: str, **kwargs):
        nonlocal dirty
        calls.append(args)
        if args[:2] == ("status", "--porcelain"):
            if dirty:
                return " M stale.py\n?? stale-artifact"
            return ""
        if args[:2] == ("clean", "-fd"):
            dirty = False
        return ""

    monkeypatch.setattr(acceptance, "git", fake_git)
    monkeypatch.setattr(acceptance, "emit", lambda event: calls.append(("emit", event["event"])))

    acceptance.recover_acceptance_worktree(tmp_path, reason="ensure_worktree_start")

    assert ("reset", "--hard", "HEAD") in calls
    assert ("clean", "-fd") in calls
    assert ("emit", "acceptance_worktree_recovered") in calls


def test_recover_task_worktree_cleans_stale_acceptance_state(monkeypatch, tmp_path):
    from types import SimpleNamespace

    calls = []

    def fake_git(cwd: Path, *args: str, **kwargs):
        calls.append(args)
        if args[:2] == ("status", "--porcelain"):
            if not any(call[:2] == ("clean", "-fd") for call in calls):
                return " M stale.py\n?? stale-artifact"
        return ""

    monkeypatch.setattr(acceptance, "git", fake_git)
    monkeypatch.setattr(acceptance, "emit", lambda event: calls.append(("emit", event["event"])))

    acceptance.recover_task_worktree(tmp_path, SimpleNamespace(task_id="P0.4"))

    assert ("reset", "--hard", "HEAD") in calls
    assert ("clean", "-fd") in calls
    assert any(call[:2] == ("status", "--porcelain") for call in calls)
    assert any(call[0] == "emit" for call in calls)


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
    monkeypatch.setattr(chat, "AUDIT_KEY_DIR", tmp_path / "keys")
    monkeypatch.setattr(chat, "AUDIT_PRIVATE_KEY_PATH", tmp_path / "keys" / "private.pem")
    monkeypatch.setattr(chat, "AUDIT_PUBLIC_KEY_PATH", tmp_path / "keys" / "public.pem")

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
    assert decision["chain_index"] == 1
    assert decision["previous_record_hash"] == chat.NEW_CHAT_CHAIN_GENESIS
    assert state["new_chat_decision_chain_head"] == decision["record_hash"]
    assert decision["signature_algorithm"] == "Ed25519"
    assert decision["signature_key_id"] == state["new_chat_decision_signature_key_id"]
    assert decision["record_signature"]
    assert (tmp_path / "keys" / "private.pem").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "keys" / "public.pem").exists()
    replay = chat.replay_new_chat_decisions(
        tmp_path / "new-chat-decisions.jsonl",
        tmp_path / "chat-session.json",
        tmp_path / "keys" / "public.pem",
    )
    assert replay["valid"] is True


def test_replay_validation_accepts_and_replays_durable_decision_record(tmp_path):
    import base64
    import json
    from datetime import datetime, timezone
    from scripts import pasi_chat as chat
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    chat_path=tmp_path / "new-chat-decisions.jsonl"
    public_path=tmp_path / "public.pem"
    private=Ed25519PrivateKey.generate()
    public_path.write_bytes(private.public_key().public_bytes(Encoding.PEM,PublicFormat.SubjectPublicKeyInfo))

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
        "chain_index":1,
        "previous_record_hash":chat.NEW_CHAT_CHAIN_GENESIS,
        "signature_algorithm":"Ed25519",
        "signature_key_id":chat._public_key_id(private.public_key()),
    }
    record["record_hash"]=chat._record_hash(record)
    record["record_signature"]=base64.b64encode(private.sign(record["record_hash"].encode("ascii"))).decode("ascii")
    chat_path.write_text(json.dumps(record)+"\n",encoding="utf-8")

    result=chat.replay_new_chat_decisions(chat_path, public_key_path=public_path)
    assert result["valid"] is True
    assert result["records"] == 1
    assert result["errors"] == []
    assert result["chain_head"] == record["record_hash"]


def _write_two_chat_decision_records(chat, path, state_path, public_path, private):
    from datetime import datetime, timezone
    import base64
    import json

    captured=datetime.now(timezone.utc).isoformat()
    first={
        "decision_id":"decision-1",
        "decision":"create_new_chat",
        "reason":"first",
        "recorded_at":datetime.now(timezone.utc).isoformat(),
        "observed_conversation_url":"https://chatgpt.com/c/current",
        "conversation_signature":"sig-1",
        "freshness_window_seconds":{"min":-5.0,"max":30.0},
        "observed_age_seconds":1.0,
        "source_operation_id":"op-1",
        "exhaustion_evidence":{
            "kind":"chatgpt_state",
            "chat_url":"https://chatgpt.com/c/current",
            "conversation_signature":"sig-1",
            "captured_at":captured,
            "active_operation_id":"op-1",
            "conversation_context_exhausted":True,
            "chat_exhausted":True,
            "normalized_chat_exhausted":True,
        },
        "chain_index":1,
        "previous_record_hash":chat.NEW_CHAT_CHAIN_GENESIS,
        "signature_algorithm":"Ed25519",
        "signature_key_id":chat._public_key_id(private.public_key()),
    }
    first["record_hash"]=chat._record_hash(first)
    first["record_signature"]=base64.b64encode(private.sign(first["record_hash"].encode("ascii"))).decode("ascii")
    second={**first}
    second.update({
        "decision_id":"decision-2",
        "reason":"second",
        "conversation_signature":"sig-2",
        "source_operation_id":"op-2",
        "chain_index":2,
        "previous_record_hash":first["record_hash"],
        "exhaustion_evidence":{
            **first["exhaustion_evidence"],
            "conversation_signature":"sig-2",
            "active_operation_id":"op-2",
        },
    })
    second["record_hash"]=chat._record_hash(second)
    second["record_signature"]=base64.b64encode(private.sign(second["record_hash"].encode("ascii"))).decode("ascii")
    path.write_text(
        json.dumps(first,sort_keys=True,separators=(",",":"))+"\n"+
        json.dumps(second,sort_keys=True,separators=(",",":"))+"\n",
        encoding="utf-8",
    )
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    public_path.write_bytes(private.public_key().public_bytes(
        Encoding.PEM,
        PublicFormat.SubjectPublicKeyInfo,
    ))
    state_path.write_text(json.dumps({
        "new_chat_decision_count":2,
        "new_chat_decision_chain_head":second["record_hash"],
        "new_chat_decision_signature_key_id":chat._public_key_id(private.public_key()),
    })+"\n",encoding="utf-8")
    return first,second


def test_replay_detects_deleted_middle_audit_entry(tmp_path):
    import json
    from scripts import pasi_chat as chat
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    path=tmp_path / "new-chat-decisions.jsonl"
    state=tmp_path / "chat-session.json"
    public_path=tmp_path / "public.pem"
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    private=Ed25519PrivateKey.generate()
    first,second=_write_two_chat_decision_records(chat,path,state,public_path,private)
    path.write_text(json.dumps(second)+"\n",encoding="utf-8")

    result=chat.replay_new_chat_decisions(path,state,public_path)
    assert result["valid"] is False
    assert any("chain_index" in error for error in result["errors"])
    assert any("previous_record_hash" in error for error in result["errors"])
    assert any("chain anchor count" in error for error in result["errors"])


def test_replay_detects_reordered_audit_entries(tmp_path):
    import json
    from scripts import pasi_chat as chat
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    path=tmp_path / "new-chat-decisions.jsonl"
    state=tmp_path / "chat-session.json"
    public_path=tmp_path / "public.pem"
    private=Ed25519PrivateKey.generate()
    first,second=_write_two_chat_decision_records(chat,path,state,public_path,private)
    path.write_text(json.dumps(second)+"\n"+json.dumps(first)+"\n",encoding="utf-8")

    result=chat.replay_new_chat_decisions(path,state,public_path)
    assert result["valid"] is False
    assert any("chain_index" in error for error in result["errors"])
    assert any("previous_record_hash" in error for error in result["errors"])
    assert any("record_hash" in error or "chain anchor head" in error for error in result["errors"])


def test_replay_detects_inserted_audit_entry_against_persisted_tip(tmp_path):
    import json
    from scripts import pasi_chat as chat
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    path=tmp_path / "new-chat-decisions.jsonl"
    state=tmp_path / "chat-session.json"
    public_path=tmp_path / "public.pem"
    private=Ed25519PrivateKey.generate()
    first,second=_write_two_chat_decision_records(chat,path,state,public_path,private)
    inserted={**second}
    inserted.update({
        "decision_id":"decision-inserted",
        "chain_index":3,
        "previous_record_hash":second["record_hash"],
        "reason":"unexpected insertion",
    })
    inserted["record_hash"]=chat._record_hash(inserted)
    inserted["record_signature"]=__import__("base64").b64encode(private.sign(inserted["record_hash"].encode("ascii"))).decode("ascii")
    path.write_text(
        json.dumps(first,sort_keys=True,separators=(",",":"))+"\n"+
        json.dumps(second,sort_keys=True,separators=(",",":"))+"\n"+
        json.dumps(inserted,sort_keys=True,separators=(",",":"))+"\n",
        encoding="utf-8",
    )

    result=chat.replay_new_chat_decisions(path,state,public_path)
    assert result["valid"] is False
    assert any("chain anchor count" in error for error in result["errors"])
    assert any("chain anchor head" in error for error in result["errors"])


def test_replay_validation_rejects_tampered_signature(tmp_path):
    import json
    from datetime import datetime, timezone
    from scripts import pasi_chat as chat
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    private=Ed25519PrivateKey.generate()
    public_path=tmp_path / "public.pem"
    public_path.write_bytes(private.public_key().public_bytes(Encoding.PEM,PublicFormat.SubjectPublicKeyInfo))

    captured=datetime.now(timezone.utc).isoformat()
    record={
        "decision_id":"decision-tampered",
        "decision":"create_new_chat",
        "recorded_at":captured,
        "observed_conversation_url":"https://chatgpt.com/c/current",
        "conversation_signature":"sig-current",
        "freshness_window_seconds":{"min":-5.0,"max":30.0},
        "observed_age_seconds":1.0,
        "exhaustion_evidence":{
            "kind":"chatgpt_state",
            "chat_url":"https://chatgpt.com/c/current",
            "conversation_signature":"sig-current",
            "captured_at":captured,
            "conversation_context_exhausted":True,
            "chat_exhausted":True,
            "normalized_chat_exhausted":True,
        },
        "chain_index":1,
        "previous_record_hash":chat.NEW_CHAT_CHAIN_GENESIS,
        "signature_algorithm":"Ed25519",
        "signature_key_id":chat._public_key_id(private.public_key()),
    }
    record["record_hash"]=chat._record_hash(record)
    record["record_signature"]="invalid"
    path=tmp_path / "new-chat-decisions.jsonl"
    path.write_text(json.dumps(record)+"\n",encoding="utf-8")

    result=chat.replay_new_chat_decisions(path,public_key_path=public_path)
    assert result["valid"] is False
    assert any("record_signature is invalid" in error for error in result["errors"])


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
    assert "--public-key" in source
    assert "REPO_ROOT=Path(__file__).resolve().parents[1]" in source
    assert "sys.path.insert(0,str(REPO_ROOT))" in source
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
    assert 'elif [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then' in launcher
    assert '"$PYTHON_BIN" scripts/pasi_168h_acceptance.py' in launcher
    assert 'PASI_ENGINEERING_EXECUTOR_CMD="${PASI_ENGINEERING_EXECUTOR_CMD:-$PYTHON_BIN scripts/pasi_engineering_executor.py}"' in launcher
    assert '"$PYTHON_BIN" -m venv "$REPO_ROOT/.venv"' in launcher
    assert 'if ! "$PYTHON_BIN" -c \'import cryptography\'' in launcher
    assert 'PASI_ENGINEERING_RUNTIME_DIR="${PASI_ENGINEERING_RUNTIME_DIR:-$RUNTIME_DIR/runtime}"' in launcher
    assert 'PASI_NEW_CHAT_AUDIT_KEY_DIR="${PASI_NEW_CHAT_AUDIT_KEY_DIR:-$RUNTIME_DIR/keys}"' in launcher
    assert 'PASI_168H_SMOKE="${PASI_168H_SMOKE:-0}"' in launcher
    assert 'resolved_python="$(command -v "$PYTHON_BIN" || true)"' in launcher
    assert 'scripts/pasi_168h_acceptance.py --hours 168 --smoke' in launcher


def test_168h_runner_replays_and_persists_new_chat_audit():
    result = {
        "path": "/tmp/new-chat-decisions.jsonl",
        "public_key": "/tmp/public.pem",
        "records": 3,
        "valid": True,
        "errors": [],
        "chain_head": "abc123",
    }
    state = {}
    original = acceptance.new_chat_audit
    try:
        acceptance.new_chat_audit = lambda: result
        replayed = acceptance.record_new_chat_audit(state)
    finally:
        acceptance.new_chat_audit = original
    assert replayed == result
    assert state["new_chat_audit_valid"] is True
    assert state["new_chat_audit_record_count"] == 3
    assert state["new_chat_audit_chain_head"] == "abc123"
    assert state["new_chat_audit_log"] == "/tmp/new-chat-decisions.jsonl"
    assert state["new_chat_audit_public_key"] == "/tmp/public.pem"
    assert state["new_chat_audit_errors"] == []


def test_168h_runner_fails_closed_on_invalid_new_chat_audit():
    result = {
        "path": "/tmp/new-chat-decisions.jsonl",
        "public_key": "/tmp/public.pem",
        "records": 1,
        "valid": False,
        "errors": ["line 1: record_signature is invalid"],
        "chain_head": "bad",
    }
    state = {}
    original = acceptance.new_chat_audit
    try:
        acceptance.new_chat_audit = lambda: result
        try:
            acceptance.record_new_chat_audit(state)
        except RuntimeError as exc:
            assert "new-chat audit chain is invalid" in str(exc)
        else:
            raise AssertionError("invalid new-chat audit chain was accepted")
    finally:
        acceptance.new_chat_audit = original
    assert state["new_chat_audit_valid"] is False
    assert state["new_chat_audit_record_count"] == 1
    assert "record_signature is invalid" in state["new_chat_audit_errors"][0]


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
def test_ensure_worktree_skips_fetch_for_same_branch(monkeypatch, tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".git").mkdir()

    calls = []

    def fake_git(cwd: Path, *args: str, **kwargs):
        calls.append(args)
        if args[:2] == ("branch", "--show-current"):
            return "same-branch"
        if args[:2] == ("status", "--porcelain"):
            return ""
        return ""

    monkeypatch.setattr(acceptance, "git", fake_git)
    acceptance.ensure_worktree(tmp_path, worktree, "same-branch")

    assert ("fetch", "origin", "main") not in calls


def test_hint_task_uses_durable_current_task(monkeypatch):
    from scripts import pasi_168h_acceptance as acceptance

    phase = acceptance.Phase("P0", 108, "Q3-2026", "2026-09-22", "2026-10-04")
    monkeypatch.setattr(acceptance, "schedule", lambda: (phase,))
    monkeypatch.setattr(
        acceptance,
        "tasks_for",
        lambda current: (acceptance.Task(current, "P0.8", "Security dependency review", False),),
    )

    task = acceptance.hinted_task({"current_task": "P0.8"})

    assert task is not None
    assert task.task_id == "P0.8"


def test_p0_6_can_be_preverified_from_durable_workflow_policy(monkeypatch, tmp_path):
    from scripts import pasi_168h_acceptance as acceptance

    phase = acceptance.Phase("P0", 108, "Q3-2026", "2026-09-22", "2026-10-04")
    task = acceptance.Task(phase, "P0.6", "CI/workflow consolidation", False)

    monkeypatch.setattr(
        acceptance,
        "schedule",
        lambda: (phase,),
    )
    monkeypatch.setattr(
        acceptance,
        "git",
        lambda cwd, *args, **kwargs: "abc123" if args == ("rev-parse", "HEAD") else "",
    )
    monkeypatch.setattr(
        "scripts.verify_workflow_consolidation.verify",
        lambda root: {"valid": True, "workflow_count": 4, "errors": []},
    )

    evidence = acceptance.preverified_task(task, tmp_path)

    assert evidence is not None
    assert evidence["task_id"] == "P0.6"
    assert evidence["commit"] == "abc123"


def test_runner_bounds_and_surfaces_task_retries():
    source = Path(__file__).with_name("pasi_168h_acceptance.py").read_text(encoding="utf-8")
    assert "MAX_TASK_ATTEMPTS" in source
    assert "task_bounded_retry_exhausted" in source
    assert "moving to the next eligible task" in source
    assert "retrying {task.task_id} in" in source
