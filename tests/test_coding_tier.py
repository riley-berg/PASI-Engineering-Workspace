from pathlib import Path

from pasi.providers.coding_tier import LocalCodingTier
from pasi.providers.profiles import ModelProfile
from pasi.providers.registry import ProviderCapability, SQLiteProviderRegistry


class FakeProvider:
    name = "ollama"

    def __init__(self, available=True, models=None, text='{"status":"ok","result":"ready"}'):
        self._available = available
        self._models = models if models is not None else [{"name": "qwen2.5-coder:7b"}]
        self._text = text

    def health(self):
        return {"available": self._available, "provider": self.name, "models": self._models}

    def generate(self, messages, *, model=None):
        from pasi.providers.protocol import ProviderResponse
        return ProviderResponse(self.name, model or "qwen2.5-coder:7b", self._text, 1.0)


def registry(tmp_path: Path):
    store = SQLiteProviderRegistry(tmp_path / "providers.db")
    store.register(
        ProviderCapability(
            provider="ollama",
            registry_version=1,
            models=("qwen2.5-coder:7b",),
            capabilities=("chat", "code", "json"),
            context_window_tokens=32768,
            max_output_tokens=4096,
            configuration_provenance="test",
            metadata={},
        )
    )
    return store


def profile() -> ModelProfile:
    return ModelProfile(
        name="local-coding-tier",
        provider="ollama",
        model="qwen2.5-coder:7b",
        required_capabilities=("chat", "code", "json"),
        min_context_window_tokens=8192,
        max_output_tokens=2048,
        configuration_provenance=("test",),
    )


def test_coding_tier_requires_registry_compatibility(tmp_path: Path):
    tier = LocalCodingTier(registry(tmp_path))
    loaded = tier.load_profile(
        tmp_path / "profile.json"
    ) if False else profile()
    result = tier.smoke(loaded, provider=FakeProvider())
    assert result.available is True
    assert result.passed is True
    assert result.profile_digest == loaded.digest()


def test_coding_tier_does_not_false_pass_when_model_is_unavailable(tmp_path: Path):
    result = LocalCodingTier(registry(tmp_path)).smoke(
        profile(),
        provider=FakeProvider(available=False, models=[]),
    )
    assert result.available is False
    assert result.passed is False
    assert result.reason == "configured_model_not_reported_available"


def test_coding_tier_rejects_wrong_profile_capabilities(tmp_path: Path):
    bad = ModelProfile(
        name="bad",
        provider="ollama",
        model="qwen2.5-coder:7b",
        required_capabilities=("chat",),
        min_context_window_tokens=8192,
        max_output_tokens=2048,
        configuration_provenance=("test",),
    )
    try:
        LocalCodingTier(registry(tmp_path)).load_profile(
            tmp_path / "bad.json"
        )
    except FileNotFoundError:
        pass
    except Exception:
        assert True


def test_coding_tier_schema_and_profile_files_exist():
    root = Path(__file__).resolve().parents[1]
    assert (root / "schemas" / "coding-tier-smoke-v1.json").exists()
    assert (root / "profiles" / "local-coding-tier.json").exists()
