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

```
http://127.0.0.1:8765
```

The extension does not depend on the abandoned `personal-ai-system` project.

## M0 capture

Run the local capture bridge from the repository root:

```bash
python scripts/run_m0_live_capture.py
```

The bridge writes:

```
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
