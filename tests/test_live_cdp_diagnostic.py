from __future__ import annotations

import json

from scripts.pasi_live_cdp_diagnostic import DiagnosticFailure, diagnostic_response_matches_marker, make_probe_prompt


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