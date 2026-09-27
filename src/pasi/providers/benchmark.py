from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from pasi.providers.protocol import ChatMessage, ModelProvider, ProviderResponse


BENCHMARK_SCHEMA_VERSION = 1
MAX_CASES = 100
MAX_PROMPT_CHARS = 4096
MAX_RESPONSE_EVIDENCE_CHARS = 4096


class BenchmarkError(ValueError):
    """Raised when benchmark definitions or runner configuration are invalid."""


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    prompt: str
    checks: tuple[str, ...]
    max_latency_ms: float = 30_000.0

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise BenchmarkError("case_id is required")
        if not self.prompt.strip() or len(self.prompt) > MAX_PROMPT_CHARS:
            raise BenchmarkError("prompt is required and bounded")
        if not self.checks:
            raise BenchmarkError(f"benchmark case {self.case_id} requires checks")
        if self.max_latency_ms <= 0:
            raise BenchmarkError("max_latency_ms must be positive")


@dataclass(frozen=True)
class BenchmarkResult:
    case_id: str
    classification: str
    passed: bool
    latency_ms: float
    response_sha256: str
    response_evidence: str
    failure_reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "classification": self.classification,
            "passed": self.passed,
            "latency_ms": self.latency_ms,
            "response_sha256": self.response_sha256,
            "response_evidence": self.response_evidence,
            "failure_reason": self.failure_reason,
        }


@dataclass(frozen=True)
class BenchmarkReport:
    suite_version: int
    provider: str
    model: str
    profile_name: str
    profile_digest: str
    registry_digest: str
    case_count: int
    passed_count: int
    failed_count: int
    partial_failure: bool
    passed: bool
    results: tuple[BenchmarkResult, ...]
    started_at: str
    completed_at: str
    report_digest: str

    def to_dict(self) -> dict[str, object]:
        return {
            "suite_version": self.suite_version,
            "provider": self.provider,
            "model": self.model,
            "profile_name": self.profile_name,
            "profile_digest": self.profile_digest,
            "registry_digest": self.registry_digest,
            "case_count": self.case_count,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "partial_failure": self.partial_failure,
            "passed": self.passed,
            "results": [result.to_dict() for result in self.results],
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "report_digest": self.report_digest,
        }


def load_suite(path: Path | str) -> tuple[BenchmarkCase, ...]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkError("benchmark suite could not be loaded") from exc

    if not isinstance(payload, dict):
        raise BenchmarkError("benchmark suite must be an object")
    if int(payload.get("version", -1)) != BENCHMARK_SCHEMA_VERSION:
        raise BenchmarkError("unsupported benchmark suite version")

    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise BenchmarkError("benchmark suite must contain cases")
    if len(raw_cases) > MAX_CASES:
        raise BenchmarkError("benchmark suite exceeds case limit")

    cases: list[BenchmarkCase] = []
    seen: set[str] = set()
    for raw in raw_cases:
        if not isinstance(raw, dict):
            raise BenchmarkError("benchmark case must be an object")
        case = BenchmarkCase(
            case_id=str(raw.get("case_id", "")),
            prompt=str(raw.get("prompt", "")),
            checks=tuple(str(item) for item in raw.get("checks", [])),
            max_latency_ms=float(raw.get("max_latency_ms", 30_000)),
        )
        if case.case_id in seen:
            raise BenchmarkError(f"duplicate benchmark case: {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    return tuple(cases)


class BenchmarkRunner:
    """Run bounded benchmark cases through the real model-provider adapter boundary."""

    def run(
        self,
        *,
        cases: Sequence[BenchmarkCase],
        provider: ModelProvider,
        model: str,
        profile_name: str,
        profile_digest: str,
        registry_digest: str,
    ) -> BenchmarkReport:
        if not cases:
            raise BenchmarkError("benchmark run requires at least one case")
        if len(cases) > MAX_CASES:
            raise BenchmarkError("benchmark run exceeds case limit")
        started_at = datetime.now(timezone.utc).isoformat()
        results: list[BenchmarkResult] = []

        for case in cases:
            results.append(
                self._run_case(
                    case,
                    provider=provider,
                    model=model,
                )
            )

        completed_at = datetime.now(timezone.utc).isoformat()
        passed_count = sum(result.passed for result in results)
        failed_count = len(results) - passed_count
        partial_failure = 0 < passed_count < len(results)
        passed = failed_count == 0

        canonical_payload: dict[str, Any] = {
            "suite_version": BENCHMARK_SCHEMA_VERSION,
            "provider": provider.name,
            "model": model,
            "profile_name": profile_name,
            "profile_digest": profile_digest,
            "registry_digest": registry_digest,
            "case_count": len(results),
            "passed_count": passed_count,
            "failed_count": failed_count,
            "partial_failure": partial_failure,
            "passed": passed,
            "results": [
                {
                    "case_id": result.case_id,
                    "classification": result.classification,
                    "passed": result.passed,
                    "response_sha256": result.response_sha256,
                    "failure_reason": result.failure_reason,
                }
                for result in results
            ],
        }
        report_digest = hashlib.sha256(
            json.dumps(
                canonical_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()

        return BenchmarkReport(
            suite_version=BENCHMARK_SCHEMA_VERSION,
            provider=provider.name,
            model=model,
            profile_name=profile_name,
            profile_digest=profile_digest,
            registry_digest=registry_digest,
            case_count=len(results),
            passed_count=passed_count,
            failed_count=failed_count,
            partial_failure=partial_failure,
            passed=passed,
            results=tuple(results),
            started_at=started_at,
            completed_at=completed_at,
            report_digest=report_digest,
        )

    @staticmethod
    def write_report(path: Path | str, report: BenchmarkReport) -> None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report.to_dict(), sort_keys=True, indent=2),
            encoding="utf-8",
        )

    def _run_case(
        self,
        case: BenchmarkCase,
        *,
        provider: ModelProvider,
        model: str,
    ) -> BenchmarkResult:
        started = time.perf_counter()
        try:
            response: ProviderResponse = provider.generate(
                [ChatMessage(role="user", content=case.prompt)],
                model=model,
            )
            latency_ms = (time.perf_counter() - started) * 1000
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            message = str(exc).strip()[:MAX_RESPONSE_EVIDENCE_CHARS]
            return BenchmarkResult(
                case_id=case.case_id,
                classification="provider_error",
                passed=False,
                latency_ms=latency_ms,
                response_sha256=hashlib.sha256(b"").hexdigest(),
                response_evidence="",
                failure_reason=message or exc.__class__.__name__,
            )

        text = response.text
        response_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        evidence = text[:MAX_RESPONSE_EVIDENCE_CHARS]
        if latency_ms > case.max_latency_ms:
            return BenchmarkResult(
                case_id=case.case_id,
                classification="latency_exceeded",
                passed=False,
                latency_ms=latency_ms,
                response_sha256=response_sha256,
                response_evidence=evidence,
                failure_reason=(
                    f"latency {latency_ms:.1f}ms exceeded "
                    f"{case.max_latency_ms:.1f}ms"
                ),
            )

        try:
            payload: object = json.loads(text)
        except json.JSONDecodeError:
            payload = text

        failures: list[str] = []
        for check in case.checks:
            if check == "json_object":
                if not isinstance(payload, dict):
                    failures.append("response is not a JSON object")
            elif check == "status_ok":
                if not isinstance(payload, dict) or payload.get("status") != "ok":
                    failures.append("status is not ok")
            elif check.startswith("key:"):
                key = check.split(":", 1)[1]
                if not isinstance(payload, dict) or key not in payload:
                    failures.append(f"missing JSON key: {key}")
            elif check.startswith("contains:"):
                expected = check.split(":", 1)[1]
                if expected not in text:
                    failures.append(f"missing required text: {expected}")
            else:
                failures.append(f"unknown benchmark check: {check}")

        if failures:
            return BenchmarkResult(
                case_id=case.case_id,
                classification="contract_failure",
                passed=False,
                latency_ms=latency_ms,
                response_sha256=response_sha256,
                response_evidence=evidence,
                failure_reason="; ".join(failures),
            )

        return BenchmarkResult(
            case_id=case.case_id,
            classification="pass",
            passed=True,
            latency_ms=latency_ms,
            response_sha256=response_sha256,
            response_evidence=evidence,
        )
