from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from pasi.core.m0_acceptance import apply_validate_commit, parse_response_file
from pasi.core.task_progression import RoadmapTaskCatalog, TaskPromptProgression, TaskProgressionError


def persist_evidence(source: Path, destination: Path) -> Path:
    """Copy acceptance evidence out of the temporary worktree before cleanup."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination



def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply and verify one authenticated ChatGPT M0 response in an isolated worktree."
    )
    parser.add_argument("--response", type=Path, required=True, help="Path to the authenticated ChatGPT JSON response.")
    parser.add_argument("--base-ref", default="HEAD", help="Git ref from which to create the acceptance worktree.")
    parser.add_argument("--worktree", type=Path, default=None, help="Optional dedicated acceptance worktree.")
    parser.add_argument("--roadmap", type=Path, default=REPO_ROOT / "roadmap" / "p0-p4.json", help="Canonical roadmap JSON.")
    parser.add_argument("--progression-state", type=Path, default=REPO_ROOT / ".runtime" / "acceptance" / "task-progression.json", help="Durable task/prompt progression state.")
    args = parser.parse_args()

    response = parse_response_file(args.response)
    catalog = RoadmapTaskCatalog.from_roadmap_file(args.roadmap.expanduser().resolve())
    progression_path = args.progression_state.expanduser().resolve()
    if progression_path.exists():
        progression = TaskPromptProgression.load(catalog=catalog, path=progression_path)
    else:
        progression = TaskPromptProgression.start(catalog=catalog, task_id=response.task_id)
    if progression.state.current_task_id != response.task_id:
        raise TaskProgressionError("live response task does not match durable current task")
    expected_next_task_id, expected_next_prompt = progression.expected_next()
    if response.next_task_id != expected_next_task_id or response.next_prompt != expected_next_prompt:
        raise TaskProgressionError("live response next task/prompt does not match the canonical roadmap")
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
        progression, receipt = progression.complete(
            verified_task_id=response.task_id,
            evidence=f"{response.evidence}\ncommit={commit}\nclean_worktree=true",
        )
        progression.save(progression_path)
        print(
            "M0 PASS: authenticated response -> contract parsing -> patch application -> "
            "canonical validation -> commit -> clean worktree"
        )
        print(f"task_id={response.task_id}")
        print(f"commit={commit}")
        persistent_evidence = persist_evidence(
            evidence,
            REPO_ROOT / ".runtime" / "acceptance" / "m0-live.json",
        )
        print(f"evidence={persistent_evidence}")
        if receipt.next_task_id and receipt.next_prompt:
            print(f"next_task={receipt.next_task_id}")
            print("next_prompt=derived from canonical roadmap after verified completion")
        print(f"progression={progression_path}")
        return 0
    finally:
        if worktree.exists():
            subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=REPO_ROOT, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
