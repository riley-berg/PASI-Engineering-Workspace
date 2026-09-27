# PASI Human Test Harness

The human-test extension automates the operator-facing browser acceptance step with a typed action language. It is intentionally not an autonomous coding agent.

## Safety boundary

The extension can only execute these actions:

- navigate within the suite origin allowlist
- click
- fill
- assert visible/text/URL
- wait
- reload
- capture a screenshot hash
- GET JSON from an allowlisted origin and assert a JSON value

It cannot execute arbitrary JavaScript supplied by a test manifest, cannot submit model prompts, and cannot grant itself trust.

## Live P0 smoke

1. Load `extensions/pasi-human-tester` as an unpacked MV3 extension.
2. Open the real PASI frontend/runtime target in the active tab.
3. Enter its origin and the exact Git commit SHA being tested.
4. Enter the separate human-test ingest token.
5. Run the packaged `pasi-p0-runtime-smoke` suite.

The suite deliberately fails when the real target does not expose the expected health/UI contract. A passing CI fixture is not substituted for a real browser run.

## Trust qualification

Trust is issued outside the extension after durable evidence exists.

Default qualification policy:
- 20 successful runs
- at least 3 distinct code heads
- at least 3 successful negative-control runs
- zero policy violations
- last 5 runs successful

Run:

```bash
python3 scripts/evaluate_human_test_trust.py
```

The script exits 0 only when the durable qualification policy is satisfied.

## Self-improvement gate

A code-change candidate is eligible for autonomous promotion only when the trust certificate is already `trusted`, repository CI passes, the human-test run passes, the human-test run was executed against the exact candidate SHA, and no policy violations were recorded.

```bash
python3 scripts/evaluate_self_improvement.py   --candidate-sha <40-char-sha>   --human-test-run <run-id>   --ci-passed
```

The gate does not merge or deploy code. It emits an auditable PASS/BLOCKED decision for a later promotion controller.

## Revised 168-hour run

The revised Long-run branch is separate until its live smoke has been executed. Do not mark P0.10 complete from static tests alone.
