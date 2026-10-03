from __future__ import annotations

from typing import Any

from automation.pasi_agent_browser_testing import (
    BrowserTestingClient,
    BrowserTestingError,
    prove_page_functionality,
)


class FakeBrowserClient(BrowserTestingClient):
    def __init__(self, *, missing_text: bool = False):
        self.missing_text = missing_text
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def request(self, action: str, *, tab_id: int | None = None, params=None, timeout_seconds: float = 15.0):
        params = dict(params or {})
        self.calls.append((action, params))
        if action == "page_state":
            return {
                "kind": "page_state",
                "tab_id": tab_id or 1,
                "url": "https://example.test/app",
                "security_origin": "https://example.test",
                "mime_type": "text/html",
                "width": 1280,
                "height": 720,
            }
        if action == "dom":
            text = "" if self.missing_text else "Welcome to PASI"
            selector = str(params.get("selector") or "body")
            elements = [{
                "tag": "body" if selector == "body" else "button",
                "id": "start" if selector != "body" else "",
                "role": "button" if selector != "body" else None,
                "aria_label": "Start",
                "text": text if selector == "body" else "Start",
                "visible": True,
                "disabled": False,
            }]
            return {"kind": "dom", "selector": selector, "matched_count": 1, "elements": elements}
        if action == "accessibility":
            return {
                "kind": "accessibility",
                "matched_count": 1,
                "elements": [{
                    "name": "Start",
                    "role": "button",
                    "focused": False,
                    "disabled": False,
                }],
            }
        if action == "click":
            return {"kind": "interaction", "action": "click", "success": True}
        if action == "console_errors":
            return {"kind": "console", "count": 0, "errors": []}
        if action == "network":
            return {
                "kind": "network",
                "events": [{
                    "kind": "response",
                    "url": "https://example.test/api/save",
                    "resource_type": "Fetch",
                    "status": 200,
                }],
            }
        if action == "screenshot":
            return {
                "kind": "screenshot",
                "image_base64": "aGVsbG8=",
                "mime_type": "image/png",
                "width": 1280,
                "height": 720,
                "byte_length": 5,
                "captured_at": "2026-10-03T00:00:00Z",
            }
        raise BrowserTestingError("unexpected action: " + action)


def test_reference_proof_reports_complete_reference_coverage():
    client = FakeBrowserClient()
    result = prove_page_functionality(
        client,
        {
            "url_contains": "https://example.test/app",
            "required_text": ["Welcome to PASI"],
            "required_elements": [{
                "selector": "#start",
                "tag": "button",
                "text": "Start",
                "visible": True,
            }],
            "accessibility": [{
                "name": "Start",
                "role": "button",
            }],
            "steps": [{
                "action": "click",
                "params": {"target": "Start", "role": "button"},
                "expect": {"text": "Welcome to PASI"},
            }],
            "require_no_console_errors": True,
            "network_requirements": [{
                "url_contains": "/api/save",
                "resource_type": "Fetch",
                "status": 200,
            }],
            "screenshot_count": 1,
        },
        tab_id=7,
    )

    assert result["result"] == "passed"
    assert result["reference_complete"] is True
    assert result["coverage_percent"] == 100.0
    assert result["checks_failed"] == 0
    assert any(check["name"] == "steps[0]" for check in result["checks"])
    assert any(item["check"] == "screenshot[0]" for item in result["evidence"])


def test_reference_proof_fails_when_reference_requirement_is_missing():
    result = prove_page_functionality(
        FakeBrowserClient(missing_text=True),
        {
            "url_contains": "https://example.test/app",
            "required_text": ["Welcome to PASI"],
        },
        tab_id=7,
    )

    assert result["result"] == "failed"
    assert result["reference_complete"] is False
    assert result["coverage_percent"] == 0.0
    assert result["checks_failed"] == 1


def test_reference_proof_does_not_claim_unbounded_functionality():
    result = prove_page_functionality(
        FakeBrowserClient(),
        {"required_text": ["Welcome to PASI"]},
        tab_id=7,
    )

    assert any("outside the tested flows" in text for text in result["limitations"])
