"""Business logic layer above the repository."""

from __future__ import annotations

from typing import Any

from twaky.agents_config.models import AgentConfig
from twaky.config import settings


class ValidationError(ValueError):
    """Raised when a patch payload violates constraints."""

    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


def effective_model(cfg: AgentConfig) -> str:
    """Resolved model — either the row's override or the daemon-side default."""
    return cfg.model or settings.model


def validate_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Return the normalized patch on success; raise ValidationError on failure."""
    if not patch:
        raise ValidationError("_body", "at least one field required")

    allowed = {"system_prompt", "model", "temperature"}
    unknown = set(patch) - allowed
    if unknown:
        raise ValidationError(min(unknown), "unknown field")

    normalized: dict[str, Any] = {}

    if "system_prompt" in patch:
        sp = patch["system_prompt"]
        if not isinstance(sp, str):
            raise ValidationError("system_prompt", "must be a string")
        sp = sp.strip()
        if not sp:
            raise ValidationError("system_prompt", "must not be empty")
        if len(sp) > 8000:
            raise ValidationError("system_prompt", "must be at most 8000 characters")
        normalized["system_prompt"] = sp

    if "temperature" in patch:
        temp = patch["temperature"]
        if temp is None:
            normalized["temperature"] = None
        else:
            if not isinstance(temp, (int, float)) or isinstance(temp, bool):
                raise ValidationError("temperature", "must be a number or null")
            if temp < 0.0 or temp > 2.0:
                raise ValidationError("temperature", "must be between 0.0 and 2.0")
            normalized["temperature"] = float(temp)

    if "model" in patch:
        model = patch["model"]
        if model is None:
            normalized["model"] = None
        else:
            if not isinstance(model, str):
                raise ValidationError("model", "must be a string or null")
            stripped = model.strip()
            if not stripped:
                raise ValidationError("model", "must not be empty")
            normalized["model"] = stripped

    return normalized


__all__ = ["ValidationError", "effective_model", "validate_patch"]
