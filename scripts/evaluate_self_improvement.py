#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pasi.core.human_test_store import HumanTestStore
from pasi.core.human_testing import (
    CodeChangeCandidate,
    HumanTestStatus,
    SelfImprovementGate,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=".runtime/acceptance/human-tests.db")
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--human-test-run", required=True)
    parser.add_argument("--ci-passed", action="store_true")
    args = parser.parse_args()

    store = HumanTestStore(args.db)
    certificate = store.get_trust()
    if certificate is None:
        print(json.dumps({"status": "BLOCKED", "reason": "no-trust-certificate"}))
        return 2

    run = store.get_run(args.human_test_run)
    decision = SelfImprovementGate().evaluate(
        certificate,
        CodeChangeCandidate(
            candidate_sha=args.candidate_sha,
            ci_passed=args.ci_passed,
            human_test_run_id=run.run_id,
            human_test_status=run.status,
            human_test_code_head=run.code_head,
            policy_violations=run.policy_violations,
        ),
    )
    print(json.dumps(decision.to_dict(), sort_keys=True))
    return 0 if decision.status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
