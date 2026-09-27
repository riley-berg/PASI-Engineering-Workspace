#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

test "$(basename "$REPO_ROOT")" = "PASI-Engineering-Workspace" || {
  echo "error: run this from the PASI-Engineering-Workspace checkout" >&2
  exit 2
}

: "${PASI_GITHUB_TOKEN:?set PASI_GITHUB_TOKEN to a GitHub token with issue-write access}"
: "${PASI_ENGINEERING_EXECUTOR_CMD:?set PASI_ENGINEERING_EXECUTOR_CMD to the live Engineering Workspace executor/controller command}"

export PASI_PUSH=1
export PASI_TASK_TIMEOUT_SECONDS="${PASI_TASK_TIMEOUT_SECONDS:-1800}"

python scripts/pasi_168h_acceptance.py   --hours 168   --worktree "${PASI_168H_WORKTREE:-$HOME/.pasi-worktrees/pasi-engineering-workspace-168h}"   --branch "${PASI_168H_BRANCH:-pasi/p0-4-168h-run-$(date +%Y%m%d-%H%M%S)}"
