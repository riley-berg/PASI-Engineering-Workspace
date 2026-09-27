#!/usr/bin/env python3
"""Create or synchronize the repository-local PASI bridge token."""

from __future__ import annotations

import os
import secrets
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / ".runtime"
RUNTIME_TOKEN = RUNTIME_DIR / "bridge-token"
EXTENSION_TOKEN = ROOT / "extensions" / "pasi-chatgpt" / ".bridge-token"


def main() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    configured = os.environ.get("PASI_BRIDGE_TOKEN", "").strip()
    if configured:
        token = configured
    elif RUNTIME_TOKEN.exists():
        token = RUNTIME_TOKEN.read_text(encoding="utf-8").strip()
    else:
        token = secrets.token_urlsafe(32)

    if not token:
        raise SystemExit("PASI bridge token cannot be empty")

    RUNTIME_TOKEN.write_text(token + "\n", encoding="utf-8")
    try:
        os.chmod(RUNTIME_TOKEN, 0o600)
    except OSError:
        pass

    EXTENSION_TOKEN.write_text(token + "\n", encoding="utf-8")
    print("PASI bridge token synchronized.")
    print(f"  runtime  : {RUNTIME_TOKEN}")
    print(f"  extension: {EXTENSION_TOKEN}")


if __name__ == "__main__":
    main()
