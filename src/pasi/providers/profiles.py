from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from pasi.providers.registry import SQLiteProviderRegistry


PROFILE_SCHEMA_VERSION = 1
MAX_PROFILE_NAME = 128
MAX_MODEL_NAME = 256
MAX_ENV_KEYS = 32


class ModelProfileError(ValueError):
    """Raised when a local model profile is invalid or incompatible."""


@dataclass(frozen=True)
class ModelProfile:
    name: str
    provider: str
    model: str
    required_capabilities: tuple[str, ...]
    min_context_window_tokens: int
    max_output_tokens: int
    configuration_provenance: tuple[str, ...]
    version: int = PROFILE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.version != PROFILE_SCHEMA_VERSION:
            raise ModelProfileError("unsupported model profile version")
        if not self.name.strip() or len(self.name) > MAX_PROFILE_NAME:
            raise ModelProfileError("profile name is required and bounded")
        if not self.provider.strip():
            raise ModelProfileError("profile provider is required")
        if not self.model.strip() or len(self.model) > MAX_MODEL_NAME:
            raise ModelProfileError("profile model is required and bounded")
        if not self.required_capabilities:
            raise ModelProfileError("profile requires at least one capability")
        if len(set(self.required_capabilities)) != len(self.required_capabilities):
            raise ModelProfileError("profile capabilities contain duplicates")
        if self.min_context_window_tokens <= 0 or self.max_output_tokens <= 0:
            raise ModelProfileError("profile token bounds must be positive")
        if self.max_output_tokens >= self.min_context_window_tokens:
            raise ModelProfileError("profile output bound must be below context minimum")
        if not self.configuration_provenance:
            raise ModelProfileError("profile configuration provenance is required")
        if len(self.configuration_provenance) > MAX_ENV_KEYS:
            raise ModelProfileError("profile configuration provenance is too large")

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "name": self.name,
            "provider": self.provider,
            "model": self.model,
            "required_capabilities": list(self.required_capabilities),
            "min_context_window_tokens": self.min_context_window_tokens,
            "max_output_tokens": self.max_output_tokens,
            "configuration_provenance": list(self.configuration_provenance),
        }

    def digest(self) -> str:
        canonical = json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ModelProfileManager:
    """Load and validate model profiles against the authoritative provider registry."""

    def load(self, path: Path | str) -> ModelProfile:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ModelProfileError("model profile could not be loaded") from exc
        return self.from_mapping(payload)

    def from_mapping(self, value: dict[str, object]) -> ModelProfile:
        if not isinstance(value, dict):
            raise ModelProfileError("profile must be an object")
        try:
            capabilities = tuple(str(item) for item in value["required_capabilities"])  # type: ignore[index]
            provenance = tuple(str(item) for item in value["configuration_provenance"])  # type: ignore[index]
            return ModelProfile(
                version=int(value.get("version", -1)),
                name=str(value["name"]),
                provider=str(value["provider"]),
                model=str(value["model"]),
                required_capabilities=capabilities,
                min_context_window_tokens=int(value["min_context_window_tokens"]),
                max_output_tokens=int(value["max_output_tokens"]),
                configuration_provenance=provenance,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelProfileError("profile fields are malformed") from exc

    def validate(self, profile: ModelProfile, registry: SQLiteProviderRegistry) -> str:
        try:
            capability = registry.get(profile.provider)
        except KeyError as exc:
            raise ModelProfileError(
                f"profile provider is not registered: {profile.provider}"
            ) from exc

        if profile.model not in capability.models:
            raise ModelProfileError(
                f"profile model is not registered: {profile.provider}/{profile.model}"
            )
        missing = set(profile.required_capabilities) - set(capability.capabilities)
        if missing:
            raise ModelProfileError(
                f"profile missing provider capabilities: {sorted(missing)}"
            )
        if capability.context_window_tokens < profile.min_context_window_tokens:
            raise ModelProfileError("provider context window is below profile requirement")
        if capability.max_output_tokens < profile.max_output_tokens:
            raise ModelProfileError("provider output bound is below profile requirement")
        return registry.digest()
