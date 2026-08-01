"""Config validation tests — owner email must be required."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from twaky.config import Settings


def test_owner_email_required_missing_raises(monkeypatch):
    monkeypatch.delenv("TWAKY_OWNER_EMAIL", raising=False)
    with pytest.raises(ValidationError) as ei:
        Settings(_env_file=None)  # type: ignore[call-arg]
    assert "twaky_owner_email" in str(ei.value).lower()


def test_owner_email_present_ok(monkeypatch):
    monkeypatch.setenv("TWAKY_OWNER_EMAIL", "alice@example.com")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.twaky_owner_email == "alice@example.com"


def test_agent_exchanges_default_includes_mail(monkeypatch):
    monkeypatch.setenv("TWAKY_OWNER_EMAIL", "alice@example.com")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert "mail:message:received" in s.exchanges
    assert "mail:message:expunged" in s.exchanges
    assert "mail:message:flags:updated" in s.exchanges
    assert "mail:message:moved" in s.exchanges
