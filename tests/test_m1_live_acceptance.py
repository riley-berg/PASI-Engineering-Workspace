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
