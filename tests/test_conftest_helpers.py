"""Unit tests for tests/_conftest_helpers.py."""

from __future__ import annotations

import pytest

from tests._conftest_helpers import destructive_wipe_allowed, skip_reason


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "Yes", "YES"])
def test_allowed_truthy_values(monkeypatch, value: str) -> None:
    monkeypatch.setenv("TWAKY_ALLOW_DESTRUCTIVE_TESTS", value)
    assert destructive_wipe_allowed() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "any_junk"])
def test_denied_falsy_or_missing(monkeypatch, value: str) -> None:
    monkeypatch.setenv("TWAKY_ALLOW_DESTRUCTIVE_TESTS", value)
    assert destructive_wipe_allowed() is False


def test_denied_when_env_unset(monkeypatch) -> None:
    monkeypatch.delenv("TWAKY_ALLOW_DESTRUCTIVE_TESTS", raising=False)
    assert destructive_wipe_allowed() is False


def test_skip_reason_mentions_env_var_and_investigation_doc() -> None:
    reason = skip_reason()
    assert "TWAKY_ALLOW_DESTRUCTIVE_TESTS" in reason
    assert "2026-08-12-spam-decision-purge.md" in reason
