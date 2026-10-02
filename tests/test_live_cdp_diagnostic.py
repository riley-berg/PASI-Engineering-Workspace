from __future__ import annotations

import json

from scripts.pasi_live_cdp_diagnostic import (
    DiagnosticFailure,
    diagnostic_response_matches_marker,
    make_probe_prompt,
    run_local_tests,
)


def test_diagnostic_failure_preserves_first_failure_boundary() -> None:
    failure = DiagnosticFailure("network_completion", "stream was not complete", {"stream_complete": False})

    assert failure.step == "network_completion"
    assert failure.message == "stream was not complete"
    assert failure.as_dict()["evidence"] == {"stream_complete": False}


def test_probe_prompt_is_non_mutating_and_contains_marker() -> None:
    prompt = make_probe_prompt("PASI_LIVE_CDP_TEST")

    assert "Do not modify files" in prompt
    assert "call tools" in prompt
    assert "PASI_LIVE_CDP_TEST" in prompt



def test_diagnostic_response_rejects_stale_text_that_happens_to_contain_marker() -> None:
    marker = "PASI_LIVE_CDP_TEST"
    response = "That is the previous assistant response.\n" + marker
    assert not diagnostic_response_matches_marker(response, marker)


def test_diagnostic_response_rejects_extra_text_around_marker() -> None:
    marker = "PASI_LIVE_CDP_TEST"
    response = marker + "\nAdditional text"
    assert not diagnostic_response_matches_marker(response, marker)


def test_diagnostic_response_accepts_exact_marker_after_outer_whitespace_trim() -> None:
    marker = "PASI_LIVE_CDP_TEST"
    response = "  \n" + marker + "\n  "
    assert diagnostic_response_matches_marker(response, marker)


def test_failure_payload_is_machine_readable() -> None:
    failure = DiagnosticFailure("bridge_health", "bad service", {"service": "wrong"})
    encoded = json.dumps(failure.as_dict())

    decoded = json.loads(encoded)
    assert decoded["step"] == "bridge_health"
    assert decoded["evidence"]["service"] == "wrong"

def test_local_tests_run_all_suites_before_reporting_failure(tmp_path, monkeypatch) -> None:
    extension_test = tmp_path / "extensions" / "pasi-chatgpt" / "src" / "test_popup_ui.cjs"
    extension_test.parent.mkdir(parents=True)
    extension_test.write_text("test()", encoding="utf-8")

    calls = []

    class Completed:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode
            self.stdout = "diagnostic output"

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "node" and command[2] == "web/app.test.js":
            return Completed(0)
        if command[0] == "node":
            return Completed(1)
        return Completed(1)

    monkeypatch.setattr("scripts.pasi_live_cdp_diagnostic.subprocess.run", fake_run)

    try:
        run_local_tests(tmp_path)
    except DiagnosticFailure as failure:
        assert failure.step == "local_tests"
        results = failure.evidence["results"]
        assert set(results) == {"pytest", "frontend", "extension"}
        assert results["pytest"]["returncode"] == 1
        assert results["frontend"]["returncode"] == 0
        assert results["extension"]["returncode"] == 1
        assert len(calls) == 3
        return

    raise AssertionError("run_local_tests should report the aggregate failure")

def test_m1_conversation_identity_ignores_url_query_changes() -> None:
    from scripts.pasi_m1_cdp_chain import chat_conversation_identity

    assert chat_conversation_identity("https://chatgpt.com/c/abc123") == "chatgpt.com/c/abc123"
    assert chat_conversation_identity("https://chatgpt.com/c/abc123?model=gpt-5") == "chatgpt.com/c/abc123"
    assert chat_conversation_identity("https://www.chatgpt.com/c/abc123/") == "www.chatgpt.com/c/abc123"
    assert chat_conversation_identity("https://chatgpt.com/c/xyz789") != "chatgpt.com/c/abc123"

