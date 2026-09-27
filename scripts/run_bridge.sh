#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "$" + "{BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python3 scripts/provision_bridge_token.py
exec python3 -m pasi_bridge
