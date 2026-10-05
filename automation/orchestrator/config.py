from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AI_DIR = PROJECT_ROOT / ".ai"


@dataclass(frozen=True)
class OrchestratorConfig:
    project_root: Path = PROJECT_ROOT
    ai_dir: Path = AI_DIR


CONFIG = OrchestratorConfig()
