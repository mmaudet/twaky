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

    def save_draft(
        self,
        *,
        in_reply_to: str,
        body: str,
        language: str,
        from_addr: list[dict[str, str]] | None = None,
        to_addr: list[dict[str, str]] | None = None,
        cc_addr: list[dict[str, str]] | None = None,
        subject: str | None = None,
        references: list[str] | None = None,
    ) -> str:
        """Save a draft reply and return the assigned draft id."""
        ...

    def set_keyword(self, email_id: str, keyword: str, value: bool) -> None:
        """Set a single keyword on an email to a boolean value."""
        ...

    def set_keywords_bulk(
        self,
        email_id: str,
        patches: dict[str, bool],
        *,
        mailbox_patches: dict[str, bool] | None = None,
    ) -> None:
        """Atomically set multiple keywords (and optionally mailbox membership).

        ``mailbox_patches`` maps mailbox-id → bool and controls whether the
        email is added to (``True``) or removed from (``False``) that
        mailbox. Bundled into the same JMAP round-trip when non-empty so
        restore semantics (unarchive + clear labels) stay atomic.
        """
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
        self._keywords: dict[str, dict[str, bool]] = {}
        self._mailboxes: dict[str, dict[str, bool]] = {}

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

    def save_draft(
        self,
        *,
        in_reply_to: str,
        body: str,
        language: str,
        from_addr: list[dict[str, str]] | None = None,
        to_addr: list[dict[str, str]] | None = None,
        cc_addr: list[dict[str, str]] | None = None,
        subject: str | None = None,
        references: list[str] | None = None,
    ) -> str:
        """Store a draft and return its assigned id (``draft-N``)."""
        draft_id = f"draft-{len(self._drafts) + 1}"
        self._drafts.append(
            {
                "id": draft_id,
                "in_reply_to": in_reply_to,
                "body": body,
                "language": language,
                "from": from_addr or [],
                "to": to_addr or [],
                "cc": cc_addr or [],
                "subject": subject,
                "references": references or [],
            }
        )
        return draft_id

    def set_keyword(self, email_id: str, keyword: str, value: bool) -> None:
        """Store a single keyword value for an email."""
        self._keywords.setdefault(email_id, {})[keyword] = value

    def set_keywords_bulk(
        self,
        email_id: str,
        patches: dict[str, bool],
        *,
        mailbox_patches: dict[str, bool] | None = None,
    ) -> None:
        """Store multiple keyword values (and optional mailbox membership)."""
        for k, v in patches.items():
            self.set_keyword(email_id, k, v)
        if mailbox_patches:
            mbox_map = self._mailboxes.setdefault(email_id, {})
            for mbox_id, add in mailbox_patches.items():
                if add:
                    mbox_map[mbox_id] = True
                else:
                    mbox_map.pop(mbox_id, None)


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
        # Lazily resolved Drafts mailbox id — James JMAP rejects the symbolic
        # ``$drafts`` name in ``mailboxIds`` and expects the actual UUID.
        self._drafts_mailbox_id: str | None = None
        # Cache of resolved mailbox ids keyed by JMAP ``role`` (RFC 8621 §2.1.4).
        # Populated on demand by ``_resolve_role_mailbox_id``.
        self._mailbox_ids_by_role: dict[str, str] = {}

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
        """Return all emails in a thread, sorted by ``receivedAt`` ascending.

        Uses ``Thread/get`` to resolve the ordered list of ``emailIds``, then
        fetches each with ``get_email``. Some JMAP implementations (notably
        James JMAP as of 2026) do not support ``Email/query filter:inThread``
        and reject the request with ``invalidArguments: '[inThread]' was
        unsupported filter options``; ``Thread/get`` is the portable primitive
        defined by RFC 8621 §5 and works across implementations.
        """
        result = self._call("Thread/get", {"ids": [thread_id]})
        threads = result.get("list") or []
        if not threads:
            return []
        email_ids: list[str] = threads[0].get("emailIds") or []
        emails = [self.get_email(eid) for eid in email_ids]
        return sorted(emails, key=lambda e: e.get("receivedAt", ""))

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

    def resolve_role_mailbox_id(self, role: str) -> str:
        """Resolve and cache the id of the mailbox with the given RFC 8621 role.

        JMAP mailbox roles are the portable way to reference standard folders
        (``"inbox"``, ``"drafts"``, ``"sent"``, ``"trash"``, ``"junk"``,
        ``"archive"``, …). James JMAP rejects symbolic names like ``$drafts``
        in ``mailboxIds``; callers must use the actual UUID resolved here.

        Results are cached per-adapter-instance under
        ``self._mailbox_ids_by_role``. On the first call for any role, a
        single ``Mailbox/get`` fetches the full mailbox list (with ``id`` +
        ``role``) and populates every role at once — subsequent lookups are
        free.

        Raises ``RuntimeError`` when no mailbox carries the requested role.
        """
        if role in self._mailbox_ids_by_role:
            return self._mailbox_ids_by_role[role]
        result = self._call(
            "Mailbox/get",
            {"ids": None, "properties": ["id", "role"]},
        )
        for mbox in result.get("list") or []:
            m_role = mbox.get("role")
            if m_role:
                self._mailbox_ids_by_role[str(m_role)] = str(mbox["id"])
        if role not in self._mailbox_ids_by_role:
            raise RuntimeError(
                f"no mailbox with role={role!r} found on this account"
            )
        return self._mailbox_ids_by_role[role]

    def _resolve_drafts_mailbox_id(self) -> str:
        """Back-compat alias — resolves the Drafts mailbox id.

        Kept for callers written before the generic
        ``resolve_role_mailbox_id`` helper existed. Delegates to it and
        keeps the legacy per-role cache in sync.
        """
        if self._drafts_mailbox_id is not None:
            return self._drafts_mailbox_id
        self._drafts_mailbox_id = self.resolve_role_mailbox_id("drafts")
        return self._drafts_mailbox_id

    def save_draft(
        self,
        *,
        in_reply_to: str,
        body: str,
        language: str,
        from_addr: list[dict[str, str]] | None = None,
        to_addr: list[dict[str, str]] | None = None,
        cc_addr: list[dict[str, str]] | None = None,
        subject: str | None = None,
        references: list[str] | None = None,
    ) -> str:
        """Create a draft reply via ``Email/set create``.

        Returns the server-assigned id of the created draft. Raises
        ``RuntimeError`` when the server refuses the create (surfacing the
        ``notCreated`` reason so failures are debuggable instead of a
        confusing ``StopIteration``).

        Uses the RFC 8621 §4.1.3 typed-header syntax
        (``header:In-Reply-To:asMessageIds`` and
        ``header:References:asMessageIds``) rather than a generic ``headers``
        array — James JMAP rejects the array form on create with
        ``JsonValidationError('headers' is not allowed)``.

        Resolves the Drafts mailbox id via ``Mailbox/get`` on first call;
        the symbolic ``$drafts`` name in ``mailboxIds`` is rejected by James
        JMAP with ``Invalid UUID string: $drafts``.

        Parameters
        ----------
        in_reply_to:
            Message-Id header value of the message being replied to. Wrapped
            with angle brackets by JMAP itself. Passed as-is; caller is
            responsible for extracting it from the ``messageId`` property or
            the ``Message-ID`` header of the original message.
        body:
            Plain-text body content.
        language:
            BCP-47 language tag, used as a fallback in the placeholder
            subject when *subject* is not provided.
        from_addr, to_addr:
            Envelope addresses as JMAP EmailAddress dicts
            (``[{"name": "...", "email": "..."}, ...]``). Both default to
            empty lists when omitted (SP6 legacy behaviour); callers
            wanting a valid draft SHOULD provide them.
        subject:
            Reply subject line (typically ``"Re: <original subject>"``).
            Falls back to ``"Re: (draft in <language>)"`` when omitted.
        references:
            Optional ``References`` header value(s): the reply chain of
            Message-Ids for RFC 5322 threading. Passed with the JMAP
            ``asMessageIds`` typed-header setter when non-empty.
        """
        drafts_id = self._resolve_drafts_mailbox_id()
        create_body: dict[str, Any] = {
            "mailboxIds": {drafts_id: True},
            "keywords": {"$draft": True},
            "from": from_addr or [],
            "to": to_addr or [],
            "subject": subject or f"Re: (draft in {language})",
            "textBody": [{"partId": "1", "type": "text/plain"}],
            "bodyValues": {"1": {"value": body}},
            "header:In-Reply-To:asMessageIds": [in_reply_to],
        }
        if cc_addr:
            create_body["cc"] = cc_addr
        if references:
            create_body["header:References:asMessageIds"] = references
        result = self._call(
            "Email/set",
            {"create": {"draft1": create_body}},
        )
        created: dict[str, Any] = result.get("created") or {}
        if not created:
            not_created = result.get("notCreated") or {}
            raise RuntimeError(f"save_draft: notCreated={not_created}")
        entry: dict[str, Any] = next(iter(created.values()))
        return str(entry.get("id") or next(iter(created.keys())))

    def set_keyword(self, email_id: str, keyword: str, value: bool) -> None:
        """Set a single keyword on an email via ``Email/set``."""
        self._call(
            "Email/set",
            {
                "update": {
                    email_id: {f"keywords/{keyword}": value},
                },
            },
        )

    def set_keywords_bulk(
        self,
        email_id: str,
        patches: dict[str, bool],
        *,
        mailbox_patches: dict[str, bool] | None = None,
    ) -> None:
        """Atomically set keywords + optionally patch mailbox membership.

        Both patches are sent in the same ``Email/set`` update so restore
        semantics (unarchive + clear labels) stay atomic — a partial success
        would leave the email in an inconsistent state.
        """
        patch_dict: dict[str, bool] = {
            f"keywords/{k}": v for k, v in patches.items()
        }
        if mailbox_patches:
            for mbox_id, add in mailbox_patches.items():
                patch_dict[f"mailboxIds/{mbox_id}"] = add
        self._call(
            "Email/set",
            {
                "update": {email_id: patch_dict},
            },
        )


__all__ = ["InMemoryMailAdapter", "JmapMailAdapter", "MailAdapter"]
