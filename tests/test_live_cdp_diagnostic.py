from __future__ import annotations

import json

from scripts.pasi_live_cdp_diagnostic import DiagnosticFailure, make_probe_prompt


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


def test_failure_payload_is_machine_readable() -> None:
    failure = DiagnosticFailure("bridge_health", "bad service", {"service": "wrong"})
    encoded = json.dumps(failure.as_dict())

    decoded = json.loads(encoded)
    assert decoded["step"] == "bridge_health"
    assert decoded["evidence"]["service"] == "wrong"
