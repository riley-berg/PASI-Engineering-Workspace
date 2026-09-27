import json
from pathlib import Path

import pytest

from pasi.providers.profiles import ModelProfile, ModelProfileError, ModelProfileManager
from pasi.providers.registry import ProviderCapability, SQLiteProviderRegistry


def capability():
    return ProviderCapability(
        provider="ollama",
        registry_version=1,
        models=("coder-7b",),
        capabilities=("chat", "code", "json"),
        context_window_tokens=32768,
        max_output_tokens=4096,
        configuration_provenance="test",
        metadata={},
    )


def test_profile_round_trip_digest_and_registry_validation(tmp_path: Path):
    registry = SQLiteProviderRegistry(tmp_path / "providers.db")
    registry.register(capability())
    profile = ModelProfile(
        name="local-coding",
        provider="ollama",
        model="coder-7b",
        required_capabilities=("code", "json"),
        min_context_window_tokens=8192,
        max_output_tokens=2048,
        configuration_provenance=("env:PASI_OLLAMA_URL", "env:PASI_OLLAMA_MODEL"),
    )
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile.to_dict()), encoding="utf-8")

    manager = ModelProfileManager()
    loaded = manager.load(path)
    assert loaded == profile
    assert loaded.digest() == profile.digest()
    assert manager.validate(loaded, registry) == registry.digest()


@pytest.mark.parametrize(
    "model,caps",
    [
        ("missing-model", ("code",)),
        ("coder-7b", ("browser",)),
    ],
)
def test_profile_rejects_registry_mismatch(tmp_path: Path, model, caps):
    registry = SQLiteProviderRegistry(tmp_path / "providers.db")
    registry.register(capability())
    profile = ModelProfile(
        name="bad",
        provider="ollama",
        model=model,
        required_capabilities=caps,
        min_context_window_tokens=8192,
        max_output_tokens=2048,
        configuration_provenance=("env:PASI_OLLAMA_MODEL",),
    )
    with pytest.raises(ModelProfileError):
        ModelProfileManager().validate(profile, registry)


def test_profile_rejects_invalid_bounds():
    with pytest.raises(ModelProfileError):
        ModelProfile(
            name="bad",
            provider="ollama",
            model="coder-7b",
            required_capabilities=("code",),
            min_context_window_tokens=1024,
            max_output_tokens=1024,
            configuration_provenance=("env:PASI_OLLAMA_MODEL",),
        )


def test_profile_schema_exists():
    assert (Path(__file__).resolve().parents[1] / "schemas" / "model-profile-v1.json").exists()
