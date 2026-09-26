# PASI Engineering Workspace automation rules

## Canonical project

This repository, `th3-st0v3/PASI-Engineering-Workspace`, is the authoritative PASI automation workspace.

Do not modify, import from, or rebase automation work onto the abandoned `personal-ai-system` repository. The browser extension and automation code for this project live under `extensions/pasi-chatgpt/` and the Python control-plane code lives under `src/pasi/` and `scripts/`.

## Chat automation invariants

- Reuse the durable PASI automation ChatGPT conversation.
- If the browser is on another ChatGPT conversation, switch back to the stored automation conversation rather than creating a new one.
- A fresh ChatGPT conversation may be created only after an explicit ChatGPT usage/context limit is detected on the automation conversation.
- Never create a fresh chat merely because a task completed, the current chat contains prior messages, or Thinking is currently off.
- Before usage-gated fresh-chat recovery, verify the desired Thinking state. After the new chat exists, verify Thinking again before injecting the task.
- A fresh-chat event must record `fresh_chat_creation_reason=usage_limit`; other fresh-chat reasons are invalid.

## Prompt progression invariants

- The current task prompt is immutable while the task is incomplete.
- Advance the durable task state exactly once, and only after the authoritative acceptance harness verifies completion.
- Derive the next prompt after verification; never precompute the next task prompt inside the current task's completion contract.
- Each next prompt must be task-specific and include a bounded handoff from the verified prior task so prompts evolve with real progress rather than repeating a static prompt.
- Connection-loss recovery preserves the same task and operation identity and does not advance progression.

## Evidence and completion

A completion claim is not authoritative until the repository acceptance harness has validated the response, patch, and evidence. Browser observations are evidence inputs, not permission to fabricate commits or completion state.
