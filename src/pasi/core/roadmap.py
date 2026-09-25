from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class RoadmapPhase:
    id: str
    title: str
    depends_on: tuple[str, ...]
    status: str

@dataclass(frozen=True)
class Roadmap:
    phases: tuple[RoadmapPhase, ...]

    def next_ready(self) -> RoadmapPhase | None:
        completed = {phase.id for phase in self.phases if phase.status == "completed"}
        for phase in self.phases:
            if phase.status not in {"planned", "in_progress"}:
                continue
            if all(dep in completed for dep in phase.depends_on):
                return phase
        return None

def load_roadmap(path: Path) -> Roadmap:
    payload = json.loads(path.read_text(encoding="utf-8"))
    phases = tuple(
        RoadmapPhase(
            id=str(item["id"]),
            title=str(item["title"]),
            depends_on=tuple(str(value) for value in item.get("depends_on", [])),
            status=str(item.get("status", "planned")),
        )
        for item in payload.get("phases", [])
    )
    if not phases:
        raise ValueError("roadmap must contain at least one phase")
    return Roadmap(phases)
