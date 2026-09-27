from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROVIDER_CAPABILITY_SCHEMA_VERSION = 1
MAX_PROVIDER_NAME = 128
MAX_MODEL_NAME = 256
MAX_CAPABILITIES = 64
MAX_CONFIG_PROVENANCE = 1024
MAX_CONTEXT_TOKENS = 2_000_000
MAX_OUTPUT_TOKENS = 200_000


class ProviderRegistryError(ValueError):
    """Raised when provider capability metadata is invalid."""


class DuplicateProvider(ValueError):
    """Raised when a provider identity is registered twice."""


class ProviderNotFound(KeyError):
    """Raised when a provider identity is absent."""


@dataclass(frozen=True)
class ProviderCapability:
    provider: str
    registry_version: int
    models: tuple[str, ...]
    capabilities: tuple[str, ...]
    context_window_tokens: int
    max_output_tokens: int
    configuration_provenance: str
    metadata: dict[str, str]

    def __post_init__(self) -> None:
        if not self.provider.strip() or len(self.provider) > MAX_PROVIDER_NAME:
            raise ProviderRegistryError("provider name is required and bounded")
        if self.registry_version != PROVIDER_CAPABILITY_SCHEMA_VERSION:
            raise ProviderRegistryError("unsupported provider capability schema version")
        if not self.models or any(
            not model.strip() or len(model) > MAX_MODEL_NAME for model in self.models
        ):
            raise ProviderRegistryError("provider must declare bounded model names")
        if len(set(self.models)) != len(self.models):
            raise ProviderRegistryError("provider model list contains duplicates")
        if not self.capabilities or len(self.capabilities) > MAX_CAPABILITIES:
            raise ProviderRegistryError("provider capabilities are required and bounded")
        if any(not value.strip() for value in self.capabilities):
            raise ProviderRegistryError("provider capabilities cannot be empty")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ProviderRegistryError("provider capability list contains duplicates")
        if (
            self.context_window_tokens <= 0
            or self.context_window_tokens > MAX_CONTEXT_TOKENS
            or self.max_output_tokens <= 0
            or self.max_output_tokens > MAX_OUTPUT_TOKENS
        ):
            raise ProviderRegistryError("provider token bounds are invalid")
        if self.max_output_tokens >= self.context_window_tokens:
            raise ProviderRegistryError(
                "max_output_tokens must be smaller than context_window_tokens"
            )
        if not self.configuration_provenance.strip():
            raise ProviderRegistryError("configuration provenance is required")
        if len(self.configuration_provenance) > MAX_CONFIG_PROVENANCE:
            raise ProviderRegistryError("configuration provenance is too long")
        if not isinstance(self.metadata, dict):
            raise ProviderRegistryError("metadata must be an object")
        if any(
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(value, str)
            for key, value in self.metadata.items()
        ):
            raise ProviderRegistryError("provider metadata must be string-to-string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "registry_version": self.registry_version,
            "models": list(self.models),
            "capabilities": list(self.capabilities),
            "context_window_tokens": self.context_window_tokens,
            "max_output_tokens": self.max_output_tokens,
            "configuration_provenance": self.configuration_provenance,
            "metadata": dict(self.metadata),
        }


class SQLiteProviderRegistry:
    """Durable provider capability registry with deterministic state hashing."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_capability (
                    provider TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL
                )
                """
            )

    def register(self, capability: ProviderCapability) -> ProviderCapability:
        payload = json.dumps(
            capability.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO provider_capability(provider, payload_json)
                    VALUES (?, ?)
                    """,
                    (capability.provider, payload),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateProvider(capability.provider) from exc
        return capability

    def get(self, provider: str) -> ProviderCapability:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM provider_capability
                WHERE provider = ?
                """,
                (provider,),
            ).fetchone()
        if row is None:
            raise ProviderNotFound(provider)
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError as exc:
            raise ProviderRegistryError("stored provider capability is invalid JSON") from exc
        return self._from_mapping(payload)

    def list(self) -> tuple[ProviderCapability, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM provider_capability
                ORDER BY provider ASC
                """
            ).fetchall()
        return tuple(
            self._from_mapping(json.loads(row["payload_json"]))
            for row in rows
        )

    def digest(self) -> str:
        payload = [
            capability.to_dict()
            for capability in self.list()
        ]
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _from_mapping(value: dict[str, Any]) -> ProviderCapability:
        models = value.get("models")
        capabilities = value.get("capabilities")
        if not isinstance(models, list) or not isinstance(capabilities, list):
            raise ProviderRegistryError("stored provider lists must be arrays")
        metadata = value.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ProviderRegistryError("stored provider metadata must be an object")
        return ProviderCapability(
            provider=str(value.get("provider", "")),
            registry_version=int(value.get("registry_version", -1)),
            models=tuple(str(item) for item in models),
            capabilities=tuple(str(item) for item in capabilities),
            context_window_tokens=int(value.get("context_window_tokens", 0)),
            max_output_tokens=int(value.get("max_output_tokens", 0)),
            configuration_provenance=str(
                value.get("configuration_provenance", "")
            ),
            metadata={str(key): str(item) for key, item in metadata.items()},
        )


def provider_capability_schema_digest() -> str:
    payload = {
        "schema_version": PROVIDER_CAPABILITY_SCHEMA_VERSION,
        "max_context_tokens": MAX_CONTEXT_TOKENS,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
