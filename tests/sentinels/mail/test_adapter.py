"""Unit tests for MailAdapter protocol, InMemoryMailAdapter, and JmapMailAdapter."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from twaky.sentinels.mail.adapter import InMemoryMailAdapter, JmapMailAdapter

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_email(
    email_id: str,
    thread_id: str,
    received_at: str,
    mailbox_ids: dict[str, bool] | None = None,
) -> dict[str, Any]:
    return {
        "id": email_id,
        "threadId": thread_id,
        "receivedAt": received_at,
        "mailboxIds": mailbox_ids or {"inbox-1": True},
        "subject": f"Subject for {email_id}",
    }


def _jmap_response(method: str, result: dict[str, Any]) -> httpx.Response:
    """Build a fake JMAP httpx.Response."""
    body = json.dumps({"methodResponses": [[method, result, "0"]]})
    return httpx.Response(
        200, content=body.encode(), headers={"content-type": "application/json"}
    )


class _SingleResponseTransport(httpx.MockTransport):
    """MockTransport that returns a single pre-built response for every request."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.last_request: httpx.Request | None = None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.last_request = request
        return self._response


def _adapter(transport: _SingleResponseTransport) -> JmapMailAdapter:
    """Construct a JmapMailAdapter and swap in the mock transport."""
    adapter = JmapMailAdapter(
        session_url="https://jmap.example.com/session",
        bearer_token="tok",
        account_id="acct-abc",
        api_url="https://jmap.example.com/jmap",
    )
    adapter._client = httpx.Client(transport=transport)
    return adapter


# ---------------------------------------------------------------------------
# InMemoryMailAdapter tests
# ---------------------------------------------------------------------------


class TestInMemoryMailAdapter:
    def test_get_email(self) -> None:
        """Seeded adapter returns the email by id."""
        email = _make_email("e1", "t1", "2024-01-01T10:00:00Z")
        adapter = InMemoryMailAdapter(seed={"e1": email})
        result = adapter.get_email("e1")
        assert result["id"] == "e1"
        assert result["threadId"] == "t1"

    def test_get_email_missing_raises_key_error(self) -> None:
        """KeyError is raised when the email id is not in the store."""
        adapter = InMemoryMailAdapter()
        with pytest.raises(KeyError):
            adapter.get_email("does-not-exist")

    def test_get_thread_ordered_by_received(self) -> None:
        """Thread emails are returned sorted by receivedAt ascending."""
        e1 = _make_email("e1", "t1", "2024-01-01T10:00:00Z")
        e2 = _make_email("e2", "t1", "2024-01-01T08:00:00Z")  # earlier
        e3 = _make_email("e3", "t1", "2024-01-01T12:00:00Z")  # latest
        e4 = _make_email("e4", "t2", "2024-01-01T09:00:00Z")  # different thread

        adapter = InMemoryMailAdapter(seed={"e1": e1, "e2": e2, "e3": e3, "e4": e4})
        thread = adapter.get_thread("t1")

        assert [m["id"] for m in thread] == ["e2", "e1", "e3"]

    def test_label_archive_mark(self) -> None:
        """Internal state reflects label, archive, and mark_read operations."""
        e1 = _make_email("e1", "t1", "2024-01-01T10:00:00Z")
        e2 = _make_email("e2", "t1", "2024-01-01T11:00:00Z")
        e3 = _make_email("e3", "t2", "2024-01-01T12:00:00Z")
        adapter = InMemoryMailAdapter(seed={"e1": e1, "e2": e2, "e3": e3})

        adapter.label("e1", "invoice")
        adapter.archive("e2")
        adapter.mark_read("e3")

        assert "invoice" in adapter._labels["e1"]
        assert "e2" in adapter._archived
        assert "e3" in adapter._read

    def test_save_draft_returns_id_and_stores(self) -> None:
        """First draft returns draft-1, second returns draft-2; _drafts tracks them."""
        adapter = InMemoryMailAdapter()

        id1 = adapter.save_draft(
            in_reply_to="msg-001", body="Hello there", language="en"
        )
        id2 = adapter.save_draft(in_reply_to="msg-002", body="Bonjour", language="fr")

        assert id1 == "draft-1"
        assert id2 == "draft-2"
        assert len(adapter._drafts) == 2
        assert adapter._drafts[0]["body"] == "Hello there"
        assert adapter._drafts[0]["language"] == "en"
        assert adapter._drafts[1]["body"] == "Bonjour"
        assert adapter._drafts[1]["language"] == "fr"


# ---------------------------------------------------------------------------
# JmapMailAdapter tests (via httpx.MockTransport)
# ---------------------------------------------------------------------------


class TestJmapMailAdapter:
    def test_mark_read_calls_email_set_with_seen_keyword(self) -> None:
        """mark_read posts Email/set with keywords/$seen: True."""
        transport = _SingleResponseTransport(
            _jmap_response("Email/set", {"updated": {"eml-1": None}})
        )
        adapter = _adapter(transport)
        adapter.mark_read("eml-1")

        assert transport.last_request is not None
        body = json.loads(transport.last_request.content)
        method_calls = body["methodCalls"]
        assert method_calls[0][0] == "Email/set"
        args = method_calls[0][1]
        assert args["update"] == {"eml-1": {"keywords/$seen": True}}

    def test_label_uses_keyword_prefix(self) -> None:
        """label posts Email/set with keywords/$label-<name>: True."""
        transport = _SingleResponseTransport(
            _jmap_response("Email/set", {"updated": {"eml-2": None}})
        )
        adapter = _adapter(transport)
        adapter.label("eml-2", "invoice")

        assert transport.last_request is not None
        body = json.loads(transport.last_request.content)
        args = body["methodCalls"][0][1]
        assert args["update"] == {"eml-2": {"keywords/$label-invoice": True}}

    def test_save_draft_returns_created_id(self) -> None:
        """save_draft returns the key from the created dict."""
        transport = _SingleResponseTransport(
            _jmap_response(
                "Email/set",
                {"created": {"draft1": {"id": "srv-abc", "blobId": "b"}}},
            )
        )
        adapter = _adapter(transport)
        draft_id = adapter.save_draft(
            in_reply_to="<msg-001@example.com>",
            body="Thank you",
            language="en",
        )

        # The implementation returns the key (client-side name "draft1")
        assert draft_id == "draft1"
