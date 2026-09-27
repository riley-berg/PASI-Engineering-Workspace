# Single PASI runtime and extension deployment

## One source tree

The runtime and browser extension now live in the same source tree:

- Engineering Workspace: C:\PASI\PASI-Engineering-Workspace
- WSL view of the same directory: /mnt/c/PASI/PASI-Engineering-Workspace
- Opera GX unpacked extension: C:\PASI\pasi-chatgpt-unpacked

The browser extension is never loaded from the old WSL checkout.

## Runtime topology

PASI-Engineering-Workspace
        |
        +--> pasi_bridge/                 <-- bridge implementation
        |
        +--> extensions/pasi-chatgpt/     <-- extension source
        |       |
        |       +--> .bridge-token        <-- generated, ignored
        |
        +--> .runtime/bridge-token        <-- same generated token
        |
        +--> scripts/run_bridge.sh
        |
        +--> scripts/deploy_extension.ps1
        |
        v
C:\PASI\pasi-chatgpt-unpacked
        |
        v
Opera GX -> ChatGPT
        |
        | HTTP + Authorization
        v
127.0.0.1:8765
        |
        v
WSL: python3 -m pasi_bridge

The bridge implementation is no longer sourced from ~/workspace/personal-ai-system.

## Build and deploy from Windows

Use the Engineering Workspace checkout:

    Set-Location C:\PASI\PASI-Engineering-Workspace
    npm.cmd run deploy:extension

This performs dependency setup, builds the userscript compiler, provisions the repository-local bridge token, compiles the bridge Python source, validates the MV3 extension, and mirrors extensions\pasi-chatgpt\ to C:\PASI\pasi-chatgpt-unpacked.

The deployment metadata records the exact Engineering Workspace commit.

A repeat deployment after dependencies are installed can use:

    powershell -ExecutionPolicy Bypass -File .\scripts\deploy_extension.ps1 -SkipInstall

## Run the bridge from WSL

WSL must use the same checkout that Windows just deployed:

    cd /mnt/c/PASI/PASI-Engineering-Workspace
    bash scripts/run_bridge.sh

That launcher:

1. creates or reuses .runtime/bridge-token;
2. synchronizes the same token to extensions/pasi-chatgpt/.bridge-token;
3. runs python3 -m pasi_bridge.

The bridge listens only on 127.0.0.1:8765.

Do not start another bridge from ~/workspace/personal-ai-system.

## Verify the bridge

From WSL:

    cd /mnt/c/PASI/PASI-Engineering-Workspace
    python3 scripts/verify_bridge.py

This verifies:

- the repository-local runtime token exists;
- the extension token and runtime token are identical;
- the source extension is Manifest V3;
- the bridge is reachable and healthy;
- the reported source commit is the current Engineering Workspace commit.

## Verify the deployed Opera extension

From PowerShell:

    Set-Location C:\PASI\PASI-Engineering-Workspace
    powershell -ExecutionPolicy Bypass -File .\scripts\verify_extension_deployment.ps1

This additionally verifies that:

- C:\PASI\pasi-chatgpt-unpacked exists;
- the deployed commit equals the current Engineering Workspace commit;
- runtime/source/deployed bridge tokens all match;
- native controller assets are present;
- 127.0.0.1:8765/health is reachable with the shared token.

## Load the single extension in Opera GX

Open opera://extensions

Enable Developer Mode and load only:

C:\PASI\pasi-chatgpt-unpacked

After each deployment, click Reload on that extension.

Do not load:
/home/riley/workspace/personal-ai-system/automation/chromium/pasi-chatgpt

That is no longer a browser deployment target.

## Daily workflow

### Windows

    Set-Location C:\PASI\PASI-Engineering-Workspace
    git pull --ff-only origin pasi/single-extension-deployment-20260926
    npm.cmd run deploy:extension
    powershell -ExecutionPolicy Bypass -File .\scripts\verify_extension_deployment.ps1

### WSL

    cd /mnt/c/PASI/PASI-Engineering-Workspace
    bash scripts/run_bridge.sh

### Opera GX

Reload the single unpacked extension.

This gives Windows build/deploy, WSL bridge execution, and Opera GX browser automation one Engineering Workspace source tree.

## Acceptance gate

Do not start the 168-hour benchmark until the shared runtime is verified, the automated test suite is green, M0 is live-passed, and M1 uses completion-gated dynamic next-prompt generation rather than pre-seeding all twenty prompts.
