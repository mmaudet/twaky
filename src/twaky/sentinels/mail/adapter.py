"""Mail adapter: protocol, in-memory fixture implementation, and JMAP implementation.

Three classes:
- ``MailAdapter`` — ``typing.Protocol`` defining the six mail operations.
- ``InMemoryMailAdapter`` — deterministic backing store for tests and eval fixtures.
- ``JmapMailAdapter`` — synchronous ``httpx.Client`` targeting a JMAP server.

The caller (T24 ``MailSentinel``) is responsible for session discovery and
passes ``account_id`` + ``api_url`` directly to ``JmapMailAdapter.__init__``;
no lazy discovery happens here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

import httpx

_JMAP_USING = ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail"]

_EMAIL_PROPERTIES = [
    "id",
    "threadId",
    "mailboxIds",
    "keywords",
    "from",
    "to",
    "cc",
    "subject",
    "receivedAt",
    "preview",
    "textBody",
    "bodyValues",
    "headers",
]


@runtime_checkable
class MailAdapter(Protocol):
    """Protocol for mail adapters used by the mail sentinel."""

    def get_email(self, email_id: str) -> dict[str, Any]:
        """Fetch a single email by id.

        Raises ``KeyError`` if the email is not found.
        """
        ...

    def get_thread(self, thread_id: str) -> list[dict[str, Any]]:
        """Return all emails in a thread sorted by ``receivedAt`` ascending."""
        ...

    def label(self, email_id: str, label: str) -> None:
        """Apply a named label to an email."""
        ...

    def archive(self, email_id: str) -> None:
        """Move an email out of the inbox (archive it)."""
        ...

    def mark_read(self, email_id: str) -> None:
        """Mark an email as read."""
        ...

    def save_draft(self, *, in_reply_to: str, body: str, language: str) -> str:
        """Save a draft reply and return the assigned draft id."""
        ...


class InMemoryMailAdapter:
    """In-memory mail adapter for tests and eval fixtures.

    Parameters
    ----------
    seed:
        Optional mapping of ``email_id → email dict`` to pre-populate the
        store.  Each email dict must contain at least ``"id"`` and
        ``"threadId"``.  ``"receivedAt"`` is used for thread ordering.
    """

    def __init__(self, seed: dict[str, dict[str, Any]] | None = None) -> None:
        self._emails: dict[str, dict[str, Any]] = dict(seed) if seed else {}
        self._labels: dict[str, list[str]] = {}
        self._archived: set[str] = set()
        self._read: set[str] = set()
        self._drafts: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def add(self, email: dict[str, Any]) -> None:
        """Add an email to the store keyed by ``email["id"]``."""
        self._emails[email["id"]] = email

    # ------------------------------------------------------------------
    # MailAdapter implementation
    # ------------------------------------------------------------------

    def get_email(self, email_id: str) -> dict[str, Any]:
        """Return email dict or raise ``KeyError`` if missing."""
        if email_id not in self._emails:
            raise KeyError(email_id)
        return self._emails[email_id]

    def get_thread(self, thread_id: str) -> list[dict[str, Any]]:
        """Return emails matching *thread_id*, sorted by ``receivedAt`` ascending."""
        matches = [e for e in self._emails.values() if e.get("threadId") == thread_id]
        return sorted(matches, key=lambda e: e.get("receivedAt", ""))

    def label(self, email_id: str, label: str) -> None:
        """Append *label* to the label list for *email_id*."""
        self._labels.setdefault(email_id, []).append(label)

    def archive(self, email_id: str) -> None:
        """Add *email_id* to the archived set."""
        self._archived.add(email_id)

    def mark_read(self, email_id: str) -> None:
        """Add *email_id* to the read set."""
        self._read.add(email_id)

    def save_draft(self, *, in_reply_to: str, body: str, language: str) -> str:
        """Store a draft and return its assigned id (``draft-N``)."""
        draft_id = f"draft-{len(self._drafts) + 1}"
        self._drafts.append(
            {
                "id": draft_id,
                "in_reply_to": in_reply_to,
                "body": body,
                "language": language,
            }
        )
        return draft_id


class JmapMailAdapter:
    """Synchronous JMAP mail adapter.

    Caller (T24) resolves session and passes ``account_id`` + ``api_url``
    directly — no lazy discovery here.

    Parameters
    ----------
    session_url:
        Stored for reference / logging; not used for requests.
    token_provider:
        Sync callable returning a currently-valid OIDC access token.
        Called before every request so tokens are always fresh.
    refresh_now:
        Optional sync callable that forces a token refresh (e.g.
        ``manager.sync_force_refresh``).  Return value is ignored.
        If provided, a 401 response triggers one call to ``refresh_now``
        followed by a single retry.
    account_id:
        JMAP account id (pre-resolved by caller).
    api_url:
        JMAP API endpoint URL (pre-resolved by caller).
    """

    def __init__(
        self,
        *,
        session_url: str,
        token_provider: Callable[[], str],
        refresh_now: Callable[[], object] | None = None,
        account_id: str,
        api_url: str,
    ) -> None:
        self.session_url = session_url
        self.account_id = account_id
        self.api_url = api_url
        self._token_provider = token_provider
        self._refresh_now = refresh_now
        self._client = httpx.Client(timeout=30.0)

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    def _call(self, method: str, args: dict[str, Any]) -> dict[str, Any]:
        """Post a single JMAP method call and return the response args dict.

        Builds the ``Authorization`` header fresh on every call so that
        ``token_provider`` can return a rotated token without reinitialising
        the adapter.  On a 401 response, ``refresh_now`` is called once (if
        provided) and the request is retried a single time.
        """
        payload = {
            "using": _JMAP_USING,
            "methodCalls": [
                [
                    method,
                    {"accountId": self.account_id, **args},
                    "0",
                ]
            ],
        }

        def _do_request() -> httpx.Response:
            headers = {
                "Authorization": f"Bearer {self._token_provider()}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
            return self._client.post(self.api_url, json=payload, headers=headers)

        resp = _do_request()
        if resp.status_code == 401 and self._refresh_now is not None:
            self._refresh_now()
            resp = _do_request()

        resp.raise_for_status()
        data = resp.json()
        return data["methodResponses"][0][1]  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # MailAdapter implementation
    # ------------------------------------------------------------------

    def get_email(self, email_id: str) -> dict[str, Any]:
        """Fetch a single email via ``Email/get``.

        Raises ``KeyError(email_id)`` if the server returns an empty list.
        """
        result = self._call(
            "Email/get",
            {
                "ids": [email_id],
                "properties": _EMAIL_PROPERTIES,
                "fetchTextBodyValues": True,
                "maxBodyValueBytes": 32768,
            },
        )
        emails: list[dict[str, Any]] = result.get("list", [])
        if not emails:
            raise KeyError(email_id)
        return emails[0]

    def get_thread(self, thread_id: str) -> list[dict[str, Any]]:
        """Return all emails in a thread, sorted by ``receivedAt`` ascending."""
        result = self._call(
            "Email/query",
            {
                "filter": {"inThread": thread_id},
                "sort": [{"property": "receivedAt"}],
            },
        )
        ids: list[str] = result.get("ids", [])
        return [self.get_email(eid) for eid in ids]

    def label(self, email_id: str, label: str) -> None:
        """Apply a label using the Linagora ``$label-<name>`` keyword extension."""
        self._call(
            "Email/set",
            {
                "update": {
                    email_id: {f"keywords/$label-{label}": True},
                },
            },
        )

    def archive(self, email_id: str) -> None:
        """Remove email from all current mailboxes (archive).

        Fetches current ``mailboxIds`` then unsets each flag.
        Full archive-folder mapping is deferred to SP6b.
        """
        email = self.get_email(email_id)
        current_mailboxes: dict[str, Any] = email.get("mailboxIds", {})
        self._call(
            "Email/set",
            {
                "update": {
                    email_id: {f"mailboxIds/{k}": False for k in current_mailboxes},
                },
            },
        )

    def mark_read(self, email_id: str) -> None:
        """Mark email as read via ``keywords/$seen``."""
        self._call(
            "Email/set",
            {
                "update": {
                    email_id: {"keywords/$seen": True},
                },
            },
        )

    def save_draft(self, *, in_reply_to: str, body: str, language: str) -> str:
        """Create a draft reply via ``Email/set create``.

        Returns the created key (``"draft1"``) or the server-assigned id if
        the response nests it.
        """
        result = self._call(
            "Email/set",
            {
                "create": {
                    "draft1": {
                        "mailboxIds": {"$drafts": True},
                        "keywords": {"$draft": True},
                        "from": [],
                        "to": [],
                        "subject": f"Re: (draft in {language})",
                        "textBody": [{"partId": "1", "type": "text/plain"}],
                        "bodyValues": {"1": {"value": body}},
                        "headers": [{"name": "In-Reply-To", "value": in_reply_to}],
                    }
                },
            },
        )
        created: dict[str, Any] = result.get("created", {})
        return next(iter(created.keys()))


__all__ = ["InMemoryMailAdapter", "JmapMailAdapter", "MailAdapter"]
