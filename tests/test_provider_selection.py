from pathlib import Path

import pytest

from pasi.providers.registry import ProviderCapability, SQLiteProviderRegistry
from pasi.providers.selection import (
    DeterministicProviderSelector,
    ProviderRequirements,
    ProviderSelectionError,
    StaleProviderSelection,
)
from pasi.providers.selection_store import SQLiteProviderSelectionStore


def cap(name, capabilities, context=32768, output=4096, models=("m1", "m2")):
    return ProviderCapability(
        provider=name,
        registry_version=1,
        models=models,
        capabilities=tuple(capabilities),
        context_window_tokens=context,
        max_output_tokens=min(output, context - 1),
        configuration_provenance=f"test:{name}",
        metadata={},
    )


def make_registry(tmp_path: Path):
    registry = SQLiteProviderRegistry(tmp_path / "providers.db")
    registry.register(cap("fast", ("chat", "code")))
    registry.register(cap("small", ("chat",), context=4096))
    registry.register(cap("json", ("chat", "json"), models=("json-model",)))
    return registry


def test_selector_uses_only_authoritatively_eligible_providers(tmp_path):
    registry = make_registry(tmp_path)
    selection = DeterministicProviderSelector().select(
        registry,
        ProviderRequirements(
            required_capabilities=("code",),
            min_context_window_tokens=8192,
            max_output_tokens=2048,
        ),
        advisory_preferences={"fast": 5, "small": 100},
    )
    assert selection.provider == "fast"
    assert selection.model == "m1"
    assert "small" not in selection.eligible


def test_selector_is_deterministic_and_persistable(tmp_path):
    registry = SQLiteProviderRegistry(tmp_path / "providers.db")
    registry.register(cap("b", ("chat",)))
    registry.register(cap("a", ("chat",)))

    selector = DeterministicProviderSelector()
    requirements = ProviderRequirements(("chat",))
    first = selector.select(registry, requirements, advisory_preferences={"a": 1, "b": 1})
    second = selector.select(registry, requirements, advisory_preferences={"a": 1, "b": 1})
    assert (first.provider, first.model, first.registry_digest) == (
        second.provider,
        second.model,
        second.registry_digest,
    )
    assert first.provider == "a"

    store = SQLiteProviderSelectionStore(tmp_path / "selections.db")
    assert store.record(first) == 1
    assert store.latest() == first


def test_selector_rejects_empty_eligible_set(tmp_path):
    registry = SQLiteProviderRegistry(tmp_path / "providers.db")
    registry.register(cap("chat-only", ("chat",), context=2048))
    with pytest.raises(ProviderSelectionError):
        DeterministicProviderSelector().select(
            registry,
            ProviderRequirements(
                required_capabilities=("code",),
                min_context_window_tokens=8192,
                max_output_tokens=4096,
            ),
        )


def test_selection_rejects_stale_registry(tmp_path):
    path = tmp_path / "providers.db"
    registry = SQLiteProviderRegistry(path)
    registry.register(cap("a", ("chat",)))
    selection = DeterministicProviderSelector().select(
        registry,
        ProviderRequirements(("chat",)),
    )

    second_registry = SQLiteProviderRegistry(path)
    second_registry.register(cap("b", ("chat",)))

    with pytest.raises(StaleProviderSelection):
        selection.assert_fresh(second_registry)


def test_selection_schema_exists():
    assert (Path(__file__).resolve().parents[1] / "schemas" / "provider-selection-v1.json").exists()
