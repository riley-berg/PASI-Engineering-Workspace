from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping


BRIDGE_URL = os.environ.get("PASI_BRIDGE_URL", "http://127.0.0.1:8765").rstrip("/")
BRIDGE_TOKEN_FILE = Path.home() / ".pasi" / "bridge-token"
MAX_POLL_SECONDS = 15.0


class BrowserTestingError(RuntimeError):
    """Raised when a read-only browser-test request cannot be completed."""


def _token() -> str:
    try:
        token = BRIDGE_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise BrowserTestingError("PASI bridge token is unavailable") from exc
    if not token:
        raise BrowserTestingError("PASI bridge token is empty")
    return token


def _request(
    method: str,
    path: str,
    payload: Mapping[str, Any] | None = None,
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    url = BRIDGE_URL + path
    headers = {
        "Authorization": "Bearer " + _token(),
        "Accept": "application/json",
    }
    body = None
    if payload is not None:
        body = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise BrowserTestingError("PASI browser-test bridge returned an invalid JSON object")
            return value
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:500]
        except Exception:
            pass
        raise BrowserTestingError(
            f"PASI browser-test bridge returned HTTP {exc.code}" + (f": {detail}" if detail else "")
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise BrowserTestingError(f"PASI browser-test bridge unavailable: {exc}") from exc


class BrowserTestingClient:
    """RPC client to the extension's bounded CDP browser-testing boundary."""

    def request(
        self,
        action: str,
        *,
        tab_id: int | None = None,
        params: Mapping[str, Any] | None = None,
        timeout_seconds: float = MAX_POLL_SECONDS,
    ) -> dict[str, Any]:
        queued = _request(
            "POST",
            "/browser/testing/request",
            {
                "action": action,
                "tab_id": tab_id,
                "params": dict(params or {}),
            },
            timeout=5.0,
        )
        request_id = str(queued.get("request_id") or "").strip()
        if not request_id:
            raise BrowserTestingError("PASI browser-test bridge did not return a request id")

        deadline = time.monotonic() + max(1.0, min(timeout_seconds, MAX_POLL_SECONDS))
        encoded = urllib.parse.quote(request_id, safe="")
        while time.monotonic() < deadline:
            result = _request(
                "GET",
                "/browser/testing/result?request_id=" + encoded,
                timeout=3.0,
            )
            value = result.get("result")
            if isinstance(value, Mapping):
                if value.get("ok") is not True:
                    error = value.get("error")
                    message = error.get("message") if isinstance(error, Mapping) else str(error or "browser test failed")
                    raise BrowserTestingError(message)
                data = value.get("data")
                return dict(data) if isinstance(data, Mapping) else {}
            time.sleep(0.15)

        raise BrowserTestingError(
            f"PASI browser-test request {request_id} timed out waiting for the extension"
        )


def _elements_contain_text(elements: Any, expected: str) -> bool:
    needle = str(expected or "").strip().casefold()
    if not needle:
        return True
    for element in elements if isinstance(elements, list) else []:
        text = " ".join(
            str(element.get(key) or "")
            for key in ("text", "name", "aria_label", "title")
            if isinstance(element, Mapping)
        ).casefold()
        if needle in text:
            return True
    return False


def _match_reference_element(element: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    for key in ("tag", "id", "class_name", "role", "aria_label", "title", "test_id", "type", "name"):
        if key in expected and str(element.get(key) or "") != str(expected.get(key) or ""):
            return False
    if expected.get("visible") is True and element.get("visible") is not True:
        return False
    if expected.get("disabled") is False and element.get("disabled") is True:
        return False
    expected_text = str(expected.get("text") or "").strip()
    if expected_text and expected_text.casefold() not in str(element.get("text") or "").casefold():
        return False
    return True


def _require_reference_elements(dom: Mapping[str, Any], requirements: Any) -> list[str]:
    failures: list[str] = []
    elements = dom.get("elements") if isinstance(dom, Mapping) else []
    if not isinstance(requirements, list):
        return failures
    for index, requirement in enumerate(requirements):
        if not isinstance(requirement, Mapping):
            failures.append(f"required_elements[{index}] is not an object")
            continue
        selector = str(requirement.get("selector") or "body")
        matched = dom.get("selector") == selector and isinstance(elements, list)
        if not matched:
            failures.append(f"required_elements[{index}] selector was not captured: {selector}")
            continue
        minimum = max(1, int(requirement.get("count_min") or 1))
        matches = [
            element for element in elements
            if isinstance(element, Mapping) and _match_reference_element(element, requirement)
        ]
        if len(matches) < minimum:
            failures.append(
                f"required_elements[{index}] expected at least {minimum} match(es), found {len(matches)}"
            )
    return failures


def _reference_failure(check: str, detail: str) -> dict[str, str]:
    return {"check": check, "detail": detail}


def load_reference_material(path: str | os.PathLike[str]) -> dict[str, Any]:
    reference_path = Path(path).expanduser().resolve()
    try:
        payload = json.loads(reference_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BrowserTestingError(f"reference material is not valid JSON: {reference_path}") from exc
    if not isinstance(payload, dict):
        raise BrowserTestingError("reference material must be a JSON object")
    return payload


class BrowserProofResult(dict):
    """Structured, reference-bound webpage proof result."""


def prove_page_functionality(
    client: BrowserTestingClient,
    reference: Mapping[str, Any],
    *,
    tab_id: int | None = None,
) -> BrowserProofResult:
    """
    Execute a bounded declarative webpage proof.

    A 100% result means every requirement explicitly present in the reference
    material passed. It does not mean the page is universally or mathematically
    proven functional outside the tested reference scope.
    """
    if not isinstance(reference, Mapping):
        raise BrowserTestingError("reference material must be an object")

    checks: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: str, data: Mapping[str, Any] | None = None) -> None:
        checks.append({"name": name, "passed": ok, "detail": detail})
        if isinstance(data, Mapping):
            evidence.append({"check": name, "data": dict(data)})

    page = client.request("page_state", tab_id=tab_id)
    evidence.append({"check": "page_state", "data": page})

    expected_url_contains = str(reference.get("url_contains") or reference.get("url") or "").strip()
    if expected_url_contains:
        actual_url = str(page.get("url") or "")
        record(
            "url",
            expected_url_contains in actual_url,
            f"expected URL to contain {expected_url_contains!r}; actual {actual_url!r}",
        )

    dom = client.request("dom", tab_id=tab_id, params={"selector": "body", "max_elements": 100, "max_text_chars": 2000})
    evidence.append({"check": "dom", "data": dom})
    for index, expected_text in enumerate(reference.get("required_text", []) if isinstance(reference.get("required_text"), list) else []):
        text_value = str(expected_text or "").strip()
        ok = _elements_contain_text(dom.get("elements"), text_value)
        record(
            f"required_text[{index}]",
            ok,
            f"expected visible/reference text {text_value!r}",
        )

    for index, requirement in enumerate(reference.get("required_elements", []) if isinstance(reference.get("required_elements"), list) else []):
        if not isinstance(requirement, Mapping):
            record(f"required_elements[{index}]", False, "requirement is not an object")
            continue
        selector = str(requirement.get("selector") or "body")
        snapshot = dom if selector == "body" else client.request(
            "dom",
            tab_id=tab_id,
            params={
                "selector": selector,
                "max_elements": min(max(int(requirement.get("max_elements") or 20), 1), 100),
                "max_text_chars": 2000,
            },
        )
        failures = _require_reference_elements(snapshot, [requirement])
        record(
            f"required_elements[{index}]",
            not failures,
            failures[0] if failures else f"matched reference element for selector {selector!r}",
            snapshot,
        )

    for index, requirement in enumerate(reference.get("accessibility", []) if isinstance(reference.get("accessibility"), list) else []):
        if not isinstance(requirement, Mapping):
            record(f"accessibility[{index}]", False, "requirement is not an object")
            continue
        accessibility = client.request(
            "accessibility",
            tab_id=tab_id,
            params={
                "name": requirement.get("name") or requirement.get("target") or "",
                "role": requirement.get("role") or "",
                "limit": min(max(int(requirement.get("limit") or 20), 1), 100),
            },
        )
        matches = accessibility.get("elements") if isinstance(accessibility, Mapping) else []
        expected_name = str(requirement.get("name") or requirement.get("target") or "").casefold()
        expected_role = str(requirement.get("role") or "").casefold()
        passed = any(
            isinstance(item, Mapping)
            and (not expected_name or expected_name in str(item.get("name") or "").casefold())
            and (not expected_role or expected_role == str(item.get("role") or "").casefold())
            for item in matches if isinstance(item, Mapping)
        )
        record(
            f"accessibility[{index}]",
            passed,
            f"expected accessible control name={expected_name!r} role={expected_role!r}",
            accessibility,
        )

    steps = reference.get("steps")
    if isinstance(steps, list):
        for index, step in enumerate(steps):
            if not isinstance(step, Mapping):
                record(f"steps[{index}]", False, "step is not an object")
                continue
            action = str(step.get("action") or "").strip()
            params = step.get("params") if isinstance(step.get("params"), Mapping) else {
                key: value for key, value in step.items()
                if key not in {"action", "expect", "settle_ms", "name"}
            }
            try:
                result = client.request(action, tab_id=tab_id, params=params)
                evidence.append({"check": f"steps[{index}]", "data": result})
                settle_ms = min(max(float(step.get("settle_ms") or 0) / 1000.0, 0.0), 5.0)
                if settle_ms:
                    time.sleep(settle_ms)
                expectations = step.get("expect") if isinstance(step.get("expect"), Mapping) else {}
                step_failures: list[str] = []
                if expectations.get("url_contains"):
                    page_after = client.request("page_state", tab_id=tab_id)
                    if str(expectations["url_contains"]) not in str(page_after.get("url") or ""):
                        step_failures.append("URL expectation failed")
                if expectations.get("text") is not None:
                    dom_after = client.request(
                        "dom",
                        tab_id=tab_id,
                        params={"selector": "body", "max_elements": 100, "max_text_chars": 2000},
                    )
                    if not _elements_contain_text(dom_after.get("elements"), str(expectations["text"])):
                        step_failures.append("text expectation failed")
                record(
                    f"steps[{index}]",
                    not step_failures,
                    "; ".join(step_failures) if step_failures else "action and expectations passed",
                )
            except BrowserTestingError as exc:
                record(f"steps[{index}]", False, str(exc))

    if reference.get("require_no_console_errors") is True:
        console = client.request("console_errors", tab_id=tab_id, params={"limit": 100})
        record(
            "console_errors",
            int(console.get("count") or 0) == 0,
            f"captured {int(console.get('count') or 0)} console error(s)",
            console,
        )

    network_requirements = reference.get("network_requirements")
    if isinstance(network_requirements, list):
        network = client.request("network", tab_id=tab_id, params={"limit": 100})
        events = network.get("events") if isinstance(network, Mapping) else []
        for index, requirement in enumerate(network_requirements):
            if not isinstance(requirement, Mapping):
                record(f"network_requirements[{index}]", False, "requirement is not an object")
                continue
            matching = [
                event for event in events
                if isinstance(event, Mapping)
                and (not requirement.get("url_contains") or str(requirement["url_contains"]) in str(event.get("url") or ""))
                and (not requirement.get("resource_type") or str(requirement["resource_type"]) == str(event.get("resource_type") or ""))
                and (not requirement.get("kind") or str(requirement["kind"]) == str(event.get("kind") or ""))
            ]
            if requirement.get("status") is not None:
                matching = [
                    event for event in matching
                    if int(event.get("status") or 0) == int(requirement["status"])
                ]
            minimum = max(1, int(requirement.get("count_min") or 1))
            record(
                f"network_requirements[{index}]",
                len(matching) >= minimum,
                f"expected at least {minimum} matching network event(s), found {len(matching)}",
                {"matching": matching},
            )

    screenshots = int(reference.get("screenshot_count") or 0)
    for index in range(max(0, min(screenshots, 5))):
        shot = client.request("screenshot", tab_id=tab_id)
        record(
            f"screenshot[{index}]",
            bool(shot.get("image_base64")),
            "screenshot captured",
            {key: shot.get(key) for key in ("mime_type", "width", "height", "byte_length", "captured_at")},
        )

    total = len(checks)
    passed = sum(1 for item in checks if item.get("passed") is True)
    coverage = 100.0 if total == 0 else round((passed / total) * 100.0, 2)
    return BrowserProofResult({
        "schema_version": 1,
        "result": "passed" if total > 0 and passed == total else "failed",
        "reference_complete": total > 0 and passed == total,
        "coverage_percent": coverage,
        "checks_total": total,
        "checks_passed": passed,
        "checks_failed": total - passed,
        "checks": checks,
        "evidence": evidence,
        "limitations": [
            "100% means complete coverage of the supplied declarative reference requirements.",
            "It does not prove behavior outside the tested flows, environments, dependencies, or unreferenced states.",
        ],
    })


def prove_page_functionality_from_file(
    client: BrowserTestingClient,
    reference_path: str | os.PathLike[str],
    *,
    tab_id: int | None = None,
) -> BrowserProofResult:
    return prove_page_functionality(
        client,
        load_reference_material(reference_path),
        tab_id=tab_id,
    )
