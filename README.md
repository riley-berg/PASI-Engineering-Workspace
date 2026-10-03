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


## Live runner diagnostics

After the Engineering Workspace bridge is running, the read-only live runner diagnostic surface is available on the same localhost bridge:

    http://127.0.0.1:8765/runner/diagnostics

It exposes sanitized bridge process data, detected PASI runner processes, profile-isolated M1/168h state, and current browser health. It does not expose the bridge token.


## PASI Agent Interface

PASI exposes a read-only observation interface for AI agents through the Model Context Protocol (MCP). The interface deliberately separates observation authority from the privileged runner/control bridge.

Install the optional MCP dependency:

```bash
python -m pip install -e '.[agent]'
```

Launch the local stdio MCP server:

```bash
python scripts/pasi_agent_mcp.py
```

The server exposes exactly these seven read-only tools:

- `pasi.get_runner_state`
- `pasi.get_process_state`
- `pasi.get_browser_state`
- `pasi.get_extension_state`
- `pasi.get_operation_state`
- `pasi.get_environment_state`
- `pasi.get_acceptance_evidence`

The source of truth for their JSON Schema 2020-12 input/output contracts is `automation/pasi_agent_contracts.py`. Tool results use the `pasi-agent-v1` envelope:

```json
{
  "schema_version": "pasi-agent-v1",
  "request_id": "req-...",
  "ok": true,
  "observed_at": "2026-10-02T21:15:03.412Z",
  "data": {},
  "error": null
}
```

Observation failures use the same envelope with `ok=false`, `data=null`, and a structured error containing `code`, `message`, `retryable`, `source`, and `details`.

The interface is observation-only. It never receives or returns the bridge token and does not expose runner-control, queue mutation, or chat mutation operations.

### Browser testing through PASI MCP

The PASI Agent MCP app also exposes four strictly observational browser-testing tools. They reuse the extension's existing native CDP authority and do not add click, typing, navigation, runner control, queue mutation, or arbitrary JavaScript execution.

- `pasi.get_browser_screenshot` — captures the current visible tab.
- `pasi.get_browser_dom` — inspects bounded DOM metadata and computed visual properties for a CSS selector.
- `pasi.get_browser_console_errors` — reads recent console errors and uncaught exceptions.
- `pasi.get_browser_network` — reads recent sanitized network request/response/failure events.

The MCP server requests these captures through the authenticated localhost bridge. The extension performs the CDP operation against its already-attached ChatGPT tab, then returns the read-only result to the MCP caller.

The Streamable HTTP application is also available from `automation.pasi_agent_mcp.create_streamable_http_app()`, but it is not started or exposed by the default launcher. Local deployment should use an explicit authenticated transport or native host boundary rather than creating an unauthenticated network listener.
