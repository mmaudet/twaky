"""Tests for JmapObserverClient — the async JMAP adapter for the observer."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from twaky.sentinels.mail.jmap_observer_client import JmapObserverClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _jmap_resp(method: str, result: dict[str, Any]) -> httpx.Response:
    """Build a fake JMAP httpx.Response."""
    body = json.dumps({"methodResponses": [[method, result, "0"]]})
    return httpx.Response(
        200, content=body.encode(), headers={"content-type": "application/json"}
    )


class _MockTransport(httpx.MockTransport):
    """Records the last request and returns a canned response."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.last_request: httpx.Request | None = None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.last_request = request
        return self._response

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.last_request = request
        return self._response


def _make(transport: _MockTransport) -> JmapObserverClient:
    return JmapObserverClient(
        api_url="https://jmap.test/jmap",
        access_token="tok",
        account_id="acct-1",
        _transport=transport,
    )


# ---------------------------------------------------------------------------
# query_mailboxes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_mailboxes_returns_list() -> None:
    t = _MockTransport(
        _jmap_resp(
            "Mailbox/get",
            {
                "list": [
                    {"id": "m1", "role": "inbox", "name": "Inbox"},
                    {"id": "m2", "role": None, "name": "Facturation"},
                ]
            },
        )
    )
    boxes = await _make(t).query_mailboxes()
    assert [b["id"] for b in boxes] == ["m1", "m2"]


@pytest.mark.asyncio
async def test_query_mailboxes_empty_response() -> None:
    t = _MockTransport(_jmap_resp("Mailbox/get", {"list": []}))
    boxes = await _make(t).query_mailboxes()
    assert boxes == []


@pytest.mark.asyncio
async def test_query_mailboxes_sends_bearer_token() -> None:
    t = _MockTransport(_jmap_resp("Mailbox/get", {"list": []}))
    await _make(t).query_mailboxes()
    assert t.last_request is not None
    assert t.last_request.headers["Authorization"] == "Bearer tok"


# ---------------------------------------------------------------------------
# get_mailbox_state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_mailbox_state_returns_state_string() -> None:
    t = _MockTransport(_jmap_resp("Mailbox/get", {"state": "state-XYZ", "list": []}))
    state = await _make(t).get_mailbox_state("mbx-1")
    assert state == "state-XYZ"


@pytest.mark.asyncio
async def test_get_mailbox_state_missing_state_returns_empty() -> None:
    t = _MockTransport(_jmap_resp("Mailbox/get", {"list": []}))
    state = await _make(t).get_mailbox_state("mbx-1")
    assert state == ""


@pytest.mark.asyncio
async def test_get_mailbox_state_sends_correct_ids() -> None:
    t = _MockTransport(_jmap_resp("Mailbox/get", {"state": "s1", "list": []}))
    await _make(t).get_mailbox_state("mbx-99")
    body = json.loads(t.last_request.content)  # type: ignore[union-attr]
    args = body["methodCalls"][0][1]
    assert args["ids"] == ["mbx-99"]


# ---------------------------------------------------------------------------
# changes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_changes_returns_created_updated() -> None:
    t = _MockTransport(
        _jmap_resp(
            "Email/changes",
            {
                "newState": "state-Z",
                "created": ["e1", "e2"],
                "updated": [],
                "destroyed": [],
            },
        )
    )
    out = await _make(t).changes("state-Y")
    assert out["newState"] == "state-Z"
    assert out["created"] == ["e1", "e2"]
    assert out["updated"] == []
    assert out["destroyed"] == []


@pytest.mark.asyncio
async def test_changes_no_methodresponse_returns_defaults() -> None:
    body = json.dumps({"methodResponses": []})
    t = _MockTransport(
        httpx.Response(
            200,
            content=body.encode(),
            headers={"content-type": "application/json"},
        )
    )
    out = await _make(t).changes("state-Y")
    assert out["newState"] == "state-Y"
    assert out["created"] == []


@pytest.mark.asyncio
async def test_changes_sends_since_state() -> None:
    t = _MockTransport(
        _jmap_resp(
            "Email/changes",
            {"newState": "s2", "created": [], "updated": [], "destroyed": []},
        )
    )
    await _make(t).changes("state-ANCHOR")
    body = json.loads(t.last_request.content)  # type: ignore[union-attr]
    args = body["methodCalls"][0][1]
    assert args["sinceState"] == "state-ANCHOR"


# ---------------------------------------------------------------------------
# get_email
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_email_returns_email_dict() -> None:
    email = {"id": "e1", "subject": "Hello", "from": [{"email": "a@b.com"}]}
    t = _MockTransport(_jmap_resp("Email/get", {"list": [email]}))
    result = await _make(t).get_email("e1")
    assert result is not None
    assert result["id"] == "e1"
    assert result["subject"] == "Hello"


@pytest.mark.asyncio
async def test_get_email_not_found_returns_none() -> None:
    t = _MockTransport(
        _jmap_resp("Email/get", {"list": [], "notFound": ["e999"]})
    )
    result = await _make(t).get_email("e999")
    assert result is None


@pytest.mark.asyncio
async def test_get_email_sends_correct_id() -> None:
    email = {"id": "e42", "subject": "Test"}
    t = _MockTransport(_jmap_resp("Email/get", {"list": [email]}))
    await _make(t).get_email("e42")
    body = json.loads(t.last_request.content)  # type: ignore[union-attr]
    args = body["methodCalls"][0][1]
    assert args["ids"] == ["e42"]
