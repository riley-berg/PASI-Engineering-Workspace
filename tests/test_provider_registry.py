from pathlib import Path

import pytest

from pasi.providers.registry import (
    DuplicateProvider,
    ProviderCapability,
    ProviderNotFound,
    ProviderRegistryError,
    SQLiteProviderRegistry,
)


def capability(provider="ollama") -> ProviderCapability:
    return ProviderCapability(
        provider=provider,
        registry_version=1,
        models=("qwen2.5-coder:7b",),
        capabilities=("chat", "code", "json"),
        context_window_tokens=32768,
        max_output_tokens=4096,
        configuration_provenance="env:PASI_OLLAMA_URL+env:PASI_OLLAMA_MODEL",
        metadata={"transport": "local-http"},
    )


def test_registry_persists_and_hashes_deterministically(tmp_path: Path):
    path = tmp_path / "providers.db"
    registry = SQLiteProviderRegistry(path)
    registry.register(capability())
    first_digest = registry.digest()

    restarted = SQLiteProviderRegistry(path)
    assert restarted.get("ollama") == capability()
    assert restarted.digest() == first_digest
    assert restarted.list()[0].provider == "ollama"


def test_registry_rejects_duplicates_and_missing_provider(tmp_path: Path):
    registry = SQLiteProviderRegistry(tmp_path / "providers.db")
    registry.register(capability())
    with pytest.raises(DuplicateProvider):
        registry.register(capability())

    with pytest.raises(ProviderNotFound):
        registry.get("missing")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"context_window_tokens": 0},
        {"max_output_tokens": 4096, "context_window_tokens": 2048},
        {"models": ()},
        {"capabilities": ()},
        {"configuration_provenance": ""},
    ],
)
def test_registry_rejects_invalid_capabilities(kwargs):
    base = capability().__dict__.copy()
    base.update(kwargs)
    with pytest.raises(ProviderRegistryError):
        ProviderCapability(**base)


def test_registry_schema_is_present():
    schema = Path(__file__).resolve().parents[1] / "schemas" / "provider-capability-v1.json"
    assert schema.exists()
