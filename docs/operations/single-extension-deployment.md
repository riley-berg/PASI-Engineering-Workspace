# Single PASI ChatGPT extension deployment

## Source of truth

The only extension source is:

extensions/pasi-chatgpt/ in PASI-Engineering-Workspace.

The only unpacked extension loaded by Opera GX is:

C:\PASI\pasi-chatgpt-unpacked

Do not load or manually edit another PASI extension directory.

## Runtime topology

The runtime is intentionally split across Windows and WSL, but it is one runtime:

PASI-Engineering-Workspace
        |
        | build + deploy
        v
C:\PASI\pasi-chatgpt-unpacked
        |
        | Opera GX / ChatGPT
        | HTTP -> 127.0.0.1:8765
        v
WSL PASI bridge
        |
        v
durable PASI operation state / acceptance runtime

The browser extension already uses the canonical bridge URL http://127.0.0.1:8765.
There must be exactly one live bridge on that port.

## One-time Windows setup

Install Node.js for Windows so npm.cmd and node.exe are available.

Then clone/checkout the Engineering Workspace at a Windows path, for example:

powershell
Set-Location C:\PASI
git clone https://github.com/th3-st0v3/PASI-Engineering-Workspace.git PASI-Engineering-Workspace
Set-Location C:\PASI\PASI-Engineering-Workspace
git checkout pasi/single-extension-deployment-20260926

The same directory is visible from WSL as:

/mnt/c/PASI/PASI-Engineering-Workspace

Do not keep a second Engineering Workspace checkout in WSL.

## Build and deploy

From PowerShell in the Windows checkout:

powershell
Set-Location C:\PASI\PASI-Engineering-Workspace
powershell -ExecutionPolicy Bypass -File .\scripts\deploy_extension.ps1

This:

1. installs dependencies with npm.cmd ci when a lockfile is present;
2. rebuilds the bundled browser TypeScript compiler;
3. validates Manifest V3;
4. verifies the canonical bridge endpoint is present;
5. mirrors extensions\pasi-chatgpt exactly into C:\PASI\pasi-chatgpt-unpacked;
6. writes source-commit metadata into the deployed directory.

A normal repeat deployment can skip dependency installation:

powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy_extension.ps1 -SkipInstall

## Start the WSL bridge

Until the bridge implementation is consolidated into this repository, start the already-proven bridge runtime from the existing PASI runtime checkout:

bash
cd ~/workspace/personal-ai-system
source .venv/bin/activate
env -u PYTHONPATH python -m automation.orchestrator.bridge

Keep exactly one bridge process running on 127.0.0.1:8765.

The next consolidation step is to transfer the bridge implementation into Engineering Workspace and make this the only supported bridge launch location. The browser-facing contract should remain 127.0.0.1:8765 so the extension deployment does not need another round of rewiring.

## Verify Windows can reach the WSL bridge

From PowerShell:

powershell
Test-NetConnection 127.0.0.1 -Port 8765
Invoke-RestMethod http://127.0.0.1:8765/health

The TCP test must report TcpTestSucceeded : True.

If WSL is running but Windows cannot reach the port, verify WSL localhost forwarding/networking before changing the extension URL. Do not replace 127.0.0.1:8765 with a changing WSL IP as a permanent solution.

## Load the deployed extension in Opera GX

In Opera GX:

1. open opera://extensions;
2. enable Developer mode;
3. use Load unpacked once and select:
   C:\PASI\pasi-chatgpt-unpacked;
4. after every deployment, click Reload for that extension.

Never load the WSL copy:

/home/riley/workspace/personal-ai-system/automation/chromium/pasi-chatgpt

That path is no longer a browser deployment target.

## Verify the exact runtime identity

From PowerShell:

powershell
Set-Location C:\PASI\PASI-Engineering-Workspace
powershell -ExecutionPolicy Bypass -File .\scripts\verify_extension_deployment.ps1

The reported deployed source_commit must match the Engineering Workspace commit being tested.

The bridge health check must succeed before running M0/M1.

## WSL and Opera must use the same checkout/runtime contract

WSL must not maintain a second copy of the browser extension.

Use the Windows checkout from WSL when inspecting/building the Engineering Workspace:

bash
cd /mnt/c/PASI/PASI-Engineering-Workspace
git rev-parse HEAD

The browser extension is built/deployed from that same checkout and loaded from:

C:\PASI\pasi-chatgpt-unpacked

The bridge is the only component that remains a long-running WSL process during this phase.

## Acceptance gate before long runs

Do not start the 168-hour benchmark until:

- the bridge responds on 127.0.0.1:8765;
- Opera GX has only the deployed extension loaded;
- the deployed commit matches the Engineering Workspace commit;
- M0 is live-passed;
- required automated tests are green;
- M1 uses completion-gated next-prompt generation rather than pre-seeding all 20 prompts.
