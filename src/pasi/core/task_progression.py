from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


PROGRESSION_SCHEMA_VERSION = 1
TASK_STATUSES = frozenset({"planned", "active", "completed"})
PROGRESSION_STATUSES = frozenset({"active", "interrupted", "completed"})
MAX_TASK_ID_CHARS = 128
MAX_PROMPT_CHARS = 12_000
MAX_EVIDENCE_CHARS = 4_000


class TaskProgressionError(ValueError):
    """Raised when durable task/prompt progression is invalid."""


@dataclass(frozen=True)
class RoadmapTask:
    task_id: str
    title: str
    status: str
    requirements: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RoadmapTask":
        if not isinstance(value, Mapping):
            raise TaskProgressionError("roadmap task must be an object")

        task_id = str(value.get("id", "")).strip()
        title = str(value.get("title", "")).strip()
        status = str(value.get("status", "planned")).strip()
        raw_requirements = value.get("requirements", [])

        if not task_id:
            raise TaskProgressionError("roadmap task id is required")
        if not title:
            raise TaskProgressionError(f"{task_id} title is required")
        if status not in TASK_STATUSES:
            raise TaskProgressionError(f"unsupported task status: {status!r}")
        if not isinstance(raw_requirements, list) or not raw_requirements:
            raise TaskProgressionError(f"{task_id} requires at least one requirement")

        requirements: list[str] = []
        for requirement in raw_requirements:
            if not isinstance(requirement, str) or not requirement.strip():
                raise TaskProgressionError(
                    f"{task_id} contains an empty task requirement"
                )
            requirements.append(requirement.strip())

        return cls(
            task_id=task_id[:MAX_TASK_ID_CHARS],
            title=title,
            status=status,
            requirements=tuple(requirements),
        )


@dataclass(frozen=True)
class ProgressionState:
    current_task_id: str
    current_prompt: str
    status: str = "active"
    prompt_generation: int = 1
    advance_count: int = 0
    checkpoint: str = ""
    completion_digest: str = ""
    completed_task_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.current_task_id.strip():
            raise TaskProgressionError("current_task_id is required")
        if not self.current_prompt.strip():
            raise TaskProgressionError("current_prompt is required")
        if len(self.current_task_id) > MAX_TASK_ID_CHARS:
            raise TaskProgressionError("current_task_id is too long")
        if len(self.current_prompt) > MAX_PROMPT_CHARS:
            raise TaskProgressionError("current_prompt is too long")
        if self.status not in PROGRESSION_STATUSES:
            raise TaskProgressionError(f"unsupported progression status: {self.status!r}")
        if self.prompt_generation < 1:
            raise TaskProgressionError("prompt_generation must be positive")
        if self.advance_count < 0:
            raise TaskProgressionError("advance_count must be non-negative")
        if len(self.checkpoint) > MAX_PROMPT_CHARS:
            raise TaskProgressionError("checkpoint is too long")
        if self.completion_digest and len(self.completion_digest) != 64:
            raise TaskProgressionError("completion_digest must be SHA-256")


@dataclass(frozen=True)
class CompletionReceipt:
    task_id: str
    evidence_digest: str
    next_task_id: str | None
    next_prompt: str | None
    advance_count: int


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _prompt_for(task: RoadmapTask) -> str:
    requirements = "\n".join(f"- {item}" for item in task.requirements)
    return (
        f"[PASI TASK {task.task_id}]\n"
        f"{task.title}\n\n"
        f"Requirements:\n{requirements}\n\n"
        "Execution rules:\n"
        "- Work only on this task.\n"
        "- Do not start the next task early.\n"
        "- Do not claim harness-side verification or commit actions before they occur.\n"
        "- Preserve the current task identity until verified completion."
    )


class RoadmapTaskCatalog:
    """Machine-readable task catalog loaded from the canonical roadmap JSON."""

    def __init__(self, tasks: Mapping[str, RoadmapTask]) -> None:
        if not tasks:
            raise TaskProgressionError("roadmap must contain at least one task")
        self._tasks = dict(tasks)

    @classmethod
    def from_roadmap_file(cls, path: Path) -> "RoadmapTaskCatalog":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TaskProgressionError(f"invalid roadmap JSON: {exc}") from exc

        tasks: dict[str, RoadmapTask] = {}
        for phase in payload.get("phases", []):
            if not isinstance(phase, Mapping):
                raise TaskProgressionError("roadmap phase must be an object")
            for raw_task in phase.get("tasks", []):
                task = RoadmapTask.from_mapping(raw_task)
                if task.task_id in tasks:
                    raise TaskProgressionError(
                        f"duplicate roadmap task id: {task.task_id}"
                    )
                tasks[task.task_id] = task
        return cls(tasks)

    def get(self, task_id: str) -> RoadmapTask:
        try:
            return self._tasks[task_id]
        except KeyError as exc:
            raise TaskProgressionError(f"unknown roadmap task: {task_id}") from exc

    def ordered_tasks(self) -> tuple[RoadmapTask, ...]:
        return tuple(self._tasks.values())

    def next_task(self, current_task_id: str) -> RoadmapTask | None:
        tasks = self.ordered_tasks()
        for index, task in enumerate(tasks):
            if task.task_id == current_task_id:
                return tasks[index + 1] if index + 1 < len(tasks) else None
        raise TaskProgressionError(f"unknown roadmap task: {current_task_id}")


class TaskPromptProgression:
    """Durable task/prompt controller with exact-once advancement and resumable prompts."""

    def __init__(
        self,
        *,
        catalog: RoadmapTaskCatalog,
        state: ProgressionState,
    ) -> None:
        self.catalog = catalog
        self.state = state
        self.catalog.get(state.current_task_id)

    @classmethod
    def start(
        cls,
        *,
        catalog: RoadmapTaskCatalog,
        task_id: str,
    ) -> "TaskPromptProgression":
        task = catalog.get(task_id)
        return cls(
            catalog=catalog,
            state=ProgressionState(
                current_task_id=task.task_id,
                current_prompt=_prompt_for(task),
            ),
        )

    def checkpoint(self, checkpoint: str) -> "TaskPromptProgression":
        if self.state.status == "completed":
            raise TaskProgressionError("completed progression cannot be checkpointed")
        if not checkpoint.strip():
            raise TaskProgressionError("checkpoint is required")
        return TaskPromptProgression(
            catalog=self.catalog,
            state=ProgressionState(
                **{
                    **asdict(self.state),
                    "checkpoint": checkpoint,
                },
            ),
        )

    def interrupt(self) -> "TaskPromptProgression":
        if self.state.status == "completed":
            raise TaskProgressionError("completed progression cannot be interrupted")
        return TaskPromptProgression(
            catalog=self.catalog,
            state=ProgressionState(
                **{
                    **asdict(self.state),
                    "status": "interrupted",
                },
            ),
        )

    def resume(self) -> "TaskPromptProgression":
        if self.state.status != "interrupted":
            raise TaskProgressionError("only an interrupted task can be resumed")
        return TaskPromptProgression(
            catalog=self.catalog,
            state=ProgressionState(
                **{
                    **asdict(self.state),
                    "status": "active",
                },
            ),
        )

    def current_prompt(self) -> str:
        return self.state.current_prompt

    def complete(
        self,
        *,
        verified_task_id: str,
        evidence: str,
    ) -> tuple["TaskPromptProgression", CompletionReceipt]:
        if self.state.status != "active":
            raise TaskProgressionError("only an active task can advance")
        if verified_task_id != self.state.current_task_id:
            raise TaskProgressionError("verified task does not match current task")
        if not evidence.strip():
            raise TaskProgressionError("verified completion evidence is required")
        if verified_task_id in self.state.completed_task_ids:
            raise TaskProgressionError("task has already advanced")

        next_task = self.catalog.next_task(self.state.current_task_id)
        evidence_digest = _digest(evidence)

        if next_task is None:
            next_state = ProgressionState(
                **{
                    **asdict(self.state),
                    "status": "completed",
                    "advance_count": self.state.advance_count + 1,
                    "completion_digest": evidence_digest,
                    "completed_task_ids": self.state.completed_task_ids + (verified_task_id,),
                },
            )
            receipt = CompletionReceipt(
                task_id=verified_task_id,
                evidence_digest=evidence_digest,
                next_task_id=None,
                next_prompt=None,
                advance_count=next_state.advance_count,
            )
            return TaskPromptProgression(catalog=self.catalog, state=next_state), receipt

        next_prompt = _prompt_for(next_task)
        if next_prompt == self.state.current_prompt:
            raise TaskProgressionError("roadmap produced an unchanged next prompt")

        next_state = ProgressionState(
            current_task_id=next_task.task_id,
            current_prompt=next_prompt,
            status="active",
            prompt_generation=self.state.prompt_generation + 1,
            advance_count=self.state.advance_count + 1,
            checkpoint="",
            completion_digest=evidence_digest,
            completed_task_ids=self.state.completed_task_ids + (verified_task_id,),
        )
        receipt = CompletionReceipt(
            task_id=verified_task_id,
            evidence_digest=evidence_digest,
            next_task_id=next_task.task_id,
            next_prompt=next_prompt,
            advance_count=next_state.advance_count,
        )
        return TaskPromptProgression(catalog=self.catalog, state=next_state), receipt

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PROGRESSION_SCHEMA_VERSION,
            **asdict(self.state),
        }

    @classmethod
    def from_dict(
        cls,
        *,
        catalog: RoadmapTaskCatalog,
        value: Mapping[str, Any],
    ) -> "TaskPromptProgression":
        if value.get("schema_version") != PROGRESSION_SCHEMA_VERSION:
            raise TaskProgressionError("unsupported progression schema version")
        state = ProgressionState(
            current_task_id=str(value.get("current_task_id", "")),
            current_prompt=str(value.get("current_prompt", "")),
            status=str(value.get("status", "active")),
            prompt_generation=int(value.get("prompt_generation", 1)),
            advance_count=int(value.get("advance_count", 0)),
            checkpoint=str(value.get("checkpoint", "")),
            completion_digest=str(value.get("completion_digest", "")),
            completed_task_ids=tuple(str(item) for item in value.get("completed_task_ids", [])),
        )
        return cls(catalog=catalog, state=state)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)

    @classmethod
    def load(
        cls,
        *,
        catalog: RoadmapTaskCatalog,
        path: Path,
    ) -> "TaskPromptProgression":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TaskProgressionError(f"invalid progression state: {exc}") from exc
        if not isinstance(payload, dict):
            raise TaskProgressionError("progression state must be an object")
        return cls.from_dict(catalog=catalog, value=payload)


__all__ = [
    "CompletionReceipt",
    "ProgressionState",
    "RoadmapTask",
    "RoadmapTaskCatalog",
    "TASK_STATUSES",
    "TaskPromptProgression",
    "TaskProgressionError",
]