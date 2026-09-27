from pathlib import Path
import importlib.util


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_m1_live_acceptance.py"


def load_harness():
    spec = importlib.util.spec_from_file_location("pasi_m1_live_acceptance", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m1_harness_compiles_and_exposes_bridge_sequence_helpers():
    source = SCRIPT.read_text(encoding="utf-8")
    compile(source, str(SCRIPT), "exec")
    module = load_harness()
    assert module.DEFAULT_COUNT == 20
    assert module.signature_counts("12:34:abc") == (12, 34)
    assert module.signature_counts("not-a-signature") is None


def test_prompt_fingerprint_normalizes_whitespace_but_detects_content_changes():
    module = load_harness()
    assert module.prompt_fingerprint("alpha  beta\n") == module.prompt_fingerprint(" alpha beta ")
    assert module.prompt_fingerprint("alpha beta") != module.prompt_fingerprint("alpha gamma")


def test_m1_harness_enforces_exactly_twenty_operations_at_cli_boundary():
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'if args.count != DEFAULT_COUNT:' in source
    assert 'parser.error("--count must be exactly 20")' in source


def test_m1_harness_records_required_failure_categories():
    source = SCRIPT.read_text(encoding="utf-8")
    for token in [
        '"duplicate_indices"',
        '"skipped_indices"',
        '"repeated_prompts"',
        '"premature_claim_or_injection_violations"',
        '"terminal_chat_errors"',
        '"response_completed_to_prompt_injected_ms"',
        '"user_messages_added"',
        '"ack_verified"',
    ]:
        assert token in source

def test_prepare_durable_chat_uses_active_browser_chat_over_stale_persisted_url(
    monkeypatch, tmp_path
):
    module = load_harness()
    stale_url = "https://chatgpt.com/c/stale"
    active_url = "https://chatgpt.com/c/active"

    durable_state = tmp_path / "durable-automation-chat.json"
    durable_state.write_text(
        '{"chat_url": "https://chatgpt.com/c/stale", "updated_at": 1}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "DURABLE_CHAT_STATE", durable_state)
    monkeypatch.setattr(module, "M1_EVIDENCE", tmp_path / "m1-live.json")
    monkeypatch.setattr(module, "M0_EVIDENCE", tmp_path / "m0-live.json")

    monkeypatch.setattr(
        module,
        "browser_health",
        lambda _client: {
            "native_controller": True,
            "chat_url": active_url,
        },
    )
    monkeypatch.setattr(
        module,
        "browser_state",
        lambda _client: {
            "chat_url": active_url,
            "conversation_signature": "3:4:active",
        },
    )

    persisted = {}
    monkeypatch.setattr(
        module,
        "persist_durable_chat_url",
        lambda url, reason="": persisted.update(url=url, reason=reason),
    )

    result = module.prepare_durable_chat(
        object(),
        session_id="test-session",
        timeout_seconds=1,
    )

    assert result == (
        active_url,
        (3, 4),
        False,
        "",
        stale_url,
        "",
    )
    assert persisted == {
        "url": active_url,
        "reason": "active_browser_chat",
    }


def test_prepare_durable_chat_creates_new_chat_only_for_explicit_limit_signal(
    monkeypatch, tmp_path
):
    module = load_harness()
    active_url = "https://chatgpt.com/c/active"
    fresh_url = "https://chatgpt.com/c/fresh"

    durable_state = tmp_path / "durable-automation-chat.json"
    durable_state.write_text(
        '{"chat_url": "https://chatgpt.com/c/stale", "updated_at": 1}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "DURABLE_CHAT_STATE", durable_state)
    monkeypatch.setattr(module, "M1_EVIDENCE", tmp_path / "m1-live.json")
    monkeypatch.setattr(module, "M0_EVIDENCE", tmp_path / "m0-live.json")

    monkeypatch.setattr(
        module,
        "browser_health",
        lambda _client: {
            "native_controller": True,
            "chat_url": active_url,
            "provider_usage_limited": True,
        },
    )

    calls = {}
    monkeypatch.setattr(
        module,
        "create_fresh_chat_after_limit",
        lambda _client, **kwargs: (
            calls.update(kwargs) or (fresh_url, (0, 0), "op-new-chat")
        ),
    )

    result = module.prepare_durable_chat(
        object(),
        session_id="test-session",
        timeout_seconds=1,
    )

    assert result == (
        fresh_url,
        (0, 0),
        True,
        "usage_limit",
        "https://chatgpt.com/c/stale",
        "op-new-chat",
    )
    assert calls["reason"] == "usage_limit"
