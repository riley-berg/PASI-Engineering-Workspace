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

The M0 harness owns patch application, canonical validation, commit creation, clean-worktree verification, and durable evidence creation. The model response must not claim those harness-side actions already occurred.
