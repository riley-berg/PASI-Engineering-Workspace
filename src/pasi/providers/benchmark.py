from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, Sequence


BENCHMARK_SCHEMA_VERSION = 1
MAX_CASES = 100
MAX_PROMPT_CHARS = 8_000


class BenchmarkError(ValueError):
    """Raised when benchmark configuration or execution evidence is invalid."""


class CompletionClient(Protocol):
    def complete(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    prompt: str
    required_json_keys: tuple[str, ...] = ()
    required_substrings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise BenchmarkError("benchmark case_id is required")
        if not self.prompt.strip() or len(self.prompt) > MAX_PROMPT_CHARS:
            raise BenchmarkError("benchmark prompt is required and bounded")

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "prompt": self.prompt,
            "required_json_keys": list(self.required_json_keys),
            "required_substrings": list(self.required_substrings),
        }


@dataclass(frozen=True)
class BenchmarkCaseResult:
    case_id: str
    passed: bool
    duration_ms: float
    response_digest: str
    reason: str
    evidence: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "duration_ms": self.duration_ms,
            "response_digest": self.response_digest,
            "reason": self.reason,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class BenchmarkReport:
    benchmark_version: int
    provider: str
    model: str
    profile_digest: str
    started_at: str
    finished_at: str
    passed: bool
    total_cases: int
    passed_cases: int
    failed_cases: int
    results: tuple[BenchmarkCaseResult, ...]
    report_digest: str = ""

    def __post_init__(self) -> None:
        if self.benchmark_version != BENCHMARK_SCHEMA_VERSION:
            raise BenchmarkError("unsupported benchmark schema version")
        if self.total_cases != len(self.results):
            raise BenchmarkError("total_cases does not match result count")
        if self.passed_cases + self.failed_cases != self.total_cases:
            raise BenchmarkError("benchmark case counts are inconsistent")
        if self.passed and self.failed_cases:
            raise BenchmarkError("benchmark cannot pass with failed cases")

        canonical = {
            "benchmark_version": self.benchmark_version,
            "provider": self.provider,
            "model": self.model,
            "profile_digest": self.profile_digest,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "passed": self.passed,
            "results": [result.to_dict() for result in self.results],
        }
        digest = hashlib.sha256(
            json.dumps(
                canonical,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        if self.report_digest and self.report_digest != digest:
            raise BenchmarkError("report_digest does not match report content")
        object.__setattr__(self, "report_digest", digest)

    def to_dict(self) -> dict[str, object]:
        return {
            "benchmark_version": self.benchmark_version,
            "provider": self.provider,
            "model": self.model,
            "profile_digest": self.profile_digest,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "passed": self.passed,
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "failed_cases": self.failed_cases,
            "results": [result.to_dict() for result in self.results],
            "report_digest": self.report_digest,
        }


class BenchmarkRunner:
    """Run the full bounded suite and make partial failure explicit."""

    def __init__(self, client: CompletionClient) -> None:
        self.client = client

    def run(
        self,
        cases: Sequence[BenchmarkCase],
        *,
        provider: str,
        model: str,
        profile_digest: str,
    ) -> BenchmarkReport:
        if not cases:
            raise BenchmarkError("benchmark suite requires at least one case")
        if len(cases) > MAX_CASES:
            raise BenchmarkError(f"benchmark suite exceeds {MAX_CASES} cases")

        started_at = datetime.now(timezone.utc).isoformat()
        results: list[BenchmarkCaseResult] = []

        for case in cases:
            started = time.perf_counter()
            response = ""
            response_digest = ""
            reason = "ok"
            evidence: dict[str, object] = {}
            passed = False

            try:
                response = self.client.complete(case.prompt)
                response_digest = hashlib.sha256(
                    response.encode("utf-8")
                ).hexdigest()

                for required in case.required_substrings:
                    if required not in response:
                        raise BenchmarkError(
                            f"required substring missing: {required}"
                        )

                if case.required_json_keys:
                    payload = json.loads(response)
                    if not isinstance(payload, dict):
                        raise BenchmarkError("response is not a JSON object")
                    missing = sorted(
                        set(case.required_json_keys) - set(payload.keys())
                    )
                    if missing:
                        raise BenchmarkError(
                            f"required JSON keys missing: {missing}"
                        )

                passed = True
                evidence = {
                    "response_sha256": response_digest,
                    "response_chars": len(response),
                }
            except Exception as exc:
                reason = str(exc)[:500]
                evidence = {
                    "response_sha256": response_digest,
                    "response_chars": len(response),
                    "error_type": type(exc).__name__,
                }

            results.append(
                BenchmarkCaseResult(
                    case_id=case.case_id,
                    passed=passed,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    response_digest=response_digest,
                    reason=reason,
                    evidence=evidence,
                )
            )

        finished_at = datetime.now(timezone.utc).isoformat()
        passed_cases = sum(result.passed for result in results)
        failed_cases = len(results) - passed_cases
        return BenchmarkReport(
            benchmark_version=BENCHMARK_SCHEMA_VERSION,
            provider=provider,
            model=model,
            profile_digest=profile_digest,
            started_at=started_at,
            finished_at=finished_at,
            passed=failed_cases == 0,
            total_cases=len(results),
            passed_cases=passed_cases,
            failed_cases=failed_cases,
            results=tuple(results),
        )
