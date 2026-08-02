"""Pure validation + effective_model tests (no DB)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from twaky.agents_config.models import AgentConfig
from twaky.agents_config.service import ValidationError, effective_model, validate_patch


def _cfg(model: str | None = None) -> AgentConfig:
    return AgentConfig(
        id="plume",
        display_name="Plume",
        role="specialist",
        system_prompt="hi",
        model=model,
        temperature=None,
        updated_at=datetime.now(UTC),
    )


class TestEffectiveModel:
    def test_returns_row_model_when_set(self):
        assert effective_model(_cfg(model="openai/gpt-4o")) == "openai/gpt-4o"

    def test_falls_back_to_settings_when_null(self, monkeypatch):
        from twaky import config as _cfg_mod

        monkeypatch.setattr(_cfg_mod.settings, "model", "sentinel-default")
        assert effective_model(_cfg(model=None)) == "sentinel-default"


class TestValidatePatchSystemPrompt:
    def test_ok(self):
        out = validate_patch({"system_prompt": "  hello world  "})
        assert out == {"system_prompt": "hello world"}

    def test_empty_raises(self):
        with pytest.raises(ValidationError) as exc:
            validate_patch({"system_prompt": "   "})
        assert exc.value.field == "system_prompt"

    def test_too_long_raises(self):
        with pytest.raises(ValidationError):
            validate_patch({"system_prompt": "x" * 8001})

    def test_non_string_raises(self):
        with pytest.raises(ValidationError):
            validate_patch({"system_prompt": 42})


class TestValidatePatchTemperature:
    def test_ok_low(self):
        assert validate_patch({"temperature": 0.0}) == {"temperature": 0.0}

    def test_ok_high(self):
        assert validate_patch({"temperature": 2.0}) == {"temperature": 2.0}

    def test_ok_null(self):
        assert validate_patch({"temperature": None}) == {"temperature": None}

    def test_below_zero_raises(self):
        with pytest.raises(ValidationError):
            validate_patch({"temperature": -0.1})

    def test_above_two_raises(self):
        with pytest.raises(ValidationError):
            validate_patch({"temperature": 2.01})

    def test_bool_raises(self):
        with pytest.raises(ValidationError):
            validate_patch({"temperature": True})


class TestValidatePatchModel:
    def test_ok_string(self):
        assert validate_patch({"model": " openai/gpt-4o "}) == {
            "model": "openai/gpt-4o"
        }

    def test_ok_null(self):
        assert validate_patch({"model": None}) == {"model": None}

    def test_empty_string_raises(self):
        with pytest.raises(ValidationError):
            validate_patch({"model": "   "})


class TestValidatePatchBody:
    def test_empty_body_raises(self):
        with pytest.raises(ValidationError) as exc:
            validate_patch({})
        assert "at least one field required" in exc.value.message

    def test_unknown_field_raises(self):
        with pytest.raises(ValidationError):
            validate_patch({"tools": ["read_email"]})

    def test_multi_field_patch_ok(self):
        out = validate_patch({"system_prompt": "hi", "temperature": 0.5, "model": None})
        assert out == {"system_prompt": "hi", "temperature": 0.5, "model": None}
