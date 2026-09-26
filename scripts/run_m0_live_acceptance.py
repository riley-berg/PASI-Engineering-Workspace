from __future__ import annotations

import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from pasi.core.m0_acceptance import apply_validate_commit, parse_response_file


REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply and verify one authenticated ChatGPT M0 response in an isolated worktree."
    )
    parser.add_argument("--response", type=Path, required=True, help="Path to the authenticated ChatGPT JSON response.")
    parser.add_argument("--base-ref", default="HEAD", help="Git ref from which to create the acceptance worktree.")
    parser.add_argument("--worktree", type=Path, default=None, help="Optional dedicated acceptance worktree.")
    args = parser.parse_args()

    response = parse_response_file(args.response)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    worktree = (
        args.worktree.expanduser().resolve()
        if args.worktree is not None
        else (REPO_ROOT / ".runtime" / f"m0-worktree-{stamp}").resolve()
    )
    branch = f"pasi/m0-acceptance-{stamp}"

    worktree.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "worktree", "add", "--quiet", "-b", branch, str(worktree), args.base_ref],
            cwd=REPO_ROOT,
            check=True,
        )
        commit, evidence = apply_validate_commit(
            repo=worktree,
            response=response,
            branch_name=branch,
        )
        print(
            "M0 PASS: authenticated response -> contract parsing -> patch application -> "
            "canonical validation -> commit -> clean worktree"
        )
        print(f"task_id={response.task_id}")
        print(f"commit={commit}")
        print(f"evidence={evidence}")
        if response.next_task_id and response.next_prompt:
            print(f"next_task={response.next_task_id}")
            print("next_prompt=advanced only after verified completion")
        return 0
    finally:
        if worktree.exists():
            subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=REPO_ROOT, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
