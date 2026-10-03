#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


POLICY_RELATIVE = Path(".github/workflows/workflow-policy.json")
WORKFLOW_SUFFIXES = {".yml", ".yaml"}
NAME_RE = re.compile(r"^name:\s*(.+?)\s*$")
JOB_RE = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*$")


def load_policy(root: Path) -> dict:
    path = root / POLICY_RELATIVE
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise RuntimeError("workflow policy schema_version must be 1")
    workflows = payload.get("workflows")
    if not isinstance(workflows, dict) or not workflows:
        raise RuntimeError("workflow policy must define at least one workflow")
    return payload


def workflow_name(text: str, path: Path) -> str:
    for line in text.splitlines():
        match = NAME_RE.match(line)
        if match:
            return match.group(1).strip().strip("'").strip('"')
    raise RuntimeError(f"{path}: missing top-level workflow name")


def job_ids(text: str) -> list[str]:
    jobs: list[str] = []
    in_jobs = False
    for line in text.splitlines():
        if line.startswith("jobs:"):
            in_jobs = True
            continue
        if in_jobs and line and not line[0].isspace():
            break
        if in_jobs:
            match = JOB_RE.match(line)
            if match:
                jobs.append(match.group(1))
    return jobs


def normalized_workflow_hash(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or NAME_RE.match(line):
            continue
        lines.append(stripped)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def verify(root: Path) -> dict:
    root = root.expanduser().resolve()
    workflow_dir = root / ".github" / "workflows"
    policy = load_policy(root)
    expected = policy["workflows"]

    actual_paths = sorted(
        path.name
        for path in workflow_dir.iterdir()
        if path.is_file() and path.suffix in WORKFLOW_SUFFIXES
    )
    expected_paths = sorted(expected.keys())
    errors: list[str] = []

    if actual_paths != expected_paths:
        missing = sorted(set(expected_paths) - set(actual_paths))
        unexpected = sorted(set(actual_paths) - set(expected_paths))
        if missing:
            errors.append("policy-listed workflow(s) missing: " + ", ".join(missing))
        if unexpected:
            errors.append(
                "unlisted workflow(s) present; add an intentional durable workflow "
                "to workflow-policy.json before introducing it: " + ", ".join(unexpected)
            )

    names: dict[str, str] = {}
    hashes: dict[str, str] = {}
    roles: dict[str, str] = {}

    for filename, metadata in expected.items():
        path = workflow_dir / filename
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        actual_name = workflow_name(text, path)
        expected_name = str(metadata.get("name") or "").strip()
        role = str(metadata.get("role") or "").strip()

        if actual_name != expected_name:
            errors.append(
                f"{filename}: workflow name {actual_name!r} does not match policy {expected_name!r}"
            )
        if actual_name in names:
            errors.append(
                f"duplicate workflow name {actual_name!r}: {names[actual_name]} and {filename}"
            )
        else:
            names[actual_name] = filename

        if not role:
            errors.append(f"{filename}: policy role is required")
        if role in roles:
            errors.append(
                f"duplicate workflow role {role!r}: {roles[role]} and {filename}"
            )
        else:
            roles[role] = filename

        digest = normalized_workflow_hash(text)
        if digest in hashes:
            errors.append(
                f"duplicate workflow body after normalization: {hashes[digest]} and {filename}"
            )
        else:
            hashes[digest] = filename

        ids = job_ids(text)
        if len(ids) != len(set(ids)):
            errors.append(f"{filename}: duplicate job id detected")

    return {
        "valid": not errors,
        "workflow_count": len(actual_paths),
        "workflows": actual_paths,
        "roles": roles,
        "errors": errors,
        "policy": str((root / POLICY_RELATIVE).resolve()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    result = verify(args.repo)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
