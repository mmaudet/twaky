"""Async JMAP client exposing exactly the four methods the MailObserver needs.

Designed for use from the async poll loop (jmap_poll.py).  Unlike
JmapMailAdapter (which is synchronous and uses a token_provider callable),
this client takes a pre-resolved access_token, api_url, and account_id —
the same values already available inside _poll_once.

The four methods mirror the MailObserver's adapter protocol:
  - query_mailboxes() -> list[dict]
  - get_mailbox_state(mailbox_id) -> str
  - changes(mailbox_id, since_state) -> dict
  - get_email(email_id) -> dict | None
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

# Type alias accepted for the optional transport parameter used in tests.
_Transport = httpx.AsyncBaseTransport | httpx.BaseTransport | None

_JMAP_USING = [
    "urn:ietf:params:jmap:core",
    "urn:ietf:params:jmap:mail",
]

_EMAIL_PROPERTIES = [
    "id",
    "threadId",
    "mailboxIds",
    "keywords",
    "from",
    "to",
    "cc",
    "replyTo",
    "subject",
    "messageId",
    "inReplyTo",
    "references",
    "receivedAt",
    "preview",
    "textBody",
    "bodyValues",
    "headers",
]


class JmapObserverClient:
    """Async JMAP client for the four observer methods.

    Parameters
    ----------
    api_url:
        JMAP API endpoint URL (pre-resolved by the poll loop session discovery).
    access_token:
        Currently-valid Bearer token (freshly fetched by the poll loop).
    account_id:
        JMAP account id (pre-resolved by the poll loop).
    """

    def __init__(
        self,
        *,
        api_url: str,
        access_token: str,
        account_id: str,
        _transport: _Transport = None,
    ) -> None:
        self.api_url = api_url
        self.account_id = account_id
        self._access_token = access_token
        self._transport = _transport  # injected only in tests

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    def _make_client(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {"timeout": 30.0}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def query_mailboxes(self) -> list[dict[str, Any]]:
        """Return all mailboxes as a list of dicts (id, role, name, ...)."""
        payload = {
            "using": _JMAP_USING,
            "methodCalls": [
                ["Mailbox/get", {"accountId": self.account_id}, "0"]
            ],
        }
        async with self._make_client() as client:
            resp = await client.post(
                self.api_url, json=payload, headers=self._headers()
            )
            resp.raise_for_status()
        data = resp.json()
        for method, response, _ in data.get("methodResponses", []):
            if method == "Mailbox/get":
                return list(response.get("list", []))
        return []

    async def get_mailbox_state(self, mailbox_id: str) -> str:
        """Return the JMAP state string for the given mailbox.

        Uses Mailbox/get with a single-id filter; the ``state`` field in the
        response is the value suitable for passing to Email/changes as
        ``sinceState``.
        """
        payload = {
            "using": _JMAP_USING,
            "methodCalls": [
                [
                    "Mailbox/get",
                    {"accountId": self.account_id, "ids": [mailbox_id]},
                    "0",
                ]
            ],
        }
        async with self._make_client() as client:
            resp = await client.post(
                self.api_url, json=payload, headers=self._headers()
            )
            resp.raise_for_status()
        data = resp.json()
        for method, response, _ in data.get("methodResponses", []):
            if method == "Mailbox/get":
                return str(response.get("state", ""))
        return ""

    async def get_global_state(self) -> str:
        """Return the current global Email/state (used for bootstrap).

        SP5c: replaces the per-mailbox ``get_mailbox_state`` for the
        observer's tick logic. Uses ``Email/get`` with an empty ``ids``
        list — the response's ``state`` field is the collection state
        that ``Email/changes`` accepts as ``sinceState``.

        (Email/query's ``queryState`` is a different state and Email/
        changes rejects it — verified on James JMAP 2026-08.)

        Falls back to an empty string on unexpected response shape.
        """
        payload = {
            "using": _JMAP_USING,
            "methodCalls": [
                [
                    "Email/get",
                    {"accountId": self.account_id, "ids": []},
                    "0",
                ]
            ],
        }
        async with self._make_client() as client:
            resp = await client.post(
                self.api_url, json=payload, headers=self._headers()
            )
            resp.raise_for_status()
        data = resp.json()
        for method, response, _ in data.get("methodResponses", []):
            if method == "Email/get":
                return str(response.get("state") or "")
        return ""

    async def changes(
        self, since_state: str, mailbox_id: str | None = None
    ) -> dict[str, Any]:
        """Run Email/changes since *since_state*.

        SP5c: ``mailbox_id`` is optional and IGNORED for the global path.
        Kept as a keyword parameter for backward compatibility with the
        SP5b signature ``changes(mailbox_id, since_state)`` — old callers
        that pass positionally must be updated (see observer.py).

        Returns a dict with keys: newState, created, updated, destroyed.
        The response is a global mail-collection delta; the observer
        filters/dispatches based on each returned email's ``mailboxIds``.
        """
        _ = mailbox_id  # explicitly unused
        payload = {
            "using": _JMAP_USING,
            "methodCalls": [
                [
                    "Email/changes",
                    {
                        "accountId": self.account_id,
                        "sinceState": since_state,
                        "maxChanges": 100,
                    },
                    "0",
                ]
            ],
        }
        async with self._make_client() as client:
            resp = await client.post(
                self.api_url, json=payload, headers=self._headers()
            )
            resp.raise_for_status()
        data = resp.json()
        for method, response, _ in data.get("methodResponses", []):
            if method == "Email/changes":
                return {
                    "newState": response.get("newState", since_state),
                    "created": response.get("created", []),
                    "updated": response.get("updated", []),
                    "destroyed": response.get("destroyed", []),
                }
        return {
            "newState": since_state,
            "created": [],
            "updated": [],
            "destroyed": [],
        }

    async def get_email(self, email_id: str) -> dict[str, Any] | None:
        """Fetch a single email by id.  Returns None if not found."""
        payload = {
            "using": _JMAP_USING,
            "methodCalls": [
                [
                    "Email/get",
                    {
                        "accountId": self.account_id,
                        "ids": [email_id],
                        "properties": _EMAIL_PROPERTIES,
                        "fetchTextBodyValues": True,
                        "maxBodyValueBytes": 32768,
                    },
                    "0",
                ]
            ],
        }
        async with self._make_client() as client:
            resp = await client.post(
                self.api_url, json=payload, headers=self._headers()
            )
            resp.raise_for_status()
        data = resp.json()
        for method, response, _ in data.get("methodResponses", []):
            if method == "Email/get":
                emails: list[dict[str, Any]] = response.get("list", [])
                return emails[0] if emails else None
        return None

    async def get_mailbox_total(self, mailbox_id: str) -> int:
        """Return the ``totalEmails`` count for the given mailbox.

        Used by SP7 style-analysis to decide when to trigger a fresh
        analysis (delta vs stored ``sent_count_at_compute``).
        """
        payload = {
            "using": _JMAP_USING,
            "methodCalls": [
                [
                    "Mailbox/get",
                    {
                        "accountId": self.account_id,
                        "ids": [mailbox_id],
                        "properties": ["totalEmails"],
                    },
                    "0",
                ]
            ],
        }
        async with self._make_client() as client:
            resp = await client.post(
                self.api_url, json=payload, headers=self._headers()
            )
            resp.raise_for_status()
        data = resp.json()
        for method, response, _ in data.get("methodResponses", []):
            if method == "Mailbox/get":
                lst = response.get("list", [])
                if lst:
                    return int(lst[0].get("totalEmails", 0))
        return 0

    async def list_recent_emails(
        self, mailbox_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """List the most recent emails in a mailbox with body text.

        Uses Email/query (ordered by receivedAt DESC) then Email/get in
        a single JMAP round-trip via result-references. Returns dicts
        with subject + body (plain text assembled from textBody/bodyValues).
        """
        payload = {
            "using": _JMAP_USING,
            "methodCalls": [
                [
                    "Email/query",
                    {
                        "accountId": self.account_id,
                        "filter": {"inMailbox": mailbox_id},
                        "sort": [{"property": "receivedAt", "isAscending": False}],
                        "limit": limit,
                    },
                    "q",
                ],
                [
                    "Email/get",
                    {
                        "accountId": self.account_id,
                        "#ids": {
                            "resultOf": "q",
                            "name": "Email/query",
                            "path": "/ids",
                        },
                        "properties": ["id", "subject", "textBody", "bodyValues"],
                        "fetchTextBodyValues": True,
                        "maxBodyValueBytes": 8192,
                    },
                    "g",
                ],
            ],
        }
        async with self._make_client() as client:
            resp = await client.post(
                self.api_url, json=payload, headers=self._headers()
            )
            resp.raise_for_status()
        data = resp.json()
        for method, response, _ in data.get("methodResponses", []):
            if method == "Email/get":
                return list(response.get("list", []))
        return []


__all__ = ["JmapObserverClient"]
