"""Unit tests for twaky.oauth.refresh_manager.

Uses ``httpx.MockTransport`` to intercept token-endpoint HTTP calls and an
in-memory fake repository — no DB or network required.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest

import twaky.oauth.refresh_manager as rm_module
from twaky.oauth.models import OAuthCredential
from twaky.oauth.refresh_manager import (
    RefreshFailed,
    RefreshManager,
    get_manager,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOKEN_ENDPOINT = "https://auth.example.com/token"


def _make_cred(
    *,
    access_token_enc: str | None = None,
    access_token_expires_at: datetime | None = None,
    refresh_token_enc: str | None = "enc-refresh-token",
) -> OAuthCredential:
    """Build a minimal OAuthCredential for tests."""
    now = datetime.now(UTC)
    return OAuthCredential(
        id=uuid4(),
        sentinel_name="mail",
        provider="jmap",
        client_id="twaky-mail-sentinel",
        token_endpoint=_TOKEN_ENDPOINT,
        session_url="https://jmap.example.com/jmap/session",
        scope="openid profile email offline_access",
        refresh_token_enc=refresh_token_enc,
        access_token_enc=access_token_enc,
        access_token_expires_at=access_token_expires_at,
        account_email="user@example.com",
        account_name="Test User",
        last_refresh_at=None,
        last_refresh_error=None,
        created_at=now,
        updated_at=now,
    )


class _FakeRepo:
    """In-memory stand-in for twaky.oauth.repository."""

    def __init__(self, cred: OAuthCredential | None = None) -> None:
        self._cred = cred
        self.update_calls: list[dict[str, Any]] = []
        self.set_error_calls: list[tuple[str, str]] = []

    def get(self, sentinel_name: str) -> OAuthCredential | None:
        return self._cred

    def update_after_refresh(
        self,
        *,
        sentinel_name: str,
        access_token_enc: str,
        access_token_expires_at: datetime | None,
        refresh_token_enc: str | None = None,
    ) -> OAuthCredential:
        self.update_calls.append(
            {
                "sentinel_name": sentinel_name,
                "access_token_enc": access_token_enc,
                "access_token_expires_at": access_token_expires_at,
                "refresh_token_enc": refresh_token_enc,
            }
        )
        assert self._cred is not None
        # Return a minimally updated credential
        from dataclasses import replace

        self._cred = replace(
            self._cred,
            access_token_enc=access_token_enc,
            access_token_expires_at=access_token_expires_at,
        )
        return self._cred

    def set_error(self, sentinel_name: str, error: str) -> None:
        self.set_error_calls.append((sentinel_name, error))


def _make_transport_handler(responses: list[httpx.Response]):
    """Build a MockTransport that pops from *responses* on each request."""
    call_count = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return responses.pop(0)

    transport = httpx.MockTransport(_handler)
    # Attach counter as an attribute for test assertions
    transport._call_count_ref = lambda: call_count  # type: ignore[attr-defined]
    return transport


def _json_response(
    data: dict[str, Any],
    status: int = 200,
) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        headers={"content-type": "application/json"},
        content=json.dumps(data).encode(),
    )


def _make_client_factory(transport: httpx.MockTransport):  # type: ignore[no-untyped-def]
    """Return a patched AsyncClient class that injects *transport*."""
    _orig_init = httpx.AsyncClient.__init__

    class _PatchedClient(httpx.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            _orig_init(self, *args, **kwargs)

    return _PatchedClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_managers():
    """Wipe the module-level _MANAGERS dict between tests for isolation."""
    rm_module._MANAGERS.clear()
    yield
    rm_module._MANAGERS.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_avoids_network() -> None:
    """A warm in-process cache returns the token without touching the network."""
    call_count = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return _json_response(
            {"access_token": "should-not-be-called", "expires_in": 3600}
        )

    transport = httpx.MockTransport(_handler)
    patched_client = _make_client_factory(transport)
    fake_repo = _FakeRepo(cred=None)

    manager = RefreshManager("mail")
    # Pre-seed the in-process cache
    manager._cached_token = "cached-access-token"
    manager._cached_at = time.monotonic()

    with (
        patch("twaky.oauth.refresh_manager.repository", fake_repo),
        patch("twaky.oauth.refresh_manager.httpx.AsyncClient", patched_client),
    ):
        result = await manager.get_access_token()

    assert result == "cached-access-token"
    assert call_count == 0, "MockTransport should NOT have been called on a cache hit"


@pytest.mark.asyncio
async def test_expired_triggers_refresh() -> None:
    """When access_token_expires_at is in the past, a refresh is triggered."""
    expired_at = datetime.now(UTC) - timedelta(seconds=1)
    cred = _make_cred(
        access_token_enc="enc-old-access",
        access_token_expires_at=expired_at,
    )
    fake_repo = _FakeRepo(cred=cred)

    responses = [_json_response({"access_token": "new-at", "expires_in": 3600})]
    transport = _make_transport_handler(responses)
    patched_client = _make_client_factory(transport)

    manager = RefreshManager("mail")

    with (
        patch("twaky.oauth.refresh_manager.repository", fake_repo),
        patch("twaky.oauth.refresh_manager.httpx.AsyncClient", patched_client),
        patch("twaky.oauth.refresh_manager.decrypt", return_value="plaintext-refresh"),
        patch("twaky.oauth.refresh_manager.encrypt", return_value="enc-new-access"),
    ):
        result = await manager.get_access_token()

    assert result == "new-at"
    assert len(fake_repo.update_calls) == 1, (
        "update_after_refresh should have been called once"
    )
    assert fake_repo.update_calls[0]["sentinel_name"] == "mail"


@pytest.mark.asyncio
async def test_refresh_rotates_when_server_provides_new_refresh() -> None:
    """When server returns a new refresh_token, update_after_refresh gets a non-None enc value."""
    cred = _make_cred(
        access_token_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    fake_repo = _FakeRepo(cred=cred)

    responses = [
        _json_response(
            {
                "access_token": "new-at",
                "expires_in": 3600,
                "refresh_token": "new-rt",
            }
        )
    ]
    transport = _make_transport_handler(responses)
    patched_client = _make_client_factory(transport)

    manager = RefreshManager("mail")

    encrypted_values: list[str] = []

    def _fake_encrypt(plaintext: str) -> str:
        encrypted_values.append(plaintext)
        return f"enc:{plaintext}"

    with (
        patch("twaky.oauth.refresh_manager.repository", fake_repo),
        patch("twaky.oauth.refresh_manager.httpx.AsyncClient", patched_client),
        patch("twaky.oauth.refresh_manager.decrypt", return_value="old-rt-plaintext"),
        patch("twaky.oauth.refresh_manager.encrypt", side_effect=_fake_encrypt),
    ):
        await manager.get_access_token()

    assert len(fake_repo.update_calls) == 1
    call = fake_repo.update_calls[0]
    assert call["refresh_token_enc"] is not None, (
        "Should have encrypted the new refresh_token"
    )
    assert call["refresh_token_enc"] == "enc:new-rt"


@pytest.mark.asyncio
async def test_refresh_keeps_old_refresh_when_server_omits_it() -> None:
    """When server does NOT return a refresh_token, update_after_refresh gets refresh_token_enc=None."""
    cred = _make_cred(
        access_token_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    fake_repo = _FakeRepo(cred=cred)

    responses = [
        _json_response(
            {
                "access_token": "new-at",
                "expires_in": 3600,
                # No refresh_token key
            }
        )
    ]
    transport = _make_transport_handler(responses)
    patched_client = _make_client_factory(transport)

    manager = RefreshManager("mail")

    with (
        patch("twaky.oauth.refresh_manager.repository", fake_repo),
        patch("twaky.oauth.refresh_manager.httpx.AsyncClient", patched_client),
        patch("twaky.oauth.refresh_manager.decrypt", return_value="old-rt-plaintext"),
        patch("twaky.oauth.refresh_manager.encrypt", return_value="enc-new-at"),
    ):
        await manager.get_access_token()

    assert len(fake_repo.update_calls) == 1
    assert fake_repo.update_calls[0]["refresh_token_enc"] is None, (
        "refresh_token_enc should be None when server does not rotate"
    )


@pytest.mark.asyncio
async def test_invalid_grant_records_error_and_raises() -> None:
    """A 400 invalid_grant response records the error and raises RefreshFailed."""
    cred = _make_cred(
        access_token_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    fake_repo = _FakeRepo(cred=cred)

    responses = [_json_response({"error": "invalid_grant"}, status=400)]
    transport = _make_transport_handler(responses)
    patched_client = _make_client_factory(transport)

    manager = RefreshManager("mail")

    with (
        patch("twaky.oauth.refresh_manager.repository", fake_repo),
        patch("twaky.oauth.refresh_manager.httpx.AsyncClient", patched_client),
        patch("twaky.oauth.refresh_manager.decrypt", return_value="old-rt"),
        patch("twaky.oauth.refresh_manager.encrypt", return_value="enc-new-at"),
        pytest.raises(RefreshFailed, match="invalid_grant"),
    ):
        await manager.get_access_token()

    assert len(fake_repo.set_error_calls) == 1
    assert fake_repo.set_error_calls[0] == ("mail", "invalid_grant")
    assert len(fake_repo.update_calls) == 0


@pytest.mark.asyncio
async def test_5xx_records_network_and_raises() -> None:
    """A 500 response with non-JSON body records http_500 and raises RefreshFailed."""
    cred = _make_cred(
        access_token_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    fake_repo = _FakeRepo(cred=cred)

    responses = [
        httpx.Response(
            status_code=500,
            headers={"content-type": "text/plain"},
            content=b"Internal Server Error",
        )
    ]
    transport = _make_transport_handler(responses)
    patched_client = _make_client_factory(transport)

    manager = RefreshManager("mail")

    with (
        patch("twaky.oauth.refresh_manager.repository", fake_repo),
        patch("twaky.oauth.refresh_manager.httpx.AsyncClient", patched_client),
        patch("twaky.oauth.refresh_manager.decrypt", return_value="old-rt"),
        patch("twaky.oauth.refresh_manager.encrypt", return_value="enc-new-at"),
        pytest.raises(RefreshFailed, match="http_500"),
    ):
        await manager.get_access_token()

    assert len(fake_repo.set_error_calls) == 1
    assert fake_repo.set_error_calls[0] == ("mail", "http_500")


@pytest.mark.asyncio
async def test_single_flight_lock_serializes_concurrent_refresh() -> None:
    """Three concurrent get_access_token() calls trigger only one HTTP request."""
    cred = _make_cred(
        access_token_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    fake_repo = _FakeRepo(cred=cred)

    http_call_count = 0

    async def _slow_handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_call_count
        http_call_count += 1
        await asyncio.sleep(0.1)  # 100 ms sleep to allow concurrency to stack up
        return _json_response({"access_token": "single-at", "expires_in": 3600})

    # Use an async transport by wrapping in a sync handler that schedules it
    sync_call_count = 0

    def _sync_handler(request: httpx.Request) -> httpx.Response:
        nonlocal sync_call_count
        sync_call_count += 1
        # Note: async sleep is not possible in a sync handler, but the lock
        # ensures serialization even without sleep. We just count calls.
        return _json_response({"access_token": "single-at", "expires_in": 3600})

    transport = httpx.MockTransport(_sync_handler)
    patched_client = _make_client_factory(transport)

    manager = RefreshManager("mail")

    with (
        patch("twaky.oauth.refresh_manager.repository", fake_repo),
        patch("twaky.oauth.refresh_manager.httpx.AsyncClient", patched_client),
        patch("twaky.oauth.refresh_manager.decrypt", return_value="old-rt"),
        patch("twaky.oauth.refresh_manager.encrypt", return_value="enc-new-at"),
    ):
        results = await asyncio.gather(
            manager.get_access_token(),
            manager.get_access_token(),
            manager.get_access_token(),
        )

    assert all(r == "single-at" for r in results), (
        f"All results should be 'single-at': {results}"
    )
    assert sync_call_count == 1, (
        f"MockTransport should be called exactly once (single-flight), got {sync_call_count}"
    )


@pytest.mark.asyncio
async def test_sync_wrapper_returns_same_result() -> None:
    """sync_get_access_token() called from a thread returns the same value as the async path."""
    future_at = datetime.now(UTC) + timedelta(hours=1)
    cred = _make_cred(
        access_token_enc="enc-fresh-at",
        access_token_expires_at=future_at,
    )
    fake_repo = _FakeRepo(cred=cred)

    manager = RefreshManager("mail")

    with (
        patch("twaky.oauth.refresh_manager.repository", fake_repo),
        patch("twaky.oauth.refresh_manager.decrypt", return_value="plaintext-fresh-at"),
    ):
        # Run sync_get_access_token from a thread pool (matches the T8 runtime pattern)
        result = await asyncio.to_thread(manager.sync_get_access_token)

    assert result == "plaintext-fresh-at"


def test_get_manager_returns_same_instance() -> None:
    """get_manager returns the same instance for the same name, different for different names."""
    a1 = get_manager("a")
    a2 = get_manager("a")
    b = get_manager("b")

    assert a1 is a2, (
        "get_manager('a') should return the same instance on repeated calls"
    )
    assert a1 is not b, (
        "get_manager('a') and get_manager('b') should be distinct instances"
    )
