from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


CHAT_URL_PATTERN = re.compile(r"^https://chatgpt\\.com/c/[A-Za-z0-9_-]+$")
ALLOWED_PROVIDERS = frozenset({"chatgpt_browser"})


class M0AcceptanceError(ValueError):
    """Raised when the M0 live acceptance contract is incomplete or unsafe."""


@dataclass(frozen=True)
class M0Response:
    provider: str
    authenticated: bool
    chat_url: str
    task_id: str
    status: str
    summary: str
    evidence: str
    patch: str
    next_task_id: str | None
    next_prompt: str | None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "M0Response":
        required = ("provider", "authenticated", "chat_url", "task_id", "status", "summary", "evidence", "patch")
        missing = [key for key in required if key not in payload]
        if missing:
            raise M0AcceptanceError(f"missing response fields: {', '.join(missing)}")

        provider = payload["provider"]
        authenticated = payload["authenticated"]
        chat_url = payload["chat_url"]
        task_id = payload["task_id"]
        status = payload["status"]
        summary = payload["summary"]
        evidence = payload["evidence"]
        patch = payload["patch"]

        if provider not in ALLOWED_PROVIDERS:
            raise M0AcceptanceError("M0 requires the authenticated ChatGPT browser provider")
        if authenticated is not True:
            raise M0AcceptanceError("M0 requires authenticated ChatGPT response evidence")
        if not isinstance(chat_url, str) or not CHAT_URL_PATTERN.fullmatch(chat_url):
            raise M0AcceptanceError("M0 requires a verified ChatGPT conversation URL")
        if not isinstance(task_id, str) or not task_id.strip():
            raise M0AcceptanceError("task_id is required")
        if status != "complete":
            raise M0AcceptanceError("M0 advances only from a complete task response")
        for name, value in (("summary", summary), ("evidence", evidence), ("patch", patch)):
            if not isinstance(value, str) or not value.strip():
                raise M0AcceptanceError(f"{name} is required for M0 completion")

        next_task_id = payload.get("next_task_id")
        next_prompt = payload.get("next_prompt")
        if next_task_id is not None and (not isinstance(next_task_id, str) or not next_task_id.strip()):
            raise M0AcceptanceError("next_task_id must be a non-empty string when supplied")
        if next_prompt is not None and (not isinstance(next_prompt, str) or not next_prompt.strip()):
            raise M0AcceptanceError("next_prompt must be a non-empty string when supplied")

        return cls(
            provider=provider,
            authenticated=authenticated,
            chat_url=chat_url,
            task_id=task_id.strip(),
            status=status,
            summary=summary.strip(),
            evidence=evidence.strip(),
            patch=patch,
            next_task_id=next_task_id.strip() if isinstance(next_task_id, str) else None,
            next_prompt=next_prompt.strip() if isinstance(next_prompt, str) else None,
        )


@dataclass(frozen=True)
class PromptProgression:
    """Prompt state that advances exactly once, and only after completion evidence."""

    task_id: str
    prompt: str
    completed: bool = False
    completion_digest: str = ""

    def complete(
        self,
        *,
        verified_task_id: str,
        evidence: str,
        next_task_id: str,
        next_prompt: str,
    ) -> "PromptProgression":
        if self.completed:
            raise M0AcceptanceError("current prompt has already advanced")
        if verified_task_id != self.task_id:
            raise M0AcceptanceError("completion task identity does not match current prompt")
        if not evidence.strip():
            raise M0AcceptanceError("completion evidence is required before prompt advancement")
        if not next_task_id.strip() or not next_prompt.strip():
            raise M0AcceptanceError("next task and next prompt are required for advancement")
        if next_prompt.strip() == self.prompt.strip():
            raise M0AcceptanceError("next prompt must change after task completion")

        digest = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
        return PromptProgression(
            task_id=next_task_id.strip(),
            prompt=next_prompt.strip(),
            completed=False,
            completion_digest=digest,
        )


def parse_response_file(path: Path) -> M0Response:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise M0AcceptanceError(f"invalid M0 response JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise M0AcceptanceError("M0 response must be a JSON object")
    return M0Response.from_mapping(payload)


def _run(command: list[str], cwd: Path, *, timeout: float) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise M0AcceptanceError(
            f"command failed ({result.returncode}): {' '.join(command)}\\n{result.stdout}{result.stderr}"
        )
    return result.stdout


def apply_validate_commit(
    *,
    repo: Path,
    response: M0Response,
    branch_name: str,
    expected_proof: str = "PASI M0 LIVE PROOF\\n",
) -> tuple[str, Path]:
    if not (repo / ".git").exists():
        raise M0AcceptanceError(f"not a git worktree: {repo}")

    before = _run(["git", "rev-parse", "HEAD"], repo, timeout=10).strip()
    if _run(["git", "status", "--porcelain", "--untracked-files=all"], repo, timeout=10).strip():
        raise M0AcceptanceError("M0 worktree must start clean")

    patch_file = repo / ".runtime" / "m0-response.patch"
    patch_file.parent.mkdir(parents=True, exist_ok=True)
    patch_file.write_text(response.patch, encoding="utf-8")

    _run(["git", "apply", "--check", str(patch_file)], repo, timeout=10)
    _run(["git", "apply", "--index", str(patch_file)], repo, timeout=10)

    proof = repo / "acceptance" / "M0-LIVE-PROOF.txt"
    if not proof.is_file() or proof.read_text(encoding="utf-8") != expected_proof:
        raise M0AcceptanceError("M0 proof artifact is missing or has unexpected contents")

    try:
        _run(["bash", "scripts/test_full_repo.sh"], repo, timeout=300)
    finally:
        patch_file.unlink(missing_ok=True)

    _run(["git", "add", "-A"], repo, timeout=10)
    staged = _run(["git", "diff", "--cached", "--name-status"], repo, timeout=10)
    if "acceptance/M0-LIVE-PROOF.txt" not in staged:
        raise M0AcceptanceError("M0 proof artifact was not staged")

    _run(
        [
            "git",
            "commit",
            "-m",
            f"accept(m0): {response.task_id}",
        ],
        repo,
        timeout=30,
    )

    after = _run(["git", "rev-parse", "HEAD"], repo, timeout=10).strip()
    if after == before:
        raise M0AcceptanceError("M0 did not produce a new commit")

    parents = _run(["git", "rev-list", "--parents", "-n", "1", after], repo, timeout=10).strip().split()
    if len(parents) != 2 or parents[1] != before:
        raise M0AcceptanceError("M0 commit is not a direct child of the starting revision")

    clean = _run(["git", "status", "--porcelain", "--untracked-files=all"], repo, timeout=10).strip()
    if clean:
        raise M0AcceptanceError(f"M0 worktree is not clean after commit: {clean}")

    evidence_dir = repo / ".runtime" / "acceptance"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence = evidence_dir / "m0-live.json"
    evidence.write_text(
        json.dumps(
            {
                "gate": "M0",
                "status": "PASS",
                "provider": response.provider,
                "authenticated": response.authenticated,
                "chat_url": response.chat_url,
                "task_id": response.task_id,
                "summary": response.summary,
                "evidence": response.evidence,
                "commit": after,
                "branch": branch_name,
                "proof_file": "acceptance/M0-LIVE-PROOF.txt",
                "prompt_advance_rule": "advance only after verified completion",
                "next_task_id": response.next_task_id,
                "next_prompt_digest": (
                    hashlib.sha256(response.next_prompt.encode("utf-8")).hexdigest()
                    if response.next_prompt
                    else None
                ),
            },
            indent=2,
        )
        + "\\n",
        encoding="utf-8",
    )
    return after, evidence


__all__ = [
    "M0AcceptanceError",
    "M0Response",
    "PromptProgression",
    "apply_validate_commit",
    "parse_response_file",
]
