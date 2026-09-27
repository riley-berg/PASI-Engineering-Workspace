from __future__ import annotations

import hashlib
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class TerminalExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class TerminalCommand:
    program: str
    args: tuple[str, ...]
    cwd: Path
    timeout_seconds: float
    max_output_bytes: int
    environment: Mapping[str, str] | None = None


@dataclass(frozen=True)
class TerminalResult:
    program: str
    args: tuple[str, ...]
    exit_code: int
    timed_out: bool
    latency_ms: float
    stdout: str
    stderr: str
    stdout_sha256: str
    stderr_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "program": self.program,
            "args": list(self.args),
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "latency_ms": self.latency_ms,
            "stdout_sha256": self.stdout_sha256,
            "stderr_sha256": self.stderr_sha256,
        }


class TypedTerminalCapability:
    def __init__(
        self,
        *,
        workspace_root: Path | str,
        allowed_programs: tuple[str, ...] = ("python", "python3", "git"),
        max_timeout_seconds: float = 60,
        max_output_bytes: int = 1_000_000,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.allowed_programs = frozenset(allowed_programs)
        self.max_timeout_seconds = max_timeout_seconds
        self.max_output_bytes = max_output_bytes

    def execute(self, command: TerminalCommand) -> TerminalResult:
        if command.program not in self.allowed_programs:
            raise TerminalExecutionError(
                f"program is not allowlisted: {command.program}"
            )
        if command.timeout_seconds <= 0 or command.timeout_seconds > self.max_timeout_seconds:
            raise TerminalExecutionError("timeout exceeds terminal capability bound")
        if command.max_output_bytes <= 0 or command.max_output_bytes > self.max_output_bytes:
            raise TerminalExecutionError("output bound exceeds terminal capability limit")

        cwd = command.cwd if command.cwd.is_absolute() else self.workspace_root / command.cwd
        cwd = cwd.resolve()
        try:
            cwd.relative_to(self.workspace_root)
        except ValueError as exc:
            raise TerminalExecutionError("working directory escapes workspace root") from exc
        if not cwd.exists() or not cwd.is_dir():
            raise TerminalExecutionError("working directory does not exist")

        env = os.environ.copy()
        if command.environment:
            env.update(command.environment)

        started = time.perf_counter()
        try:
            completed = subprocess.run(
                [command.program, *command.args],
                cwd=cwd,
                env=env,
                shell=False,
                capture_output=True,
                text=True,
                timeout=command.timeout_seconds,
                check=False,
            )
            timed_out = False
            stdout = completed.stdout
            stderr = completed.stderr
            exit_code = completed.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = (exc.stdout or "")[: command.max_output_bytes]
            stderr = (exc.stderr or "")[: command.max_output_bytes]
            exit_code = -1
        latency_ms = (time.perf_counter() - started) * 1000

        stdout = stdout[: command.max_output_bytes]
        stderr = stderr[: command.max_output_bytes]
        return TerminalResult(
            program=command.program,
            args=command.args,
            exit_code=exit_code,
            timed_out=timed_out,
            latency_ms=latency_ms,
            stdout=stdout,
            stderr=stderr,
            stdout_sha256=hashlib.sha256(stdout.encode()).hexdigest(),
            stderr_sha256=hashlib.sha256(stderr.encode()).hexdigest(),
        )
