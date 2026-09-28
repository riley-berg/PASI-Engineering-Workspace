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

The native PASI ChatGPT Handoff extension lives under extensions/pasi-chatgpt, with the localhost bridge and provider-neutral ChatGPT adapter under automation/. The single-task acceptance executor is scripts/pasi_engineering_executor.py.

Browser preflight:

    python scripts/pasi_engineering_browser_preflight.py --extension-root "$PWD/extensions/pasi-chatgpt"

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

## GitHub Projects v2 automation

The roadmap Project v2 board can be inspected and updated through the GraphQL wrapper:

    export PASI_PROJECTS_TOKEN="..."
    python scripts/github_project_v2.py --owner th3-st0v3 --owner-type user --project-number 1 inspect

Controlled field updates are available through .github/workflows/github-project-v2.yml. See docs/operations/github-project-v2.md.

Branch cleanup and repository security are automated through hosted GitHub Actions in .github/workflows/branch-hygiene.yml and .github/workflows/pasi-security-analysis.yml.
