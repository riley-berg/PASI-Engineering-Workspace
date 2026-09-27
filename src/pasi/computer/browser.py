from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse


class BrowserActionError(ValueError):
    pass


@dataclass(frozen=True)
class SemanticTarget:
    role: str
    name: str

    def __post_init__(self) -> None:
        if not self.role.strip() or not self.name.strip():
            raise BrowserActionError("semantic target role/name are required")


@dataclass(frozen=True)
class BrowserActionResult:
    action: str
    target: str
    succeeded: bool
    detail: str = ""


class BrowserController(Protocol):
    def navigate(self, url: str, timeout_seconds: float) -> object: ...
    def click(self, target: SemanticTarget, timeout_seconds: float) -> object: ...
    def fill(self, target: SemanticTarget, value: str, timeout_seconds: float) -> object: ...


class BrowserSemanticAdapter:
    def __init__(
        self,
        controller: BrowserController,
        *,
        allowed_hosts: tuple[str, ...],
        timeout_seconds: float = 30,
    ) -> None:
        if not allowed_hosts:
            raise BrowserActionError("allowed_hosts is required")
        if timeout_seconds <= 0 or timeout_seconds > 120:
            raise BrowserActionError("browser timeout out of bounds")
        self.controller = controller
        self.allowed_hosts = frozenset(host.lower() for host in allowed_hosts)
        self.timeout_seconds = timeout_seconds

    def navigate(self, url: str) -> BrowserActionResult:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            raise BrowserActionError("only http/https URLs are allowed")
        if parsed.hostname.lower() not in self.allowed_hosts:
            raise BrowserActionError("browser host is not allowlisted")
        self.controller.navigate(url, self.timeout_seconds)
        return BrowserActionResult("navigate", url, True)

    def click(self, target: SemanticTarget) -> BrowserActionResult:
        self.controller.click(target, self.timeout_seconds)
        return BrowserActionResult("click", f"{target.role}:{target.name}", True)

    def fill(self, target: SemanticTarget, value: str) -> BrowserActionResult:
        if len(value) > 4096:
            raise BrowserActionError("fill value exceeds bound")
        self.controller.fill(target, value, self.timeout_seconds)
        return BrowserActionResult("fill", f"{target.role}:{target.name}", True)
