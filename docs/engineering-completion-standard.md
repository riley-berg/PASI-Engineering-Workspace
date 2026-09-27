# PASI engineering completion standard

A roadmap task is not complete merely because its generated code compiles or its unit tests pass.

## Required completion evidence

Every task must satisfy the requirements that apply to its scope:

1. **Implemented primary path**
   - The capability is integrated into the real execution path used by the repository.
   - No placeholder implementation, dead feature branch, mock-only success path, or manual source patch is required for normal use.
   - The changed code has a documented entry point or reproducible invocation.

2. **Functional vertical proof**
   - At least one deterministic test exercises the capability end-to-end across its real internal boundaries.
   - When an external dependency exists, the test must exercise the repository's actual adapter/protocol boundary rather than replacing the entire feature with a fake.
   - The proof must verify the intended observable outcome, not merely that a function returned without raising.

3. **Negative and recovery behavior**
   - Invalid input, failure, retry, interruption, or recovery behavior is covered when that behavior is part of the capability's contract.
   - Terminal failure states must be explicit and must not masquerade as success.
   - State identity and evidence must survive bounded recovery where the roadmap task requires continuity.

4. **Durable evidence**
   - The task produces machine-readable evidence for its acceptance-critical result.
   - Evidence includes the relevant implementation/version identity, timestamps or execution identity where applicable, and the checks that produced the result.
   - Independent verification is used when the task's result is security-, integrity-, or progression-critical.

5. **Reproducibility**
   - A clean checkout can execute the documented validation without hand-editing generated state.
   - Setup is bounded to documented dependencies and commands.
   - Re-running the acceptance path produces the same contract-level result, apart from explicitly nondeterministic identifiers/timestamps.

6. **Operational readiness**
   - The normal path has explicit startup, completion, failure, and recovery semantics.
   - Resource and concurrency limits are defined where the capability can run unattended or concurrently.
   - No hidden background process, local-only manual mutation, or browser/UI manipulation is required unless the task explicitly owns that integration.

7. **Human-effort gate**
   - A task is considered functionally ready only when a human can use the documented entry point with little or no code modification, data repair, or manual orchestration.
   - Human exploratory testing may add confidence, but a missing manual click-through is not the primary evidence of correctness when an equivalent deterministic vertical proof exists.
   - The 168-hour Long-run human soak was explicitly waived by project decision after sustained operation-path proof.

8. **Documentation and handoff**
   - The acceptance command and expected success/failure signals are documented.
   - A future task can consume the task's durable evidence without reconstructing hidden context from chat history.

## Scope-specific additions

**Backend/runtime:** exercise persistence, concurrency, retries, authorization or safety boundaries, and a real integration path where applicable.

**Planner/model work:** prove bounded outputs, identity/provenance, deterministic eligibility, and rejection of unsafe or malformed model output. Model-generated text is never itself acceptance evidence.

**Computer capability work:** prove typed capability invocation, bounded permissions, failure handling, and separation from model inference. DOM extraction cannot substitute for the provider boundary.

**Frontend:** connect to the real contract/API, cover loading/empty/unavailable/error/recovery states, preserve deep links, provide keyboard/accessibility behavior, and exercise the vertical slice against the backend capability. Hard-coded success fixtures are not completion evidence.

**Performance/optimization:** correctness gates must pass first; performance claims require measured benchmark evidence with reproducible workload and environment metadata.

## Closure rule

A roadmap checkbox may be marked complete only when:

- the implementation is integrated,
- the functional vertical proof passes,
- the failure/recovery contract passes where applicable,
- durable evidence exists,
- the reproducible acceptance command succeeds,
- and the result leaves the repository in a usable state for the next task.

“Tests pass” is one input to completion, never the completion criterion by itself.
