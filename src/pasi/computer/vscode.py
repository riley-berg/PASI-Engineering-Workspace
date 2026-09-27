from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


class VSCodeActionError(ValueError):
    pass


@dataclass(frozen=True)
class VSCodeActionResult:
    action: str
    argv: tuple[str, ...]
    exit_code: int


class VSCodeSemanticAdapter:
    def __init__(
        self,
        *,
        workspace_root: Path | str,
        executable: str = "code",
        runner: Callable[[Sequence[str], Path], int] | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.executable = executable
        self.runner = runner or self._default_runner

    def _resolve(self, relative_path: str) -> Path:
        path = (self.workspace_root / relative_path).resolve()
        try:
            path.relative_to(self.workspace_root)
        except ValueError as exc:
            raise VSCodeActionError("VS Code target escapes workspace root") from exc
        return path

    @staticmethod
    def _default_runner(argv: Sequence[str], cwd: Path) -> int:
        return subprocess.run(list(argv), cwd=cwd, check=False).returncode

    def open_file(self, relative_path: str) -> VSCodeActionResult:
        path = self._resolve(relative_path)
        argv = (self.executable, str(path))
        return VSCodeActionResult("open_file", argv, self.runner(argv, self.workspace_root))

    def goto_location(
        self,
        relative_path: str,
        *,
        line: int,
        column: int = 1,
    ) -> VSCodeActionResult:
        if line <= 0 or column <= 0:
            raise VSCodeActionError("line/column must be positive")
        path = self._resolve(relative_path)
        target = f"{path}:{line}:{column}"
        argv = (self.executable, "--goto", target)
        return VSCodeActionResult("goto_location", argv, self.runner(argv, self.workspace_root))

    def open_workspace(self) -> VSCodeActionResult:
        argv = (self.executable, str(self.workspace_root))
        return VSCodeActionResult("open_workspace", argv, self.runner(argv, self.workspace_root))
