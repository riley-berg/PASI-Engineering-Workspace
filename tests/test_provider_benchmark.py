import json
from pathlib import Path

import pytest

from pasi.providers.benchmark import BenchmarkError, BenchmarkRunner, load_suite
from pasi.providers.protocol import ProviderResponse


class FakeProvider:
    name = "fake"

    def __init__(self, mode: str = "pass") -> None:
        self.mode = mode

    def health(self):
        return {"available": True, "models": [{"name": "fake-model"}]}

    def generate(self, messages, *, model=None):
        prompt = messages[0].content
        if self.mode == "provider_error":
            raise RuntimeError("provider exploded")
        if self.mode == "partial" and "bounded coding" in prompt:
            text = "not-json"
        else:
            text = '{"status":"ok","result":"ready"}'
        return ProviderResponse(
            provider=self.name,
            model=model or "fake-model",
            text=text,
            latency_ms=1.0,
        )


def test_benchmark_suite_loads_and_rejects_duplicates(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    cases = load_suite(root / "benchmarks" / "pasi-provider-benchmark-v1.json")
    assert [case.case_id for case in cases] == [
        "structured-json-contract",
        "bounded-coding-response",
    ]

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        json.dumps(
            {
                "version": 1,
                "name": "duplicate",
                "cases": [
                    {
                        "case_id": "same",
                        "prompt": "prompt",
                        "checks": ["contains:ok"],
                    },
                    {
                        "case_id": "same",
                        "prompt": "prompt",
                        "checks": ["contains:ok"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(BenchmarkError):
        load_suite(duplicate)


def test_benchmark_all_pass_has_complete_report_and_stable_digest(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    cases = load_suite(root / "benchmarks" / "pasi-provider-benchmark-v1.json")
    runner = BenchmarkRunner()

    first = runner.run(
        cases=cases,
        provider=FakeProvider(),
        model="fake-model",
        profile_name="test-profile",
        profile_digest="1" * 64,
        registry_digest="2" * 64,
    )
    second = runner.run(
        cases=cases,
        provider=FakeProvider(),
        model="fake-model",
        profile_name="test-profile",
        profile_digest="1" * 64,
        registry_digest="2" * 64,
    )

    assert first.passed is True
    assert first.partial_failure is False
    assert first.passed_count == 2
    assert first.failed_count == 0
    assert first.report_digest == second.report_digest

    report_path = tmp_path / "report.json"
    runner.write_report(report_path, first)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["report_digest"] == first.report_digest


def test_benchmark_partial_failure_is_explicit():
    root = Path(__file__).resolve().parents[1]
    cases = load_suite(root / "benchmarks" / "pasi-provider-benchmark-v1.json")
    report = BenchmarkRunner().run(
        cases=cases,
        provider=FakeProvider(mode="partial"),
        model="fake-model",
        profile_name="test-profile",
        profile_digest="1" * 64,
        registry_digest="2" * 64,
    )

    assert report.passed is False
    assert report.partial_failure is True
    assert report.passed_count == 1
    assert report.failed_count == 1
    assert {result.classification for result in report.results} == {
        "pass",
        "contract_failure",
    }


def test_benchmark_provider_error_is_not_a_pass():
    root = Path(__file__).resolve().parents[1]
    cases = load_suite(root / "benchmarks" / "pasi-provider-benchmark-v1.json")
    report = BenchmarkRunner().run(
        cases=cases,
        provider=FakeProvider(mode="provider_error"),
        model="fake-model",
        profile_name="test-profile",
        profile_digest="1" * 64,
        registry_digest="2" * 64,
    )
    assert report.passed is False
    assert all(result.classification == "provider_error" for result in report.results)


def test_benchmark_schema_is_present():
    root = Path(__file__).resolve().parents[1]
    schema = json.loads(
        (root / "schemas" / "provider-benchmark-v1.json").read_text(encoding="utf-8")
    )
    assert schema["properties"]["suite_version"]["const"] == 1
    assert "report_digest" in schema["required"]
