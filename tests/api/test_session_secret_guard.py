"""The API must refuse to start with a session key that can't protect a cookie.

``require_owner`` trusts ``session["email"]`` outright, so a forgeable cookie
is a full auth bypass. ``api_session_secret`` defaults to ``""`` (the non-HTTP
workers import ``settings`` too), so the guard is what stops a silent boot.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# Resolved through the module at call time, never bound at import: several
# tests reload ``twaky.api.session``, which replaces both the function and the
# exception class. A module-level ``from ... import`` would leave this file
# comparing against a stale class and ``pytest.raises`` would not match.
from twaky.api import session as session_mod


class TestCheckSessionSecret:
    def test_empty_secret_is_rejected(self):
        with pytest.raises(session_mod.WeakSessionSecret, match="not set"):
            session_mod.check_session_secret("")

    def test_short_secret_is_rejected(self):
        with pytest.raises(
            session_mod.WeakSessionSecret, match="below the 32-byte minimum"
        ):
            session_mod.check_session_secret("too-short")

    def test_secret_one_byte_below_minimum_is_rejected(self):
        with pytest.raises(session_mod.WeakSessionSecret):
            session_mod.check_session_secret("a" * 31)

    def test_secret_at_minimum_is_accepted(self):
        session_mod.check_session_secret("a" * 32)  # must not raise

    def test_openssl_rand_hex_32_output_is_accepted(self):
        session_mod.check_session_secret("0" * 64)  # must not raise

    def test_length_is_measured_in_bytes_not_characters(self):
        """16 multi-byte chars are 32 characters' worth of entropy, not 32 bytes."""
        session_mod.check_session_secret("é" * 16)  # 32 bytes — accepted
        with pytest.raises(session_mod.WeakSessionSecret):
            session_mod.check_session_secret("é" * 15)  # 30 bytes — rejected


class TestLifespanEnforcement:
    """The guard must be wired into startup, not merely defined."""

    def test_app_startup_fails_with_empty_secret(self, monkeypatch):
        from twaky.api import main

        monkeypatch.setattr(main.settings, "api_session_secret", "")
        with pytest.raises(session_mod.WeakSessionSecret), TestClient(main.app):
            pass  # pragma: no cover - startup raises before the body runs

    def test_app_startup_succeeds_with_a_strong_secret(self, monkeypatch):
        from twaky.api import main

        monkeypatch.setattr(main.settings, "api_session_secret", "x" * 64)
        with TestClient(main.app) as client:
            assert client.get("/healthz").status_code == 200
