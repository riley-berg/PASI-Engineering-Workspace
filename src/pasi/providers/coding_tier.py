from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pasi.providers.ollama import OllamaProvider
from pasi.providers.protocol import ChatMessage
from pasi.providers.profiles import ModelProfile, ModelProfileError, ModelProfileManager
from pasi.providers.registry import SQLiteProviderRegistry


CODING_TIER_VERSION = 1


@dataclass(frozen=True)
class CodingTierSmokeResult:
    profile_name: str
    profile_digest: str
    provider: str
    model: str
    registry_digest: str
    available: bool
    passed: bool
    reason: str
    checked_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "tier_version": CODING_TIER_VERSION,
            "profile_name": self.profile_name,
            "profile_digest": self.profile_digest,
            "provider": self.provider,
            "model": self.model,
            "registry_digest": self.registry_digest,
            "available": self.available,
            "passed": self.passed,
            "reason": self.reason,
            "checked_at": self.checked_at,
        }


class CodingTierError(ValueError):
    """Raised when the configured coding tier cannot be validated."""


class LocalCodingTier:
    """Validate and smoke-test the configured local coding profile."""

    def __init__(self, registry: SQLiteProviderRegistry) -> None:
        self.registry = registry
        self.profile_manager = ModelProfileManager()

    def load_profile(self, path: Path | str) -> ModelProfile:
        profile = self.profile_manager.load(path)
        if "code" not in set(profile.required_capabilities):
            raise CodingTierError("coding tier requires the code capability")
        if "json" not in set(profile.required_capabilities):
            raise CodingTierError("coding tier requires structured JSON capability")
        self.profile_manager.validate(profile, self.registry)
        return profile

    def smoke(
        self,
        profile: ModelProfile,
        *,
        provider: OllamaProvider,
        prompt: str = "Return JSON with keys status and result; status must be ok.",
    ) -> CodingTierSmokeResult:
        checked_at = datetime.now(timezone.utc).isoformat()
        registry_digest = self.registry.digest()

        try:
            self.profile_manager.validate(profile, self.registry)
        except ModelProfileError as exc:
            return CodingTierSmokeResult(
                profile_name=profile.name,
                profile_digest=profile.digest(),
                provider=profile.provider,
                model=profile.model,
                registry_digest=registry_digest,
                available=False,
                passed=False,
                reason=f"profile_invalid:{exc}",
                checked_at=checked_at,
            )

        if provider.name != profile.provider:
            return CodingTierSmokeResult(
                profile_name=profile.name,
                profile_digest=profile.digest(),
                provider=profile.provider,
                model=profile.model,
                registry_digest=registry_digest,
                available=False,
                passed=False,
                reason="provider_adapter_mismatch",
                checked_at=checked_at,
            )

        health = provider.health()
        models = health.get("models", []) if isinstance(health, dict) else []
        available = bool(health.get("available")) if isinstance(health, dict) else False
        model_available = any(
            isinstance(item, dict) and item.get("name") == profile.model
            for item in models
        )

        if not available or not model_available:
            return CodingTierSmokeResult(
                profile_name=profile.name,
                profile_digest=profile.digest(),
                provider=profile.provider,
                model=profile.model,
                registry_digest=registry_digest,
                available=False,
                passed=False,
                reason="configured_model_not_reported_available",
                checked_at=checked_at,
            )

        response = provider.generate(
            [ChatMessage(role="user", content=prompt)],
            model=profile.model,
        )
        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError:
            return CodingTierSmokeResult(
                profile_name=profile.name,
                profile_digest=profile.digest(),
                provider=profile.provider,
                model=profile.model,
                registry_digest=registry_digest,
                available=True,
                passed=False,
                reason="provider_returned_non_json",
                checked_at=checked_at,
            )

        passed = isinstance(payload, dict) and payload.get("status") == "ok"
        return CodingTierSmokeResult(
            profile_name=profile.name,
            profile_digest=profile.digest(),
            provider=profile.provider,
            model=profile.model,
            registry_digest=registry_digest,
            available=True,
            passed=passed,
            reason="ok" if passed else "structured_output_contract_failed",
            checked_at=checked_at,
        )
