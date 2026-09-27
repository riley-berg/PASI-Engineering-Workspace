# PASI runtime dashboard

## Run

From the repository root:

```bash
python3 scripts/serve_runtime_dashboard.py
```

The server binds to loopback at `http://127.0.0.1:8790/` and serves the frontend and runtime API from the same origin.

An ephemeral control token is generated when `PASI_RUNTIME_TOKEN` is not set and printed to the terminal. Enter that token in the dashboard to authorize start/stop/retry/recover actions.

Open an operation directly with:

```
http://127.0.0.1:8790/?operation_id=<operation-id>
```

## State contract

The dashboard is read-only until an operation is loaded. Health, operation projection, event history, evidence references, recovery history, runtime identity, and long-run metadata are read from the authoritative runtime API.

Acceptance cells remain **not reported** when the backend has not provided acceptance evidence. The frontend does not manufacture green states.

Mutating controls send the current operation revision and a fresh idempotency key. The backend remains authoritative for authorization, revision conflicts, and lifecycle transitions.

## Verification

Frontend contract tests:

```bash
node --test web/app.test.js
```

Repository tests continue to run through the normal GitHub Actions workflow.
