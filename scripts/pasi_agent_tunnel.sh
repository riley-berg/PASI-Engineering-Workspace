#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${PASI_AGENT_TUNNEL_PROFILE:-$ROOT/config/pasi-agent.tunnel.yaml.example}"

if [[ -n "${PASI_TUNNEL_CLIENT_BIN:-}" ]]; then
  TUNNEL_CLIENT="$PASI_TUNNEL_CLIENT_BIN"
elif [[ -x "$ROOT/bin/tunnel-client" ]]; then
  TUNNEL_CLIENT="$ROOT/bin/tunnel-client"
elif command -v tunnel-client >/dev/null 2>&1; then
  TUNNEL_CLIENT="$(command -v tunnel-client)"
else
  echo "error: tunnel-client is not installed and no repo-local binary was found" >&2
  echo "Install the official tunnel-client release or set PASI_TUNNEL_CLIENT_BIN=/path/to/tunnel-client" >&2
  exit 127
fi

: "${CONTROL_PLANE_TUNNEL_ID:?CONTROL_PLANE_TUNNEL_ID is required}"
: "${CONTROL_PLANE_API_KEY:?CONTROL_PLANE_API_KEY is required}"

if [[ ! "$CONTROL_PLANE_TUNNEL_ID" =~ ^tunnel_[0-9a-f]{32}$ ]]; then
  echo "error: CONTROL_PLANE_TUNNEL_ID must match tunnel_<32 lowercase hexadecimal characters>" >&2
  exit 2
fi

if [[ ! -x "$TUNNEL_CLIENT" ]]; then
  echo "error: tunnel-client binary is not executable: $TUNNEL_CLIENT" >&2
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
echo "  client:  $TUNNEL_CLIENT"
echo "  server:  $ROOT/scripts/pasi_agent_mcp.py"

exec "$TUNNEL_CLIENT" run --profile-file "$PROFILE"
