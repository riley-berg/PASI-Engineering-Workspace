from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence
from urllib.parse import urlparse


class AdapterError(ValueError):
    """Raised when a semantic computer adapter request is invalid."""


@dataclass(frozen=True)
class ProcessInvocation:
    argv: tuple[str, ...]
    cwd: Path


class ProcessAdapter(Protocol):
    def run(self, invocation: ProcessInvocation) -> int: ...


class SubprocessProcessAdapter:
    def run(self, invocation: ProcessInvocation) -> int:
        completed = subprocess.run(
            list(invocation.argv),
            cwd=invocation.cwd,
            shell=False,
            check=False,
        )
        return int(completed.returncode)


class VSCodeAdapter:
    """Semantic VS Code launcher boundary; no UI scraping is used."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        process: ProcessAdapter,
        executable: str = "code",
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.process = process
        self.executable = executable

    def _safe_path(self, path: Path) -> Path:
        resolved = path.resolve()
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError as exc:
            raise AdapterError("VS Code target escapes workspace root") from exc
        return resolved

    def open_workspace(self, path: Path) -> int:
        safe = self._safe_path(path)
        return self.process.run(
            ProcessInvocation((self.executable, "--reuse-window", str(safe)), self.workspace_root)
        )

    def open_file(self, path: Path, *, line: int | None = None, column: int | None = None) -> int:
        safe = self._safe_path(path)
        if line is not None and line <= 0:
            raise AdapterError("line must be positive")
        if column is not None and column <= 0:
            raise AdapterError("column must be positive")
        target = str(safe)
        if line is not None:
            target += f":{line}"
            if column is not None:
                target += f":{column}"
        return self.process.run(
            ProcessInvocation((self.executable, "--reuse-window", target), self.workspace_root)
        )

    def goto_location(self, path: Path, *, line: int, column: int = 1) -> int:
        return self.open_file(path, line=line, column=column)


@dataclass(frozen=True)
class BrowserNavigation:
    operation_id: str
    url: str


@dataclass(frozen=True)
class BrowserClick:
    operation_id: str
    target_id: str


@dataclass(frozen=True)
class BrowserFill:
    operation_id: str
    target_id: str
    value: str


class BrowserController(Protocol):
    def navigate(self, operation: BrowserNavigation) -> dict[str, object]: ...
    def click(self, operation: BrowserClick) -> dict[str, object]: ...
    def fill(self, operation: BrowserFill) -> dict[str, object]: ...


class SemanticBrowserAdapter:
    """Typed browser capability boundary. Model transport remains out of this layer."""

    def __init__(
        self,
        controller: BrowserController,
        *,
        allowed_hosts: Sequence[str],
        allowed_schemes: Sequence[str] = ("https",),
    ) -> None:
        self.controller = controller
        self.allowed_hosts = frozenset(host.lower() for host in allowed_hosts)
        self.allowed_schemes = frozenset(allowed_schemes)

    def navigate(self, operation: BrowserNavigation) -> dict[str, object]:
        parsed = urlparse(operation.url)
        if parsed.scheme not in self.allowed_schemes:
            raise AdapterError("browser URL scheme is not allowed")
        if not parsed.hostname or parsed.hostname.lower() not in self.allowed_hosts:
            raise AdapterError("browser URL host is not allowed")
        return self.controller.navigate(operation)

    def click(self, operation: BrowserClick) -> dict[str, object]:
        if not operation.target_id.strip():
            raise AdapterError("semantic target id is required")
        return self.controller.click(operation)

    def fill(self, operation: BrowserFill) -> dict[str, object]:
        if not operation.target_id.strip():
            raise AdapterError("semantic target id is required")
        if len(operation.value) > 100_000:
            raise AdapterError("fill value exceeds bounded size")
        return self.controller.fill(operation)


@dataclass(frozen=True)
class FileReadResult:
    path: str
    content: str


@dataclass(frozen=True)
class FileWriteResult:
    path: str
    bytes_written: int


class ScopedFileAdapter:
    """Workspace-root scoped file operations."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()

    def _safe(self, path: Path) -> Path:
        resolved = path.resolve()
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError as exc:
            raise AdapterError("file target escapes workspace root") from exc
        return resolved

    def read(self, path: Path, *, max_bytes: int = 1_000_000) -> FileReadResult:
        if max_bytes <= 0 or max_bytes > 10_000_000:
            raise AdapterError("max_bytes out of bounds")
        target = self._safe(path)
        data = target.read_bytes()
        if len(data) > max_bytes:
            raise AdapterError("file exceeds read bound")
        return FileReadResult(str(target), data.decode("utf-8"))

    def write(self, path: Path, content: str, *, max_bytes: int = 1_000_000) -> FileWriteResult:
        target = self._safe(path)
        payload = content.encode("utf-8")
        if len(payload) > max_bytes:
            raise AdapterError("content exceeds write bound")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return FileWriteResult(str(target), len(payload))


class TypedGitAdapter:
    """Allowlisted git operations with explicit repository scope."""

    ALLOWED = frozenset({"status", "diff_check"})

    def __init__(self, *, workspace_root: Path, process: ProcessAdapter) -> None:
        self.workspace_root = workspace_root.resolve()
        self.process = process

    def run(self, operation: str) -> int:
        if operation not in self.ALLOWED:
            raise AdapterError(f"unsupported git operation: {operation}")
        argv = (
            ("git", "status", "--short")
            if operation == "status"
            else ("git", "diff", "--check")
        )
        return self.process.run(ProcessInvocation(argv, self.workspace_root))


class ApplicationLauncher:
    """Allowlisted application launch boundary without shell escape."""

    def __init__(self, allowed_executables: Sequence[str], process: ProcessAdapter) -> None:
        self.allowed_executables = frozenset(allowed_executables)
        self.process = process

    def launch(self, executable: str, args: Sequence[str], *, cwd: Path) -> int:
        if executable not in self.allowed_executables:
            raise AdapterError("application executable is not allowlisted")
        if any(not isinstance(arg, str) or not arg for arg in args):
            raise AdapterError("application arguments must be non-empty strings")
        return self.process.run(ProcessInvocation((executable, *tuple(args)), cwd))
