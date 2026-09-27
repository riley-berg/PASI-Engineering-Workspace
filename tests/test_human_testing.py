from datetime import datetime, timedelta, timezone

from pasi.core.human_testing import (
    CodeChangeCandidate,
    HumanTestRun,
    HumanTestStatus,
    HumanTestStepResult,
    HumanTestTrustEvaluator,
    HumanTestTrustPolicy,
    SelfImprovementGate,
    TrustStatus,
)
from pasi.core.human_test_store import HumanTestStore


def run(
    index: int,
    *,
    status: HumanTestStatus = HumanTestStatus.PASS,
    negative: bool = False,
    code_head: str | None = None,
    violations: tuple[str, ...] = (),
) -> HumanTestRun:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index)
    end = start + timedelta(seconds=1)
    step = HumanTestStepResult(
        step_id="health",
        action="api_get_json",
        status=status,
        started_at=start.isoformat(),
        ended_at=end.isoformat(),
        observed={"status": "ok"},
        error="" if status is HumanTestStatus.PASS else "expected failure",
    )
    payload = HumanTestRun(
        run_id=f"run-{index:03d}",
        suite_id="pasi-p0-qualification",
        suite_version=1,
        code_head=code_head or f"{index:040x}"[-40:],
        browser_name="Chromium",
        browser_version="136",
        extension_version="0.1.0",
        execution_source="mv3-human-test-extension",
        target_origin="http://127.0.0.1:3000",
        started_at=start.isoformat(),
        ended_at=end.isoformat(),
        status=status,
        steps=(step,),
        negative_control=negative,
        policy_violations=violations,
    )
    encoded = payload.to_dict()
    return HumanTestRun.from_mapping(encoded)


def test_trust_requires_all_qualification_dimensions():
    policy = HumanTestTrustPolicy(
        minimum_successful_runs=20,
        minimum_distinct_code_heads=3,
        minimum_negative_controls=3,
        required_trailing_successes=5,
    )
    runs = [
        run(i, negative=i in {17, 18, 19}, code_head=f"{(i % 3) + 1:040x}")
        for i in range(20)
    ]
    cert = HumanTestTrustEvaluator().evaluate(runs, policy=policy, issuer_secret="test-secret")
    assert cert.status is TrustStatus.TRUSTED
    assert cert.successful_runs == 20
    assert cert.distinct_code_heads == 3
    assert cert.negative_controls == 3


def test_failed_run_breaks_trailing_success_requirement():
    policy = HumanTestTrustPolicy(required_trailing_successes=5)
    runs = [run(i) for i in range(20)]
    runs[-5] = run(19, status=HumanTestStatus.FAIL)
    cert = HumanTestTrustEvaluator().evaluate(runs, policy=policy)
    assert cert.status is TrustStatus.PENDING
    assert cert.trailing_successes == 4


def test_self_improvement_requires_trust_and_exact_candidate_head():
    policy = HumanTestTrustPolicy(minimum_successful_runs=1, minimum_distinct_code_heads=1, minimum_negative_controls=1, required_trailing_successes=1)
    trusted = HumanTestTrustEvaluator().evaluate(
        [
            run(1, negative=True, code_head="1" * 40),
            run(2, code_head="2" * 40),
        ],
        policy=policy,
        issuer_secret="test-secret",
    )
    decision = SelfImprovementGate().evaluate(
        trusted,
        CodeChangeCandidate(
            candidate_sha="2" * 40,
            ci_passed=True,
            human_test_run_id="run-2",
            human_test_status=HumanTestStatus.PASS,
            human_test_code_head="2" * 40,
            human_test_execution_source="mv3-human-test-extension",
            human_test_suite_id="pasi-p0-qualification",
            human_test_extension_version="0.1.0",
        ),
    )
    assert decision.status == "PASS"

    mismatch = SelfImprovementGate().evaluate(
        trusted,
        CodeChangeCandidate(
            candidate_sha="3" * 40,
            ci_passed=True,
            human_test_run_id="run-2",
            human_test_status=HumanTestStatus.PASS,
            human_test_code_head="2" * 40,
            human_test_execution_source="mv3-human-test-extension",
            human_test_suite_id="pasi-p0-qualification",
            human_test_extension_version="0.1.0",
        ),
    )
    assert mismatch.status == "BLOCKED"
    assert "human-test-code-head-does-not-match-candidate" in mismatch.reasons


def test_human_test_store_persists_runs_and_trust(tmp_path):
    store = HumanTestStore(tmp_path / "human-tests.db")
    for i in range(3):
        store.record_run(run(i, negative=i == 0, code_head=f"{i+1:040x}"))
    certificate = store.evaluate_and_store_trust(
        issuer_secret="test-secret",
        policy=HumanTestTrustPolicy(
            minimum_successful_runs=3,
            minimum_distinct_code_heads=3,
            minimum_negative_controls=1,
            required_trailing_successes=3,
        )
    )
    assert certificate.status is TrustStatus.TRUSTED

    import os
    os.environ["PASI_HUMAN_TEST_TRUST_SECRET"] = "test-secret"
    restarted = HumanTestStore(tmp_path / "human-tests.db")
    assert restarted.get_run("run-2").status is HumanTestStatus.PASS
    assert restarted.get_trust().status is TrustStatus.TRUSTED
    del os.environ["PASI_HUMAN_TEST_TRUST_SECRET"]


def test_human_test_evidence_tampering_is_rejected():
    original = run(99).to_dict()
    original["steps"][0]["observed"]["status"] = "tampered"
    import pytest
    with pytest.raises(ValueError, match="evidence hash mismatch"):
        HumanTestRun.from_mapping(original)
