from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from pasi.core.roadmap import Roadmap, RoadmapError, RoadmapTask


MAX_CHILD_TASKS = 5


class ChildTaskModel(Protocol):
    def complete(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class ChildTaskCandidate:
    id: str
    title: str
    depends_on: tuple[str, ...]
    acceptance_requirements: tuple[str, ...]
    evidence_requirements: tuple[str, ...]


class ChildTaskGenerationError(RoadmapError):
    """Raised when model-proposed child tasks violate the roadmap contract."""


class BoundedChildTaskGenerator:
    """Treat model output as advisory and persist only contract-valid child tasks."""

    def __init__(self, model: ChildTaskModel, *, max_children: int = MAX_CHILD_TASKS) -> None:
        if max_children <= 0 or max_children > MAX_CHILD_TASKS:
            raise ValueError(f"max_children must be between 1 and {MAX_CHILD_TASKS}")
        self.model = model
        self.max_children = max_children

    def generate(
        self,
        roadmap: Roadmap,
        *,
        parent_task_id: str,
    ) -> tuple[RoadmapTask, ...]:
        parent = roadmap.task(parent_task_id)
        prompt = json.dumps(
            {
                "parent_task_id": parent.id,
                "phase_id": parent.phase_id,
                "objective": parent.title,
                "parent_acceptance_requirements": list(parent.acceptance_requirements),
                "parent_evidence_requirements": list(parent.evidence_requirements),
                "limits": {
                    "max_children": self.max_children,
                    "child_ids_must_start_with": parent.id + ".",
                },
                "rules": [
                    "Return JSON only.",
                    "Do not claim execution happened.",
                    "Generate only bounded implementation sub-tasks.",
                    "Every child stays in the parent phase.",
                    "Every child preserves all parent acceptance requirements.",
                    "Every child preserves all parent evidence requirements.",
                    "Dependencies may reference existing roadmap tasks or siblings in this generated set.",
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        raw = self.model.complete(prompt)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ChildTaskGenerationError("model output is not valid JSON") from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("children"), list):
            raise ChildTaskGenerationError("model output requires a children array")

        candidates = payload["children"]
        if len(candidates) == 0:
            raise ChildTaskGenerationError("model returned no child tasks")
        if len(candidates) > self.max_children:
            raise ChildTaskGenerationError(
                f"model returned {len(candidates)} children; maximum is {self.max_children}"
            )

        existing_ids = {task.id for task in roadmap.tasks}
        candidate_ids: set[str] = set()
        children: list[RoadmapTask] = []

        for index, raw_child in enumerate(candidates, start=1):
            if not isinstance(raw_child, dict):
                raise ChildTaskGenerationError(f"child {index} is not an object")
            candidate = self._parse_candidate(raw_child, parent)
            if candidate.id in existing_ids or candidate.id in candidate_ids:
                raise ChildTaskGenerationError(
                    f"child {candidate.id} collides with an existing/generated task"
                )
            if not candidate.id.startswith(parent.id + "."):
                raise ChildTaskGenerationError(
                    f"child {candidate.id} is outside parent task namespace {parent.id}."
                )

            candidate_ids.add(candidate.id)
            children.append(
                RoadmapTask(
                    id=candidate.id,
                    title=candidate.title,
                    phase_id=parent.phase_id,
                    depends_on=candidate.depends_on,
                    acceptance_requirements=candidate.acceptance_requirements,
                    evidence_requirements=candidate.evidence_requirements,
                    parent_task_id=parent.id,
                )
            )

        valid_dependency_ids = existing_ids | candidate_ids
        for child in children:
            if not set(child.depends_on).issubset(valid_dependency_ids):
                invalid = sorted(set(child.depends_on) - valid_dependency_ids)
                raise ChildTaskGenerationError(
                    f"child {child.id} references out-of-scope dependencies: {invalid}"
                )

        try:
            roadmap.add_child_tasks(parent.id, tuple(children))
        except RoadmapError as exc:
            raise ChildTaskGenerationError(str(exc)) from exc

        return tuple(children)

    @staticmethod
    def _parse_candidate(
        raw_child: dict[str, object],
        parent: RoadmapTask,
    ) -> ChildTaskCandidate:
        child_id = raw_child.get("id")
        title = raw_child.get("title")
        if not isinstance(child_id, str) or not child_id.strip():
            raise ChildTaskGenerationError("child requires a non-empty id")
        if not isinstance(title, str) or not title.strip():
            raise ChildTaskGenerationError(f"child {child_id!r} requires a title")

        depends_raw = raw_child.get("depends_on", [])
        acceptance_raw = raw_child.get(
            "acceptance_requirements",
            list(parent.acceptance_requirements),
        )
        evidence_raw = raw_child.get(
            "evidence_requirements",
            list(parent.evidence_requirements),
        )
        if not isinstance(depends_raw, list):
            raise ChildTaskGenerationError(f"child {child_id} depends_on must be an array")
        if not isinstance(acceptance_raw, list) or not acceptance_raw:
            raise ChildTaskGenerationError(
                f"child {child_id} acceptance_requirements must be a non-empty array"
            )
        if not isinstance(evidence_raw, list) or not evidence_raw:
            raise ChildTaskGenerationError(
                f"child {child_id} evidence_requirements must be a non-empty array"
            )

        depends = tuple(str(item).strip() for item in depends_raw)
        acceptance = tuple(str(item).strip() for item in acceptance_raw)
        evidence = tuple(str(item).strip() for item in evidence_raw)

        if any(not item for item in (*depends, *acceptance, *evidence)):
            raise ChildTaskGenerationError(
                f"child {child_id} contains empty requirement/dependency values"
            )

        return ChildTaskCandidate(
            id=child_id.strip(),
            title=title.strip(),
            depends_on=depends,
            acceptance_requirements=acceptance,
            evidence_requirements=evidence,
        )
