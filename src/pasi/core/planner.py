from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

class CompletionModel(Protocol):
    def complete(self, prompt: str) -> str: ...

@dataclass(frozen=True)
class PlannerDecision:
    kind: str
    task_id: str
    instruction: str

class Planner:
    """Provider-neutral planner boundary; execution remains outside the model."""

    def __init__(self, model: CompletionModel) -> None:
        self.model = model

    def propose(self, *, phase_id: str, task_id: str, task_text: str) -> PlannerDecision:
        if not task_text.strip():
            raise ValueError("task_text is required")
        prompt = json.dumps({
            "phase": phase_id,
            "task_id": task_id,
            "objective": task_text,
            "rules": [
                "Return JSON only.",
                "Propose one bounded next engineering action.",
                "Never claim that execution already happened.",
                "Do not request arbitrary shell or browser authority.",
            ],
        }, separators=(",", ":"), sort_keys=True)
        value = json.loads(self.model.complete(prompt))
        if not isinstance(value, dict):
            raise ValueError("planner response must be an object")
        kind = value.get("kind")
        instruction = value.get("instruction")
        returned_task_id = value.get("task_id", task_id)
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("planner response requires kind")
        if not isinstance(instruction, str) or not instruction.strip():
            raise ValueError("planner response requires instruction")
        if returned_task_id != task_id:
            raise ValueError("planner returned a different task id")
        return PlannerDecision(kind=kind.strip(), task_id=task_id, instruction=instruction.strip())
