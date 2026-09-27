from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone

from pasi.core.task_selection import SelectionDecision, StaleSelection
from pasi.core.roadmap import Roadmap


class RankingExplanationError(ValueError):
    """Raised when a ranking explanation is invalid."""


@dataclass(frozen=True)
class RankingExplanation:
    roadmap_id: str
    roadmap_revision: int
    selected_task_id: str | None
    candidates: tuple[dict[str, object], ...]
    canonical_sha256: str
    created_at: str

    @classmethod
    def from_decision(
        cls,
        decision: SelectionDecision,
        *,
        manual_order: dict[str, int] | None = None,
    ) -> "RankingExplanation":
        order = manual_order or {}
        candidates: list[dict[str, object]] = []
        for task_id in decision.eligible_task_ids:
            score = float(decision.advisory_scores.get(task_id, 0.0))
            manual_rank = order.get(task_id)
            candidates.append(
                {
                    "task_id": task_id,
                    "eligible": True,
                    "advisory_score": score,
                    "manual_rank": manual_rank,
                    "tie_break": "manual_rank_then_score_then_task_id",
                    "reason": "authoritatively eligible",
                }
            )
        for task_id, reason in sorted(decision.excluded_reasons.items()):
            candidates.append(
                {
                    "task_id": task_id,
                    "eligible": False,
                    "advisory_score": decision.advisory_scores.get(task_id),
                    "manual_rank": order.get(task_id),
                    "tie_break": "not_applicable",
                    "reason": reason,
                }
            )
        payload = {
            "roadmap_id": decision.roadmap_id,
            "roadmap_revision": decision.roadmap_revision,
            "selected_task_id": decision.selected_task_id,
            "candidates": candidates,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return cls(
            roadmap_id=decision.roadmap_id,
            roadmap_revision=decision.roadmap_revision,
            selected_task_id=decision.selected_task_id,
            candidates=tuple(candidates),
            canonical_sha256=digest,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def assert_fresh(self, roadmap: Roadmap) -> None:
        if (
            roadmap.roadmap_id != self.roadmap_id
            or roadmap.revision != self.roadmap_revision
        ):
            raise StaleSelection(
                "ranking explanation targets a different roadmap revision"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "roadmap_id": self.roadmap_id,
            "roadmap_revision": self.roadmap_revision,
            "selected_task_id": self.selected_task_id,
            "candidates": list(self.candidates),
            "canonical_sha256": self.canonical_sha256,
            "created_at": self.created_at,
        }


class SQLiteRankingExplanationStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ranking_explanation (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    roadmap_id TEXT NOT NULL,
                    roadmap_revision INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def record(self, explanation: RankingExplanation) -> int:
        with sqlite3.connect(self.path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO ranking_explanation(
                    roadmap_id, roadmap_revision, payload_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    explanation.roadmap_id,
                    explanation.roadmap_revision,
                    json.dumps(
                        explanation.to_dict(),
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    explanation.created_at,
                ),
            )
            return int(cursor.lastrowid)
