# PASI ChatGPT Handoff

This is the authoritative PASI browser extension for the `PASI-Engineering-Workspace` repository.

It is independent of the abandoned `personal-ai-system` repository and has no dependency on its Chromium/controller code.

## Load unpacked

From WSL:

```bash
cd ~/workspace/personal-ai-system/PASI-Engineering-Workspace
wslpath -w extensions/pasi-chatgpt
```

Use the resulting Windows path in the browser's **Load unpacked** control.

## Runtime boundary

The extension observes the authenticated `chatgpt.com` page, tracks the active operation, records a bounded checkpoint, reacts to browser online/offline transitions, and sends structured events to the local PASI capture bridge at:

```text
http://127.0.0.1:8765
```

The extension does not depend on the abandoned `personal-ai-system` project.

## M0 capture

Run the local capture bridge from the repository root:

```bash
python scripts/run_m0_live_capture.py
```

The bridge writes:

```text
.runtime/acceptance/m0-authenticated-response.json
```

when it receives a complete authenticated M0 response plus the required runtime evidence.

The existing M0 acceptance harness then consumes that file:

```bash
python scripts/run_m0_live_acceptance.py \
  --response .runtime/acceptance/m0-authenticated-response.json \
  --roadmap roadmap/p0-p4.json \
  --progression-state .runtime/acceptance/task-progression.json
```

The extension is deliberately conservative about ChatGPT UI details. It uses stable accessibility labels and the `data-message-author-role` attribute where available, and reports what it can observe instead of fabricating evidence.

## Automatic prompt progression

After prior chat usage, a fresh ChatGPT conversation is detected by the extension. The bridge then provides the current durable roadmap prompt and the extension injects it exactly once for that prompt generation.

When an accepted task response completes, the bridge runs the authoritative acceptance harness. Only a successful harness result advances the durable progression state. The extension then creates a fresh chat and injects the newly derived prompt.

A connection loss does not advance progression. The current operation keeps its operation ID and checkpoint and may resume once after reconnect.

## Automatic run loop

After the extension loads on an authenticated empty ChatGPT conversation, it performs a one-time runtime bootstrap, verifies Thinking, creates a fresh task conversation, and injects the current task prompt from the GitHub issue plan.

Task order is deterministic: backend phase P0 through P22, with the matching FE-P0 through FE-P22 issue tasks immediately after each backend phase. The order is derived from the issue plan at runtime; issue number order is not used as the execution order.

When a task response is complete, PASI requires the task marker, completion evidence, and unified patch. The authoritative task acceptance path applies the patch in an isolated worktree, runs `scripts/check_all.sh`, commits only after validation passes, fast-forwards the authoritative worktree, records durable evidence, and only then advances the task prompt.

If ChatGPT reports that the current conversation has reached its usage/context limit, PASI stops the active response when necessary, creates a fresh chat, and resubmits the same current task. This does not advance progression.

If ChatGPT reports a connection/network generation failure, PASI stops the active response, preserves the latest checkpoint/output, and submits a recovery prompt containing the most recent captured assistant output and the same task identity. Recovery never advances the task.

The extension requires Thinking to be enabled before a task or recovery prompt is submitted. It attempts to enable the Thinking control automatically when the UI exposes it; otherwise it refuses to send the task and reports a runtime error instead of silently running with the wrong mode.
