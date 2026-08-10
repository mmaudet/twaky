"""Unit tests for JmapPollingEventSource.

Uses ``httpx.MockTransport`` to intercept all HTTP calls — no live JMAP
server required.  The repository module is monkeypatched with a ``_FakeState``
stand-in so no DB is needed either.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from twaky.sentinels.sources.jmap_poll import JmapPollingEventSource

# ---------------------------------------------------------------------------
# JMAP fixture responses  (shapes verified in spec §11 POC)
# ---------------------------------------------------------------------------

SESSION_RESPONSE: dict[str, Any] = {
    "primaryAccounts": {
        "urn:ietf:params:jmap:mail": "acct-001",
    },
    "apiUrl": "https://jmap.example.com/jmap",
    "capabilities": {
        "urn:ietf:params:jmap:mail": {},
    },
}

MAILBOXES_RESPONSE: dict[str, Any] = {
    "methodResponses": [
        [
            "Mailbox/get",
            {
                "accountId": "acct-001",
                "list": [
                    {"id": "mbox-inbox", "name": "Inbox", "role": "inbox"},
                    {"id": "mbox-sent", "name": "Sent", "role": "sent"},
                ],
                "notFound": [],
            },
            "0",
        ]
    ]
}

SEED_RESPONSE: dict[str, Any] = {
    "methodResponses": [
        [
            "Email/query",
            {
                "accountId": "acct-001",
                "queryState": "state-0000",
                "canCalculateChanges": True,
                "position": 0,
                "ids": [],
                "total": 0,
            },
            "0",
        ]
    ]
}

CHANGES_HAS_ONE: dict[str, Any] = {
    "methodResponses": [
        [
            "Email/changes",
            {
                "accountId": "acct-001",
                "oldState": "state-0000",
                "newState": "state-0001",
                "hasMoreChanges": False,
                "created": ["eml-42"],
                "updated": [],
                "destroyed": [],
            },
            "0",
        ]
    ]
}

CHANGES_EMPTY: dict[str, Any] = {
    "methodResponses": [
        [
            "Email/changes",
            {
                "accountId": "acct-001",
                "oldState": "state-0001",
                "newState": "state-0001",
                "hasMoreChanges": False,
                "created": [],
                "updated": [],
                "destroyed": [],
            },
            "0",
        ]
    ]
}

EMAIL_GET: dict[str, Any] = {
    "methodResponses": [
        [
            "Email/get",
            {
                "accountId": "acct-001",
                "list": [
                    {
                        "id": "eml-42",
                        "threadId": "thr-1",
                        "mailboxIds": {"mbox-inbox": True},
                        "keywords": {},
                        "from": [{"name": "Alice", "email": "alice@example.com"}],
                        "to": [{"name": "Bob", "email": "bob@example.com"}],
                        "cc": [],
                        "subject": "hello",
                        "receivedAt": "2026-08-10T10:00:00Z",
                        "preview": "Hello there",
                        "textBody": [
                            {
                                "partId": "1",
                                "blobId": "b-1",
                                "size": 11,
                                "type": "text/plain",
                            }
                        ],
                        "htmlBody": [],
                        "bodyValues": {
                            "1": {
                                "value": "Hello there",
                                "isEncodingProblem": False,
                                "isTruncated": False,
                            }
                        },
                        "headers": [],
                        "hasAttachment": False,
                    }
                ],
                "notFound": [],
            },
            "0",
        ]
    ]
}


# ---------------------------------------------------------------------------
# Fake repository stand-in
# ---------------------------------------------------------------------------


class _FakeState:
    """In-memory stand-in for the ``repository`` module.

    Stores a single ``jmap_last_state`` value.  Implements the two methods
    used by ``JmapPollingEventSource``.
    """

    def __init__(self, initial_value: str | None = None) -> None:
        self.value = initial_value

    def get(self, name: str) -> Any:
        """Return a minimal SentinelConfig-like object."""

        class _Row:
            config_values: dict[str, Any]

            def __init__(self, val: str | None) -> None:
                self.config_values = {} if val is None else {"jmap_last_state": val}

        return _Row(self.value)

    def update_config_value(self, name: str, key: str, value: Any) -> None:
        """Store the value."""
        self.value = value


# ---------------------------------------------------------------------------
# Mock transport builder
# ---------------------------------------------------------------------------


def _make_response(data: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        headers={"content-type": "application/json"},
        content=json.dumps(data).encode(),
    )


def _build_transport(
    *,
    seed_ok: bool,
    then_changes: list[dict[str, Any]],
) -> httpx.MockTransport:
    """Build a MockTransport that sequences JMAP responses.

    Call order:
      GET /jmap/session       → SESSION_RESPONSE
      POST /jmap (Mailbox/get)→ MAILBOXES_RESPONSE
      POST /jmap (Email/query) → SEED_RESPONSE   (only when seed_ok=True)
      POST /jmap (Email/changes) → then_changes[0], then_changes[1], ...
      POST /jmap (Email/get)   → EMAIL_GET       (after first changes that has created)
    """
    responses: list[httpx.Response] = [
        _make_response(SESSION_RESPONSE),
        _make_response(MAILBOXES_RESPONSE),
    ]
    if seed_ok:
        responses.append(_make_response(SEED_RESPONSE))
    for changes_resp in then_changes:
        responses.append(_make_response(changes_resp))
        # After a non-empty changes response, expect Email/get
        if changes_resp["methodResponses"][0][1].get("created"):
            responses.append(_make_response(EMAIL_GET))

    idx = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal idx
        if idx >= len(responses):
            # Return empty changes to avoid IndexError if polled more times
            return _make_response(CHANGES_EMPTY)
        resp = responses[idx]
        idx += 1
        return resp

    return httpx.MockTransport(_handler)


# ---------------------------------------------------------------------------
# Helper: build a patched AsyncClient factory that injects the transport
# ---------------------------------------------------------------------------


def _make_client_factory(transport: httpx.MockTransport):  # type: ignore[no-untyped-def]
    """Return a replacement for ``httpx.AsyncClient`` that uses *transport*.

    The factory wraps the real ``AsyncClient.__init__`` so that all kwargs
    pass through, but ``transport`` is forced to the mock.
    """
    _orig_init = httpx.AsyncClient.__init__

    class _PatchedClient(httpx.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            _orig_init(self, *args, **kwargs)

    return _PatchedClient


# ---------------------------------------------------------------------------
# Test 1: seed run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seeds_state_on_first_run() -> None:
    """No prior state → seed Email/query, persist queryState, yield no events."""
    fake_state = _FakeState(initial_value=None)
    transport = _build_transport(seed_ok=True, then_changes=[])
    patched_client = _make_client_factory(transport)

    source = JmapPollingEventSource(
        sentinel_name="mail",
        session_url="https://jmap.example.com/jmap/session",
        bearer_token="tok-test",
        account_email="user@example.com",
        poll_interval_s=1,
    )

    stop = asyncio.Event()
    events: list[Any] = []

    with (
        patch("twaky.sentinels.sources.jmap_poll.repository", fake_state),
        patch("twaky.sentinels.sources.jmap_poll.httpx.AsyncClient", patched_client),
    ):

        async def _collect() -> None:
            async for event, _ack in source.stream(stop_event=stop):
                events.append(event)

        task = asyncio.create_task(_collect())
        # Give the source time to complete seed run
        await asyncio.sleep(0.3)
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except TimeoutError:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, StopAsyncIteration):
                pass

    assert events == [], f"Expected no events on seed run, got {events}"
    assert fake_state.value == "state-0000", (
        f"Expected persisted state 'state-0000', got {fake_state.value!r}"
    )


# ---------------------------------------------------------------------------
# Test 2: delta poll yields one event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delta_yields_one_event_and_persists_new_state() -> None:
    """With prior state → Email/changes returns one created id → yield one event."""
    fake_state = _FakeState(initial_value="state-0000")
    transport = _build_transport(
        seed_ok=False,
        then_changes=[CHANGES_HAS_ONE, CHANGES_EMPTY],
    )
    patched_client = _make_client_factory(transport)

    source = JmapPollingEventSource(
        sentinel_name="mail",
        session_url="https://jmap.example.com/jmap/session",
        bearer_token="tok-test",
        account_email="user@example.com",
        poll_interval_s=60,  # long sleep — we'll stop after the first yield
    )

    stop = asyncio.Event()
    events: list[Any] = []

    with (
        patch("twaky.sentinels.sources.jmap_poll.repository", fake_state),
        patch("twaky.sentinels.sources.jmap_poll.httpx.AsyncClient", patched_client),
    ):

        async def _collect() -> None:
            async for event, ack in source.stream(stop_event=stop):
                events.append(event)
                await ack()
                stop.set()  # stop after first event

        await asyncio.wait_for(_collect(), timeout=5.0)

    assert len(events) == 1, f"Expected 1 event, got {len(events)}"
    ev = events[0]
    assert ev["source_kind"] == "jmap_poll"
    assert ev["message_id"] == "eml-42"
    assert ev["payload"]["email"]["subject"] == "hello"
    assert fake_state.value == "state-0001", (
        f"Expected persisted state 'state-0001', got {fake_state.value!r}"
    )
