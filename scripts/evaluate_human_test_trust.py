#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os

from pasi.core.human_test_store import HumanTestStore
from pasi.core.human_testing import HumanTestTrustPolicy


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=".runtime/acceptance/human-tests.db")
    parser.add_argument("--min-runs", type=int, default=20)
    parser.add_argument("--min-code-heads", type=int, default=3)
    parser.add_argument("--min-negative-controls", type=int, default=3)
    parser.add_argument("--trailing-successes", type=int, default=5)
    args = parser.parse_args()

    secret = os.environ.get("PASI_HUMAN_TEST_TRUST_SECRET", "")
    if not secret:
        parser.error("PASI_HUMAN_TEST_TRUST_SECRET is required to issue trusted state")
    store = HumanTestStore(args.db)
    certificate = store.evaluate_and_store_trust(
        policy=HumanTestTrustPolicy(
            minimum_successful_runs=args.min_runs,
            minimum_distinct_code_heads=args.min_code_heads,
            minimum_negative_controls=args.min_negative_controls,
            required_trailing_successes=args.trailing_successes,
        ),
        issuer_secret=secret,
    )
    print(certificate.to_dict())
    return 0 if certificate.status.value == "trusted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
