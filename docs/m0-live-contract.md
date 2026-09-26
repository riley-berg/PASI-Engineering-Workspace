# M0 Live Runtime Contract

P0.1 is not complete from a repository patch alone. The authenticated live response must prove the runtime conditions below before the harness may apply, validate, commit, and record evidence.

## Required runtime evidence

The authenticated response JSON must contain `runtime_evidence` with:

- `fresh_chat_created_after_usage: true`
- `thinking_enabled: true`
- `connection_recovery.connection_loss_detected: true`
- `connection_recovery.response_stopped_on_loss: true`
- `connection_recovery.checkpoint_preserved: true`
- `connection_recovery.resumed_after_reconnect: true`
- `connection_recovery.same_operation_resumed: true`
- `connection_recovery.operation_id` identifying the same operation before and after the interruption
- `connection_recovery.resume_phase` identifying the durable point at which work resumes

The recovery state machine is implemented in `src/pasi/core/m0_runtime.py`. A connection loss must stop active generation, preserve the latest checkpoint, and permit exactly one resume of the same operation.

The live handoff must also establish that a new chat is created after the prior chat has been used and that Thinking is enabled for the accepted operation.

The M0 acceptance path owns patch application, canonical validation, commit creation, clean-worktree verification, and durable evidence creation. The model response must not claim those harness-side actions already occurred.
The authenticated response may include `next_task_id` and `next_prompt` for M0 transport compatibility, but those values are not authoritative. The durable task/prompt controller derives task order from the PASI Engineering Workspace GitHub issues through `src/pasi/core/github_issue_tasks.py`, using the predeclared phase order P0 through P22 with the corresponding FE phase after each backend phase. The local `roadmap/p0-p4.json` file remains the M0 fallback/schema source only.

A connection loss must not advance the task. The controller resumes the exact persisted current prompt and task identity, then permits advancement only after verified completion.
