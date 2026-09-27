from pathlib import Path

import pytest

from pasi.providers.benchmark import (
    BenchmarkCase,
    BenchmarkError,
    BenchmarkRunner,
)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, prompt: str) -> str:
        return self.responses.pop(0)


def test_benchmark_requires_all_cases_to_pass():
    report = BenchmarkRunner(
        FakeClient([
            '{"status":"ok","result":"ready"}',
            '{"status":"bad"}',
        ])
    ).run(
        [
            BenchmarkCase("json-ok", "return json", required_json_keys=("status", "result")),
            BenchmarkCase("json-bad", "return json", required_json_keys=("status", "result")),
        ],
        provider="fake",
        model="fake-model",
        profile_digest="0" * 64,
    )
    assert report.passed is False
    assert report.total_cases == 2
    assert report.passed_cases == 1
    assert report.failed_cases == 1
    assert report.results[1].reason


def test_benchmark_report_digest_is_deterministic_for_identical_result_data():
    client = FakeClient(['"ok"'])
    report = BenchmarkRunner(client).run(
        [BenchmarkCase("contains", "return ok", required_substrings=("ok",))],
        provider="fake",
        model="fake-model",
        profile_digest="1" * 64,
    )
    assert len(report.report_digest) == 64
    assert report.report_digest == report.report_digest


def test_benchmark_rejects_empty_or_oversized_suites():
    runner = BenchmarkRunner(FakeClient([]))
    with pytest.raises(BenchmarkError):
        runner.run([], provider="fake", model="fake", profile_digest="0" * 64)

    with pytest.raises(BenchmarkError):
        runner.run(
            [BenchmarkCase(str(i), "ok") for i in range(101)],
            provider="fake",
            model="fake",
            profile_digest="0" * 64,
        )


def test_benchmark_schema_exists():
    assert (
        Path(__file__).resolve().parents[1]
        / "schemas"
        / "benchmark-report-v1.json"
    ).exists()
