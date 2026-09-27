#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

test "$(basename "$REPO_ROOT")" = "PASI-Engineering-Workspace" || {
  echo "error: run this from the PASI-Engineering-Workspace checkout" >&2
  exit 2
}

: "${PASI_GITHUB_TOKEN:?set PASI_GITHUB_TOKEN to a GitHub token with issue-write access}"
export PASI_PUSH=1
export PASI_ENGINEERING_EXECUTOR_CMD="${PASI_ENGINEERING_EXECUTOR_CMD:-python scripts/pasi_engineering_executor.py}"
export PASI_TASK_TIMEOUT_SECONDS="${PASI_TASK_TIMEOUT_SECONDS:-1800}"

WORKTREE="${PASI_168H_WORKTREE:-$HOME/.pasi-worktrees/pasi-engineering-workspace-168h}"
BRANCH="${PASI_168H_BRANCH:-pasi/p0-4-168h-run-$(date +%Y%m%d-%H%M%S)}"
RUNTIME_DIR="${PASI_ACCEPTANCE_STATE_DIR:-$HOME/.pasi/engineering-workspace-168h}"
PID_FILE="$RUNTIME_DIR/supervisor.pid"
MAX_RESTARTS="${PASI_168H_MAX_RESTARTS:-64}"
BASE_BACKOFF="${PASI_168H_RESTART_BACKOFF_SECONDS:-5}"
MAX_BACKOFF="${PASI_168H_MAX_RESTART_BACKOFF_SECONDS:-120}"

mkdir -p "$RUNTIME_DIR"
if [[ -f "$PID_FILE" ]]; then
  existing="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ "$existing" =~ ^[0-9]+$ ]] && kill -0 "$existing" 2>/dev/null && [[ "$existing" != "$$" ]]; then
    echo "PASI 168-hour supervisor already active (PID $existing)." >&2
    exit 0
  fi
fi
printf '%s\n' "$$" > "$PID_FILE"

cleanup() {
  if [[ -f "$PID_FILE" ]] && [[ "$(cat "$PID_FILE" 2>/dev/null || true)" == "$$" ]]; then
    rm -f "$PID_FILE"
  fi
}
trap cleanup EXIT

read_status() {
  "$REPO_ROOT/.venv/bin/python" - "$RUNTIME_DIR/state.json" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
try:
    value = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    print("")
    raise SystemExit(0)
print(str(value.get("status", "")))
PY
}

restart_count=0
backoff="$BASE_BACKOFF"

while true; do
  set +e
  python scripts/pasi_168h_acceptance.py --hours 168 --worktree "$WORKTREE" --branch "$BRANCH"
  rc=$?
  set -e

  status="$(read_status)"
  case "$status" in
    deadline_reached|roadmap_complete)
      exit 0
      ;;
  esac

  if [[ "$status" != "running" ]]; then
    exit "$rc"
  fi

  restart_count=$((restart_count + 1))
  if (( restart_count > MAX_RESTARTS )); then
    echo "PASI 168-hour supervisor restart budget exhausted after $MAX_RESTARTS restarts." >&2
    exit 1
  fi

  echo "[PASI 168h supervisor] acceptance process exited code=$rc; preserving run state and restarting in ${backoff}s." >&2
  sleep "$backoff"
  next=$((backoff * 2))
  if (( next > MAX_BACKOFF )); then
    backoff="$MAX_BACKOFF"
  else
    backoff="$next"
  fi
done
