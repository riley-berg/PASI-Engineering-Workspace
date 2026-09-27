# PASI Engineering Workspace

A clean, roadmap-driven PASI automation workspace.

## Architecture

Roadmap / Issues
       |
       v
Operation State
       |
       v
Planner
       |
       v
ModelProvider
       |
       +--> PASI local API --> Ollama / local model
       |
       +--> future hosted provider

Computer capabilities remain separate from planning and provider selection.

## Live Engineering Workspace runtime

The 168-hour acceptance runtime is contained in this repository. The older personal-ai-system repository is historical implementation source only; this repository does not import or execute it at runtime.

The native ChatGPT runtime lives under automation/chromium/pasi-chatgpt, with the localhost bridge and provider-neutral ChatGPT adapter under automation/. The single-task acceptance executor is scripts/pasi_engineering_executor.py.

Browser preflight:

    python scripts/pasi_engineering_browser_preflight.py --extension-root "$PWD/automation/chromium/pasi-chatgpt"

Executor command:

    export PASI_ENGINEERING_EXECUTOR_CMD='python scripts/pasi_engineering_executor.py'

Then the P0.4 launcher can execute the canonical P0-P22 roadmap through the Engineering Workspace task boundary.

## Local model API

Set the local model:

    export PASI_OLLAMA_MODEL='nemotron-3-nano:4b'
    python -m pasi.providers.local_api

The PASI API listens on 127.0.0.1:8787 by default.

## Development

    python -m pip install -e '.[dev]'
    python -m pytest -q
    node --test web/app.test.js
