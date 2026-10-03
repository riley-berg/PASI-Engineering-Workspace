#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${PASI_AGENT_TUNNEL_PROFILE:-$ROOT/config/pasi-agent.tunnel.yaml.example}"

: "${CONTROL_PLANE_TUNNEL_ID:?CONTROL_PLANE_TUNNEL_ID is required}"
: "${CONTROL_PLANE_API_KEY:?CONTROL_PLANE_API_KEY is required}"

if ! command -v tunnel-client >/dev/null 2>&1; then
  echo "error: tunnel-client is not installed or not on PATH" >&2
  exit 127
fi

if [[ ! -f "$PROFILE" ]]; then
  echo "error: PASI tunnel profile not found: $PROFILE" >&2
  exit 1
fi

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "error: PASI virtualenv Python not found: $ROOT/.venv/bin/python" >&2
  echo "Run: python3 -m venv .venv && .venv/bin/python -m pip install -e '.[agent]'" >&2
  exit 1
fi

cd "$ROOT"

echo "Starting PASI AI MCP tunnel"
echo "  profile: $PROFILE"
echo "  tunnel:  $CONTROL_PLANE_TUNNEL_ID"
echo "  server:  $ROOT/scripts/pasi_agent_mcp.py"

exec tunnel-client run --profile-file "$PROFILE"
