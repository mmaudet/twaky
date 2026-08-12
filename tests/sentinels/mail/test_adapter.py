"""Unit tests for MailAdapter protocol, InMemoryMailAdapter, and JmapMailAdapter."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

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


class _MultiResponseTransport(httpx.MockTransport):
    """MockTransport that returns responses from a queue, one per call."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responses.pop(0)


def _adapter(transport: _SingleResponseTransport) -> JmapMailAdapter:
    """Construct a JmapMailAdapter and swap in the mock transport."""
    adapter = JmapMailAdapter(
        session_url="https://jmap.example.com/session",
        token_provider=lambda: "test-token",
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

    def test_in_memory_set_keyword_stores(self) -> None:
        """set_keyword stores a keyword value in the _keywords dict."""
        adapter = InMemoryMailAdapter()
        adapter.set_keyword("e1", "$junk", True)
        assert adapter._keywords["e1"]["$junk"] is True

    def test_in_memory_set_keyword_can_clear(self) -> None:
        """set_keyword can set a keyword to False (clearing it)."""
        adapter = InMemoryMailAdapter()
        adapter.set_keyword("e1", "$junk", True)
        adapter.set_keyword("e1", "$junk", False)
        assert adapter._keywords["e1"]["$junk"] is False

    def test_in_memory_set_keywords_bulk_all_at_once(self) -> None:
        """set_keywords_bulk stores multiple keywords for an email."""
        adapter = InMemoryMailAdapter()
        adapter.set_keywords_bulk("e1", {"$junk": True, "nonjunk": False})
        assert adapter._keywords["e1"]["$junk"] is True
        assert adapter._keywords["e1"]["nonjunk"] is False


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
        """save_draft returns the server-assigned id from the created entry.

        Per RFC 8621 §5.3, the ``create`` key ("draft1") is a client-side
        placeholder and the persisted id is the ``id`` property inside the
        created response object. Callers need the server id to re-open,
        update, or send the draft later.
        """
        transport = _SingleResponseTransport(
            _jmap_response(
                "Email/set",
                {"created": {"draft1": {"id": "srv-abc", "blobId": "b"}}},
            )
        )
        adapter = _adapter(transport)
        # Pre-seed the Drafts mailbox id to bypass the Mailbox/get resolution
        # call — the transport mock only responds to the Email/set request.
        adapter._drafts_mailbox_id = "drafts-mbox-uuid"
        draft_id = adapter.save_draft(
            in_reply_to="<msg-001@example.com>",
            body="Thank you",
            language="en",
        )

        assert draft_id == "srv-abc"

    def test_save_draft_raises_when_not_created(self) -> None:
        """save_draft raises RuntimeError with the notCreated reason (not StopIteration).

        The previous implementation did ``next(iter(created.keys()))`` blindly
        which raised ``StopIteration`` when the server refused the create —
        LangGraph then converted that to a confusing ``RuntimeError: generator
        raised StopIteration``. Now we surface the JMAP-level reason.
        """
        transport = _SingleResponseTransport(
            _jmap_response(
                "Email/set",
                {
                    "created": None,
                    "notCreated": {
                        "draft1": {
                            "type": "invalidArguments",
                            "description": "some field is not allowed",
                        }
                    },
                },
            )
        )
        adapter = _adapter(transport)
        adapter._drafts_mailbox_id = "drafts-mbox-uuid"
        with pytest.raises(RuntimeError, match="notCreated"):
            adapter.save_draft(
                in_reply_to="<msg-001@example.com>",
                body="Thank you",
                language="en",
            )

    def test_save_draft_resolves_drafts_mailbox_and_uses_uuid(self) -> None:
        """save_draft calls Mailbox/get on first invocation to resolve drafts UUID.

        James JMAP rejects ``mailboxIds: {"$drafts": True}`` with ``Invalid
        UUID string: $drafts``. The adapter must resolve the actual UUID of
        the mailbox with ``role='drafts'`` and reference it by UUID in the
        create body. This id is cached on the adapter so subsequent calls
        skip the Mailbox/get round-trip.
        """
        mailbox_response = _jmap_response(
            "Mailbox/get",
            {
                "list": [
                    {"id": "inbox-uuid", "role": "inbox"},
                    {"id": "drafts-uuid-123", "role": "drafts"},
                    {"id": "sent-uuid", "role": "sent"},
                ]
            },
        )
        create_response = _jmap_response(
            "Email/set",
            {"created": {"draft1": {"id": "new-draft-srv-id"}}},
        )
        transport = _MultiResponseTransport([mailbox_response, create_response])
        adapter = _adapter(transport)

        draft_id = adapter.save_draft(in_reply_to="<msg@x>", body="body", language="en")

        assert draft_id == "new-draft-srv-id"
        assert adapter._drafts_mailbox_id == "drafts-uuid-123"
        # The Email/set create body must reference the drafts UUID, not $drafts
        create_body = json.loads(transport.requests[1].content)
        create_args = create_body["methodCalls"][0][1]
        assert create_args["create"]["draft1"]["mailboxIds"] == {
            "drafts-uuid-123": True
        }

    def test_adapter_401_calls_refresh_now_and_retries(self) -> None:
        """On 401, refresh_now is called and request is retried with fresh token."""
        counter: list[int] = [0]

        def token_provider() -> str:
            counter[0] += 1
            return f"token-{counter[0]}"

        refresh_now = MagicMock()

        email_body = _jmap_response(
            "Email/get",
            {"list": [{"id": "eml-1", "threadId": "t-1", "subject": "Hello"}]},
        )
        # raw 401 response (no JSON body required)
        resp_401 = httpx.Response(401, content=b"Unauthorized")

        transport = _MultiResponseTransport([resp_401, email_body])

        adapter = JmapMailAdapter(
            session_url="https://jmap.example.com/session",
            token_provider=token_provider,
            refresh_now=refresh_now,
            account_id="acct-abc",
            api_url="https://jmap.example.com/jmap",
        )
        adapter._client = httpx.Client(transport=transport)

        result = adapter.get_email("eml-1")

        refresh_now.assert_called_once()
        assert result["id"] == "eml-1"
        # Second request must carry token-2 (the token returned after refresh call)
        second_auth = transport.requests[1].headers["authorization"]
        assert second_auth == "Bearer token-2"

    def test_adapter_401_without_refresh_now_reraises(self) -> None:
        """Without refresh_now, a 401 response raises HTTPStatusError immediately."""
        transport = _MultiResponseTransport(
            [httpx.Response(401, content=b"Unauthorized")]
        )

        adapter = JmapMailAdapter(
            session_url="https://jmap.example.com/session",
            token_provider=lambda: "tok",
            refresh_now=None,
            account_id="acct-abc",
            api_url="https://jmap.example.com/jmap",
        )
        adapter._client = httpx.Client(transport=transport)

        with pytest.raises(httpx.HTTPStatusError):
            adapter.get_email("eml-1")

    def test_adapter_double_401_reraises(self) -> None:
        """When both the initial request and retry return 401, HTTPStatusError is raised."""
        refresh_now = MagicMock()

        transport = _MultiResponseTransport(
            [
                httpx.Response(401, content=b"Unauthorized"),
                httpx.Response(401, content=b"Unauthorized"),
            ]
        )

        adapter = JmapMailAdapter(
            session_url="https://jmap.example.com/session",
            token_provider=lambda: "tok",
            refresh_now=refresh_now,
            account_id="acct-abc",
            api_url="https://jmap.example.com/jmap",
        )
        adapter._client = httpx.Client(transport=transport)

        with pytest.raises(httpx.HTTPStatusError):
            adapter.get_email("eml-1")

        refresh_now.assert_called_once()

    def test_jmap_set_keyword_calls_email_set(self) -> None:
        """set_keyword posts Email/set with keywords/<name>: value."""
        transport = _SingleResponseTransport(
            _jmap_response("Email/set", {"updated": {"e1": None}})
        )
        adapter = _adapter(transport)
        adapter.set_keyword("e1", "$junk", True)

        assert transport.last_request is not None
        body = json.loads(transport.last_request.content)
        method_calls = body["methodCalls"]
        assert method_calls[0][0] == "Email/set"
        args = method_calls[0][1]
        assert args["update"] == {"e1": {"keywords/$junk": True}}

    def test_jmap_set_keywords_bulk_single_call(self) -> None:
        """set_keywords_bulk posts Email/set with all keywords in a single update patch."""
        transport = _SingleResponseTransport(
            _jmap_response("Email/set", {"updated": {"e1": None}})
        )
        adapter = _adapter(transport)
        adapter.set_keywords_bulk("e1", {"$junk": True, "nonjunk": False})

        assert transport.last_request is not None
        body = json.loads(transport.last_request.content)
        method_calls = body["methodCalls"]
        assert method_calls[0][0] == "Email/set"
        args = method_calls[0][1]
        # Both keywords should be in a single update patch
        assert args["update"] == {
            "e1": {"keywords/$junk": True, "keywords/nonjunk": False}
        }

    def test_jmap_set_keywords_bulk_with_mailbox_patches(self) -> None:
        """set_keywords_bulk supports optional mailbox_patches bundled in the same update.

        Restore semantics require atomicity: clearing spam labels + re-adding
        to INBOX must happen in ONE Email/set so a partial success cannot
        leave the mail in an inconsistent state.
        """
        transport = _SingleResponseTransport(
            _jmap_response("Email/set", {"updated": {"e1": None}})
        )
        adapter = _adapter(transport)
        adapter.set_keywords_bulk(
            "e1",
            {"$junk": False, "nonjunk": True, "$label-newsletter": False},
            mailbox_patches={"inbox-uuid": True, "junk-uuid": False},
        )

        assert transport.last_request is not None
        body = json.loads(transport.last_request.content)
        args = body["methodCalls"][0][1]
        assert args["update"] == {
            "e1": {
                "keywords/$junk": False,
                "keywords/nonjunk": True,
                "keywords/$label-newsletter": False,
                "mailboxIds/inbox-uuid": True,
                "mailboxIds/junk-uuid": False,
            }
        }

    def test_get_thread_via_thread_get_not_email_query(self) -> None:
        """get_thread uses Thread/get (RFC 8621 §5) — Email/query filter:inThread
        is rejected by James JMAP with 'unsupported filter options'.

        Sequence: Thread/get {ids: [t1]} → emailIds list → Email/get each id.
        Result is sorted by receivedAt ascending.
        """
        thread_resp = _jmap_response(
            "Thread/get",
            {"list": [{"id": "t1", "emailIds": ["e2", "e1"]}]},
        )
        email_e2 = _jmap_response(
            "Email/get",
            {
                "list": [
                    {
                        "id": "e2",
                        "threadId": "t1",
                        "subject": "second",
                        "receivedAt": "2026-01-02T00:00:00Z",
                    }
                ]
            },
        )
        email_e1 = _jmap_response(
            "Email/get",
            {
                "list": [
                    {
                        "id": "e1",
                        "threadId": "t1",
                        "subject": "first",
                        "receivedAt": "2026-01-01T00:00:00Z",
                    }
                ]
            },
        )
        transport = _MultiResponseTransport([thread_resp, email_e2, email_e1])
        adapter = _adapter(transport)

        thread = adapter.get_thread("t1")

        # Chronological (asc) order enforced by the adapter
        assert [e["id"] for e in thread] == ["e1", "e2"]
        # First request was Thread/get, NOT Email/query
        first_body = json.loads(transport.requests[0].content)
        assert first_body["methodCalls"][0][0] == "Thread/get"
        assert first_body["methodCalls"][0][1] == {
            "accountId": "acct-abc",
            "ids": ["t1"],
        }

    def test_get_thread_empty_when_thread_not_found(self) -> None:
        """Thread/get with unknown id → list empty → empty result, no Email/get calls."""
        transport = _SingleResponseTransport(
            _jmap_response("Thread/get", {"list": [], "notFound": ["t-nope"]})
        )
        adapter = _adapter(transport)
        assert adapter.get_thread("t-nope") == []

    def test_save_draft_envelope_complete(self) -> None:
        """save_draft serialises the full RFC 5322 reply envelope.

        - from/to/cc as EmailAddress dicts
        - subject verbatim (no placeholder when provided)
        - header:In-Reply-To:asMessageIds + header:References:asMessageIds
          (typed setters — the ``headers`` array form is rejected by James)
        - cc key omitted from the update body when cc_addr is falsy
          (belt-and-braces: keeps request minimal).
        """
        transport = _SingleResponseTransport(
            _jmap_response(
                "Email/set",
                {"created": {"draft1": {"id": "srv-1", "blobId": "b"}}},
            )
        )
        adapter = _adapter(transport)
        adapter._drafts_mailbox_id = "drafts-uuid"
        adapter.save_draft(
            in_reply_to="<msg-parent@example.com>",
            body="Reply text.",
            language="fr",
            from_addr=[{"name": "Alice", "email": "alice@x"}],
            to_addr=[{"name": "Bob", "email": "bob@y"}],
            cc_addr=[{"email": "carol@z"}],
            subject="Re: hello",
            references=["<msg-root@example.com>", "<msg-parent@example.com>"],
        )

        body = json.loads(transport.last_request.content)
        create = body["methodCalls"][0][1]["create"]["draft1"]
        assert create["from"] == [{"name": "Alice", "email": "alice@x"}]
        assert create["to"] == [{"name": "Bob", "email": "bob@y"}]
        assert create["cc"] == [{"email": "carol@z"}]
        assert create["subject"] == "Re: hello"
        assert create["header:In-Reply-To:asMessageIds"] == ["<msg-parent@example.com>"]
        assert create["header:References:asMessageIds"] == [
            "<msg-root@example.com>",
            "<msg-parent@example.com>",
        ]
        # ``headers`` array form (rejected by James) MUST NOT appear.
        assert "headers" not in create

    def test_save_draft_omits_cc_when_empty(self) -> None:
        """When cc_addr is None or empty, no ``cc`` key is present in the create body."""
        transport = _SingleResponseTransport(
            _jmap_response("Email/set", {"created": {"draft1": {"id": "srv-1"}}})
        )
        adapter = _adapter(transport)
        adapter._drafts_mailbox_id = "drafts-uuid"
        adapter.save_draft(
            in_reply_to="<x@y>",
            body="body",
            language="en",
            from_addr=[{"email": "a@x"}],
            to_addr=[{"email": "b@y"}],
            subject="Re: x",
        )
        body = json.loads(transport.last_request.content)
        create = body["methodCalls"][0][1]["create"]["draft1"]
        assert "cc" not in create

    def test_resolve_role_mailbox_id_caches_all_roles_in_one_call(self) -> None:
        """resolve_role_mailbox_id performs one Mailbox/get for N lookups.

        Single ``Mailbox/get`` response populates every role at once, so
        subsequent lookups (drafts, inbox, sent, junk, …) never hit the
        server again for the lifetime of the adapter. Assertion is enforced
        by ``_MultiResponseTransport`` which queues exactly one response —
        a second call would raise ``IndexError`` from ``pop(0)``.
        """
        transport = _MultiResponseTransport(
            [
                _jmap_response(
                    "Mailbox/get",
                    {
                        "list": [
                            {"id": "inbox-uuid", "role": "inbox"},
                            {"id": "drafts-uuid", "role": "drafts"},
                            {"id": "junk-uuid", "role": "junk"},
                            {"id": "sent-uuid", "role": "sent"},
                        ]
                    },
                )
            ]
        )
        adapter = _adapter(transport)

        assert adapter.resolve_role_mailbox_id("inbox") == "inbox-uuid"
        assert adapter.resolve_role_mailbox_id("drafts") == "drafts-uuid"
        assert adapter.resolve_role_mailbox_id("junk") == "junk-uuid"
        assert adapter.resolve_role_mailbox_id("sent") == "sent-uuid"
        # If a 2nd Mailbox/get had been sent, the pop(0) queue would be
        # empty and the request would have raised IndexError.
        assert len(transport.requests) == 1

    def test_resolve_role_mailbox_id_raises_for_missing_role(self) -> None:
        """RuntimeError names the role that could not be resolved."""
        transport = _SingleResponseTransport(
            _jmap_response(
                "Mailbox/get",
                {"list": [{"id": "inbox-uuid", "role": "inbox"}]},
            )
        )
        adapter = _adapter(transport)
        with pytest.raises(RuntimeError, match="role='junk'"):
            adapter.resolve_role_mailbox_id("junk")

    def test_resolve_mailbox_role_by_id_no_role_mailboxes(self) -> None:
        """When the server returns zero role-tagged mailboxes the fetch runs exactly once.

        Regression guard: the old guard ``if not self._mailbox_roles_by_id``
        would re-trigger Mailbox/get on every call because the dict stays
        empty.  With ``_mailboxes_fetched`` the second call is served from
        cache (empty) without a second round-trip.
        """
        transport = _MultiResponseTransport(
            [
                _jmap_response(
                    "Mailbox/get",
                    {
                        # Server returns mailboxes but none has a role set.
                        "list": [
                            {"id": "custom-mbox-1", "role": None},
                            {"id": "custom-mbox-2", "role": None},
                        ]
                    },
                )
            ]
        )
        adapter = _adapter(transport)

        result_first = adapter.resolve_mailbox_role_by_id("some-id")
        result_second = adapter.resolve_mailbox_role_by_id("some-id")

        # Neither call should find a role.
        assert result_first is None
        assert result_second is None
        # Mailbox/get must have been called ONCE, not twice.
        assert len(transport.requests) == 1
