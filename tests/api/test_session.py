"""sign_session round-trip + cookie config."""

from __future__ import annotations

import pytest

from twaky.api.session import SESSION_COOKIE_NAME, sign_session, unsign_session


@pytest.fixture(autouse=True)
def _set_secret(monkeypatch):
    monkeypatch.setenv("API_SESSION_SECRET", "test-secret-32bytes-min-abcdefgh")
    # Force settings reload where needed — the session helpers should read
    # settings.api_session_secret at call time, not import time.


class TestSignSession:
    def test_returns_str(self):
        cookie = sign_session("alice@x")
        assert isinstance(cookie, str)
        assert len(cookie) > 20  # signed payload should be non-trivial

    def test_round_trip_recovers_email(self):
        cookie = sign_session("alice@x")
        payload = unsign_session(cookie)
        assert payload is not None
        assert payload["email"] == "alice@x"

    def test_round_trip_recovers_sub(self):
        cookie = sign_session("alice@x", sub="alice-uuid-42")
        payload = unsign_session(cookie)
        assert payload is not None
        assert payload["sub"] == "alice-uuid-42"


class TestUnsignSession:
    def test_returns_none_on_bad_signature(self):
        assert unsign_session("not-a-signed-value") is None

    def test_returns_none_on_empty(self):
        assert unsign_session("") is None


class TestCookieName:
    def test_is_stable(self):
        # Load-bearing for 3b Playwright tests.
        assert SESSION_COOKIE_NAME == "twaky_session"


class TestPublicSeam:
    def test_testing_module_re_exports(self):
        from twaky.api import testing

        assert testing.sign_session is sign_session
        assert testing.SESSION_COOKIE_NAME == SESSION_COOKIE_NAME
