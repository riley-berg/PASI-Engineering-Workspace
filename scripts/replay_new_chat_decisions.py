#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from scripts.pasi_chat import replay_new_chat_decisions


def main() -> int:
    parser=argparse.ArgumentParser(description="Replay-validate durable PASI new-chat decision records.")
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        help="JSONL decision log; defaults to PASI_ENGINEERING_RUNTIME_DIR/new-chat-decisions.jsonl",
    )
    parser.add_argument(
        "--public-key",
        type=Path,
        help="Ed25519 public key PEM; defaults to the PASI audit key",
    )
    args=parser.parse_args()
    result=replay_new_chat_decisions(args.path, public_key_path=args.public_key)

    status="VALID" if result["valid"] else "INVALID"
    print(f"{status}: {result['records']} new-chat decision record(s) checked")
    print(f"Decision log: {result['path']}")
    print(f"Chain head: {result['chain_head']}")
    for error in result["errors"]:
        print(f"ERROR: {error}")
    return 0 if result["valid"] else 1


if __name__=="__main__":
    raise SystemExit(main())
