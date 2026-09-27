from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from pasi.providers.registry import ProviderCapability, SQLiteProviderRegistry


class ProviderSelectionError(ValueError):
    """Raised when no registered provider satisfies authoritative requirements."""


class StaleProviderSelection(ProviderSelectionError):
    """Raised when a selection no longer matches the registry digest."""


@dataclass(frozen=True)
class ProviderRequirements:
    required_capabilities: tuple[str, ...]
    min_context_window_tokens: int = 1
    max_output_tokens: int = 4096
    preferred_models: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.required_capabilities:
            raise ProviderSelectionError("at least one capability is required")
        if self.min_context_window_tokens <= 0:
            raise ProviderSelectionError("min_context_window_tokens must be positive")
        if self.max_output_tokens <= 0:
            raise ProviderSelectionError("max_output_tokens must be positive")


@dataclass(frozen=True)
class ProviderSelection:
    provider: str
    model: str
    registry_digest: str
    eligible: tuple[str, ...]
    excluded_reasons: dict[str, str]
    preference_ranks: dict[str, int]
    selected_at: str

    def assert_fresh(self, registry: SQLiteProviderRegistry) -> None:
        current = registry.digest()
        if current != self.registry_digest:
            raise StaleProviderSelection(
                "provider selection registry digest is stale"
            )


class DeterministicProviderSelector:
    """Select a model only from authoritative registry-compatible candidates."""

    def select(
        self,
        registry: SQLiteProviderRegistry,
        requirements: ProviderRequirements,
        *,
        advisory_preferences: Mapping[str, int] | None = None,
    ) -> ProviderSelection:
        digest = registry.digest()
        preferences = dict(advisory_preferences or {})
        excluded: dict[str, str] = {}
        candidates: list[tuple[str, str, int]] = []

        for capability in registry.list():
            provider_id = capability.provider
            missing = sorted(
                set(requirements.required_capabilities)
                - set(capability.capabilities)
            )
            if missing:
                excluded[provider_id] = (
                    f"missing capabilities: {', '.join(missing)}"
                )
                continue
            if (
                capability.context_window_tokens
                < requirements.min_context_window_tokens
            ):
                excluded[provider_id] = "context window below requirement"
                continue
            if capability.max_output_tokens < requirements.max_output_tokens:
                excluded[provider_id] = "max output tokens below requirement"
                continue

            preferred = [
                model
                for model in requirements.preferred_models
                if model in capability.models
            ]
            model = preferred[0] if preferred else sorted(capability.models)[0]
            rank = preferences.get(provider_id, 0)
            if not isinstance(rank, int):
                excluded[provider_id] = "invalid advisory preference"
                continue
            candidates.append((provider_id, model, rank))

        if not candidates:
            raise ProviderSelectionError(
                "no registered provider satisfies authoritative requirements"
            )

        candidates.sort(key=lambda item: (-item[2], item[0], item[1]))
        selected_provider, selected_model, _ = candidates[0]

        return ProviderSelection(
            provider=selected_provider,
            model=selected_model,
            registry_digest=digest,
            eligible=tuple(item[0] for item in candidates),
            excluded_reasons=dict(sorted(excluded.items())),
            preference_ranks={
                provider: int(preferences.get(provider, 0))
                for provider, _, _ in candidates
            },
            selected_at=datetime.now(timezone.utc).isoformat(),
        )


def selection_to_json(selection: ProviderSelection) -> str:
    return json.dumps(
        {
            "provider": selection.provider,
            "model": selection.model,
            "registry_digest": selection.registry_digest,
            "eligible": list(selection.eligible),
            "excluded_reasons": selection.excluded_reasons,
            "preference_ranks": selection.preference_ranks,
            "selected_at": selection.selected_at,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
