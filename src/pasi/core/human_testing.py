from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


HUMAN_TEST_SCHEMA_VERSION = 1
TRUST_SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def canonical_sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class HumanTestStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class TrustStatus(StrEnum):
    PENDING = "pending"
    TRUSTED = "trusted"
    REVOKED = "revoked"


@dataclass(frozen=True)
class HumanTestStepResult:
    step_id: str
    action: str
    status: HumanTestStatus
    started_at: str
    ended_at: str
    observed: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass(frozen=True)
class HumanTestRun:
    run_id: str
    suite_id: str
    suite_version: int
    code_head: str
    browser_name: str
    browser_version: str
    target_origin: str
    started_at: str
    ended_at: str
    status: HumanTestStatus
    steps: tuple[HumanTestStepResult, ...]
    negative_control: bool = False
    policy_violations: tuple[str, ...] = ()
    evidence_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": HUMAN_TEST_SCHEMA_VERSION,
            "run_id": self.run_id,
            "suite_id": self.suite_id,
            "suite_version": self.suite_version,
            "code_head": self.code_head,
            "browser_name": self.browser_name,
            "browser_version": self.browser_version,
            "target_origin": self.target_origin,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "status": self.status.value,
            "steps": [
                {
                    "step_id": step.step_id,
                    "action": step.action,
                    "status": step.status.value,
                    "started_at": step.started_at,
                    "ended_at": step.ended_at,
                    "observed": step.observed,
                    "error": step.error,
                }
                for step in self.steps
            ],
            "negative_control": self.negative_control,
            "policy_violations": list(self.policy_violations),
        }
        return {
            **payload,
            "evidence_sha256": canonical_sha256(payload),
        }

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "HumanTestRun":
        if int(value.get("schema_version", -1)) != HUMAN_TEST_SCHEMA_VERSION:
            raise ValueError("unsupported human-test-run schema version")
        raw_steps = value.get("steps")
        if not isinstance(raw_steps, list):
            raise ValueError("steps must be an array")
        steps = tuple(
            HumanTestStepResult(
                step_id=str(item["step_id"]),
                action=str(item["action"]),
                status=HumanTestStatus(item["status"]),
                started_at=str(item["started_at"]),
                ended_at=str(item["ended_at"]),
                observed=dict(item.get("observed", {})),
                error=str(item.get("error", "")),
            )
            for item in raw_steps
        )
        run = cls(
            run_id=str(value["run_id"]),
            suite_id=str(value["suite_id"]),
            suite_version=int(value["suite_version"]),
            code_head=str(value["code_head"]),
            browser_name=str(value["browser_name"]),
            browser_version=str(value["browser_version"]),
            target_origin=str(value["target_origin"]),
            started_at=str(value["started_at"]),
            ended_at=str(value["ended_at"]),
            status=HumanTestStatus(value["status"]),
            steps=steps,
            negative_control=bool(value.get("negative_control", False)),
            policy_violations=tuple(str(item) for item in value.get("policy_violations", [])),
            evidence_sha256=str(value.get("evidence_sha256", "")),
        )
        payload = run.to_dict()
        supplied = payload.pop("evidence_sha256")
        if run.evidence_sha256 and run.evidence_sha256 != supplied:
            raise ValueError("human-test evidence hash mismatch")
        return run


@dataclass(frozen=True)
class HumanTestTrustPolicy:
    minimum_successful_runs: int = 20
    minimum_distinct_code_heads: int = 3
    minimum_negative_controls: int = 3
    maximum_policy_violations: int = 0
    required_trailing_successes: int = 5

    def __post_init__(self) -> None:
        if (
            self.minimum_successful_runs <= 0
            or self.minimum_distinct_code_heads <= 0
            or self.minimum_negative_controls <= 0
            or self.required_trailing_successes <= 0
        ):
            raise ValueError("trust thresholds must be positive")
        if self.maximum_policy_violations < 0:
            raise ValueError("maximum_policy_violations must be non-negative")


@dataclass(frozen=True)
class HumanTestTrustCertificate:
    status: TrustStatus
    issued_at: str
    policy: HumanTestTrustPolicy
    successful_runs: int
    distinct_code_heads: int
    negative_controls: int
    policy_violations: int
    trailing_successes: int
    source_run_ids: tuple[str, ...]
    certificate_sha256: str

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": TRUST_SCHEMA_VERSION,
            "status": self.status.value,
            "issued_at": self.issued_at,
            "policy": {
                "minimum_successful_runs": self.policy.minimum_successful_runs,
                "minimum_distinct_code_heads": self.policy.minimum_distinct_code_heads,
                "minimum_negative_controls": self.policy.minimum_negative_controls,
                "maximum_policy_violations": self.policy.maximum_policy_violations,
                "required_trailing_successes": self.policy.required_trailing_successes,
            },
            "successful_runs": self.successful_runs,
            "distinct_code_heads": self.distinct_code_heads,
            "negative_controls": self.negative_controls,
            "policy_violations": self.policy_violations,
            "trailing_successes": self.trailing_successes,
            "source_run_ids": list(self.source_run_ids),
        }
        return {
            **payload,
            "certificate_sha256": canonical_sha256(payload),
        }


class HumanTestTrustEvaluator:
    def evaluate(
        self,
        runs: list[HumanTestRun],
        *,
        policy: HumanTestTrustPolicy | None = None,
    ) -> HumanTestTrustCertificate:
        trust_policy = policy or HumanTestTrustPolicy()
        ordered = sorted(runs, key=lambda item: (item.ended_at, item.run_id))
        successful = [run for run in ordered if run.status is HumanTestStatus.PASS]
        negative_controls = [
            run for run in successful if run.negative_control
        ]
        policy_violations = sum(len(run.policy_violations) for run in ordered)
        distinct_heads = {
            run.code_head for run in successful if run.code_head
        }
        trailing_successes = 0
        for run in reversed(ordered):
            if run.status is not HumanTestStatus.PASS:
                break
            trailing_successes += 1

        trusted = (
            len(successful) >= trust_policy.minimum_successful_runs
            and len(distinct_heads) >= trust_policy.minimum_distinct_code_heads
            and len(negative_controls) >= trust_policy.minimum_negative_controls
            and policy_violations <= trust_policy.maximum_policy_violations
            and trailing_successes >= trust_policy.required_trailing_successes
        )

        source_ids = tuple(run.run_id for run in successful[-50:])
        draft = {
            "status": TrustStatus.TRUSTED.value if trusted else TrustStatus.PENDING.value,
            "issued_at": utc_now(),
            "policy": {
                "minimum_successful_runs": trust_policy.minimum_successful_runs,
                "minimum_distinct_code_heads": trust_policy.minimum_distinct_code_heads,
                "minimum_negative_controls": trust_policy.minimum_negative_controls,
                "maximum_policy_violations": trust_policy.maximum_policy_violations,
                "required_trailing_successes": trust_policy.required_trailing_successes,
            },
            "successful_runs": len(successful),
            "distinct_code_heads": len(distinct_heads),
            "negative_controls": len(negative_controls),
            "policy_violations": policy_violations,
            "trailing_successes": trailing_successes,
            "source_run_ids": list(source_ids),
        }
        return HumanTestTrustCertificate(
            status=TrustStatus.TRUSTED if trusted else TrustStatus.PENDING,
            issued_at=draft["issued_at"],
            policy=trust_policy,
            successful_runs=len(successful),
            distinct_code_heads=len(distinct_heads),
            negative_controls=len(negative_controls),
            policy_violations=policy_violations,
            trailing_successes=trailing_successes,
            source_run_ids=source_ids,
            certificate_sha256=canonical_sha256(draft),
        )


@dataclass(frozen=True)
class CodeChangeCandidate:
    candidate_sha: str
    ci_passed: bool
    human_test_run_id: str
    human_test_status: HumanTestStatus
    human_test_code_head: str
    policy_violations: tuple[str, ...] = ()


@dataclass(frozen=True)
class SelfImprovementDecision:
    status: str
    candidate_sha: str
    reasons: tuple[str, ...]
    trust_status: TrustStatus
    evaluated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status,
            "candidate_sha": self.candidate_sha,
            "reasons": list(self.reasons),
            "trust_status": self.trust_status.value,
            "evaluated_at": self.evaluated_at,
        }


class SelfImprovementGate:
    """Promotion gate for future self-improvement proposals.

    The gate does not generate patches and cannot grant trust. It only evaluates
    a candidate after an independently issued trust certificate exists.
    """

    def evaluate(
        self,
        certificate: HumanTestTrustCertificate,
        candidate: CodeChangeCandidate,
    ) -> SelfImprovementDecision:
        reasons: list[str] = []
        if certificate.status is not TrustStatus.TRUSTED:
            reasons.append("human-test-extension-is-not-trusted")
        if not candidate.ci_passed:
            reasons.append("repository-ci-failed")
        if candidate.human_test_status is not HumanTestStatus.PASS:
            reasons.append("human-test-suite-failed")
        if candidate.human_test_code_head != candidate.candidate_sha:
            reasons.append("human-test-code-head-does-not-match-candidate")
        if candidate.policy_violations:
            reasons.append("human-test-policy-violations-present")

        status = "PASS" if not reasons else "BLOCKED"
        return SelfImprovementDecision(
            status=status,
            candidate_sha=candidate.candidate_sha,
            reasons=tuple(reasons),
            trust_status=certificate.status,
            evaluated_at=utc_now(),
        )
