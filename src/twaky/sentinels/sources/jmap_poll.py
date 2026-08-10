"""JMAP delta-polling event source for the sentinels framework.

Polls a JMAP mail server for new inbox messages using the ``Email/changes``
delta protocol.  Yields one normalised ``Event`` per newly arrived email
without ever re-listing the whole inbox.

Protocol outline (spec §4.5.2 + §11):

1. **Session discovery** — ``GET session_url`` with Bearer auth.  The
   response carries ``primaryAccounts["urn:ietf:params:jmap:mail"]``
   (accountId) and ``apiUrl``.
2. **Mailbox lookup** — ``POST apiUrl`` with ``Mailbox/get``; find the entry
   whose ``role == "inbox"`` (or whose ``name`` matches *mailbox_name*).
3. **Seed** (first poll, no persisted state) — ``Email/query`` with
   ``limit: 1``; capture ``queryState`` without pulling the full inbox.
   Persist as ``jmap_last_state``.  Yield nothing on this iteration.
4. **Delta poll** — ``Email/changes { sinceState }``; response has
   ``newState``, ``created``, ``updated``, ``destroyed``.
5. **Email fetch** — ``Email/get`` for each ``created`` id, requesting body +
   headers.
6. **Yield** one ``Event`` per fetched email; ``source_kind="jmap_poll"``.
7. Persist ``newState``; sleep ``poll_interval_s`` (interruptible via
   ``stop_event``).

On HTTP 401 (bearer expired): call ``force_refresh()`` on the manager and
retry once.  If still 401, log an error, record it in the oauth repository,
sleep, and continue the outer loop.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from twaky.oauth import repository as oauth_repository
from twaky.oauth.refresh_manager import RefreshManager, get_manager
from twaky.sentinels import repository
from twaky.sentinels.base import Event
from twaky.sentinels.sources.base import Ack, EventSource, _noop_ack

log = logging.getLogger(__name__)

_MAIL_CAPABILITY = "urn:ietf:params:jmap:mail"

# Properties fetched for each new email.
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
    "htmlBody",
    "bodyValues",
    "headers",
    "hasAttachment",
]


class JmapPollingEventSource(EventSource):
    """Poll a JMAP server for new inbox emails and yield normalised Events.

    Parameters
    ----------
    sentinel_name:
        Primary key in the ``sentinel`` table; used to read/write
        ``jmap_last_state`` in ``config_values``.
    session_url:
        Full URL of the JMAP session endpoint (e.g.
        ``https://jmap-new.linagora.com/jmap/session``).
    refresh_manager:
        Optional ``RefreshManager`` instance.  Defaults to
        ``get_manager(sentinel_name)`` when ``None``.  Pass an explicit
        instance in tests to inject a mock.
    account_email:
        The email address associated with the JMAP account (informational;
        not used in the protocol but useful for logging).
    mailbox_name:
        Fallback mailbox name used when no mailbox carries ``role == "inbox"``.
        Defaults to ``"INBOX"``.
    poll_interval_s:
        Seconds to sleep between polls.  ``stop_event`` can interrupt the
        sleep early.

    Ack semantics (at-most-once)
    ----------------------------
    JMAP has no upstream acknowledgement mechanism.  ``_persist_state(new_state)``
    is called *after* fetching each batch but *before* the consumer finishes
    processing every yielded event.  A consumer crash mid-batch will lose the
    unprocessed events in that batch — they will not be re-delivered on restart
    because the state pointer has already advanced.  This is at-most-once delivery,
    unlike the at-least-once guarantee provided by RabbitMQ.  SP6b will add
    per-message state persistence to achieve at-least-once for JMAP sources.
    """

    def __init__(
        self,
        *,
        sentinel_name: str,
        session_url: str,
        refresh_manager: RefreshManager | None = None,
        account_email: str,
        mailbox_name: str = "INBOX",
        poll_interval_s: int = 60,
    ) -> None:
        self._sentinel_name = sentinel_name
        self._session_url = session_url
        self._manager = (
            refresh_manager
            if refresh_manager is not None
            else get_manager(sentinel_name)
        )
        self._account_email = account_email
        self._mailbox_name = mailbox_name
        self._poll_interval_s = poll_interval_s

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _auth_headers(self) -> dict[str, str]:
        """Build per-request Authorization headers with a fresh token."""
        token = await self._manager.get_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def _discover_session(
        self, client: httpx.AsyncClient
    ) -> tuple[str, str, str]:
        """Return ``(accountId, apiUrl, inboxId)``.

        Step 1: GET session_url → accountId + apiUrl.
        Step 2: POST apiUrl Mailbox/get → find the inbox mailbox id.
        """
        # Step 1: session
        headers = await self._auth_headers()
        req = client.build_request("GET", self._session_url, headers=headers)
        session_resp = await client.send(req)
        session_resp.raise_for_status()
        session = session_resp.json()

        account_id: str = session["primaryAccounts"][_MAIL_CAPABILITY]
        api_url: str = session["apiUrl"]

        # Step 2: mailbox list
        mailbox_body = {
            "using": [_MAIL_CAPABILITY],
            "methodCalls": [
                [
                    "Mailbox/get",
                    {
                        "accountId": account_id,
                        "ids": None,
                        "properties": ["id", "name", "role"],
                    },
                    "0",
                ]
            ],
        }
        mbox_headers = await self._auth_headers()
        mbox_resp = await client.post(api_url, json=mailbox_body, headers=mbox_headers)
        mbox_resp.raise_for_status()
        mbox_data = mbox_resp.json()

        mailboxes: list[dict[str, Any]] = mbox_data["methodResponses"][0][1]["list"]
        inbox_id: str | None = None
        for mbox in mailboxes:
            if mbox.get("role") == "inbox":
                inbox_id = mbox["id"]
                break
        if inbox_id is None:
            # Fallback: match by name
            for mbox in mailboxes:
                if mbox.get("name", "").lower() == self._mailbox_name.lower():
                    inbox_id = mbox["id"]
                    break
        if inbox_id is None:
            raise RuntimeError(
                f"No inbox mailbox found for account {account_id!r} "
                f"(looked for role='inbox' and name={self._mailbox_name!r})"
            )

        log.info(
            "jmap_poll: discovered account=%s inbox=%s api=%s",
            account_id,
            inbox_id,
            api_url,
        )
        return account_id, api_url, inbox_id

    async def _seed_state(
        self,
        client: httpx.AsyncClient,
        api_url: str,
        account_id: str,
        inbox_id: str,
    ) -> str:
        """Capture the current queryState via Email/query (no full listing).

        Returns the ``queryState`` string to be persisted as the initial
        ``sinceState`` for the first delta poll.
        """
        body = {
            "using": [_MAIL_CAPABILITY],
            "methodCalls": [
                [
                    "Email/query",
                    {
                        "accountId": account_id,
                        "filter": {"inMailbox": inbox_id},
                        "limit": 1,
                        "calculateTotal": False,
                    },
                    "0",
                ]
            ],
        }
        headers = await self._auth_headers()
        resp = await client.post(api_url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        query_state: str = data["methodResponses"][0][1]["queryState"]
        log.info("jmap_poll: seeded queryState=%s", query_state)
        return query_state

    async def _poll_changes(
        self,
        client: httpx.AsyncClient,
        api_url: str,
        account_id: str,
        since_state: str,
    ) -> tuple[str, list[str]]:
        """Run Email/changes.  Return ``(newState, created_ids)``."""
        body = {
            "using": [_MAIL_CAPABILITY],
            "methodCalls": [
                [
                    "Email/changes",
                    {
                        "accountId": account_id,
                        "sinceState": since_state,
                        "maxChanges": 200,
                    },
                    "0",
                ]
            ],
        }
        headers = await self._auth_headers()
        resp = await client.post(api_url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        result = data["methodResponses"][0][1]
        new_state: str = result["newState"]
        created: list[str] = result.get("created", []) or []
        return new_state, created

    async def _fetch_emails(
        self,
        client: httpx.AsyncClient,
        api_url: str,
        account_id: str,
        ids: list[str],
    ) -> list[dict[str, Any]]:
        """Run Email/get for *ids*.  Return the list of email objects."""
        body = {
            "using": [_MAIL_CAPABILITY],
            "methodCalls": [
                [
                    "Email/get",
                    {
                        "accountId": account_id,
                        "ids": ids,
                        "properties": _EMAIL_PROPERTIES,
                        "fetchTextBodyValues": True,
                        "fetchHTMLBodyValues": False,
                        "maxBodyValueBytes": 32768,
                    },
                    "0",
                ]
            ],
        }
        headers = await self._auth_headers()
        resp = await client.post(api_url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        result = data["methodResponses"][0][1]
        not_found: list[str] = result.get("notFound") or []
        if not_found:
            log.warning(
                "jmap_poll[%s]: Email/get: %d ids not found: %s",
                self._sentinel_name,
                len(not_found),
                not_found[:5],
            )
        return result["list"]  # type: ignore[no-any-return]

    def _load_state(self) -> str | None:
        """Read ``jmap_last_state`` from the sentinel's DB config_values."""
        row = repository.get(self._sentinel_name)
        if row is None:
            return None
        return row.config_values.get("jmap_last_state")  # type: ignore[no-any-return]

    def _persist_state(self, state: str) -> None:
        """Write ``jmap_last_state`` into the sentinel DB row."""
        repository.update_config_value(self._sentinel_name, "jmap_last_state", state)

    # ------------------------------------------------------------------
    # Internal: 401-aware HTTP helper
    # ------------------------------------------------------------------

    async def _call_with_401_retry(
        self,
        coro_factory: Any,
        stop_event: asyncio.Event,
    ) -> Any:
        """Call *coro_factory()*, retry once after force_refresh on 401.

        If the retry also returns 401, log an error, record it in the oauth
        repository, sleep poll_interval, and raise a ``_DoubleAuthError`` so
        the outer loop can ``continue``.
        """
        try:
            return await coro_factory()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 401:
                raise
            log.warning(
                "jmap_poll[%s]: 401 on first attempt — force-refreshing token",
                self._sentinel_name,
            )
            await self._manager.force_refresh()
            # Retry once with the new token
            try:
                return await coro_factory()
            except httpx.HTTPStatusError as exc2:
                if exc2.response.status_code != 401:
                    raise
                log.error(
                    "jmap_poll[%s]: 401 persists after force_refresh — "
                    "recording error and waiting %ds",
                    self._sentinel_name,
                    self._poll_interval_s,
                )
                oauth_repository.set_error(self._sentinel_name, "401_after_refresh")
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=float(self._poll_interval_s),
                    )
                except TimeoutError:
                    pass
                raise _DoubleAuthError from exc2

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def stream(
        self, *, stop_event: asyncio.Event
    ) -> AsyncIterator[tuple[Event, Ack]]:  # type: ignore[override]
        """Yield ``(Event, _noop_ack)`` pairs until ``stop_event`` is set."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Retry loop: re-entered only on recoverable errors (e.g. double-401).
            while not stop_event.is_set():
                try:
                    # --- Session discovery (once per stream() call / retry) ---
                    account_id, api_url, inbox_id = await self._call_with_401_retry(
                        lambda _c=client: self._discover_session(_c),
                        stop_event,
                    )

                    # --- Seed (first run only) ---
                    state = self._load_state()
                    if state is None:
                        seed = await self._call_with_401_retry(
                            lambda _c=client, _u=api_url, _a=account_id, _i=inbox_id: (
                                self._seed_state(_c, _u, _a, _i)
                            ),
                            stop_event,
                        )
                        self._persist_state(seed)
                        state = seed
                        log.info(
                            "jmap_poll[%s]: seed complete, state=%s",
                            self._sentinel_name,
                            seed,
                        )
                        # Yield nothing on seed run; wait poll_interval before first delta.
                        try:
                            await asyncio.wait_for(
                                stop_event.wait(),
                                timeout=float(self._poll_interval_s),
                            )
                        except TimeoutError:
                            pass
                        if stop_event.is_set():
                            return

                    # --- Delta poll loop (runs until stop_event or error) ---
                    while not stop_event.is_set():
                        new_state, created = await self._call_with_401_retry(
                            lambda _c=client, _u=api_url, _a=account_id, _s=state: (
                                self._poll_changes(_c, _u, _a, _s)
                            ),
                            stop_event,
                        )

                        if created:
                            emails = await self._call_with_401_retry(
                                lambda _c=client, _u=api_url, _a=account_id, _ids=created: (
                                    self._fetch_emails(_c, _u, _a, _ids)
                                ),
                                stop_event,
                            )
                            for email in emails:
                                event: Event = {
                                    "source_kind": "jmap_poll",
                                    "source_ref": account_id,
                                    "message_id": email["id"],
                                    "payload": {"email": email},
                                }
                                yield event, _noop_ack
                                log.debug(
                                    "jmap_poll[%s]: yielded email=%s",
                                    self._sentinel_name,
                                    email["id"],
                                )

                        self._persist_state(new_state)
                        state = new_state

                        # Interruptible sleep
                        try:
                            await asyncio.wait_for(
                                stop_event.wait(),
                                timeout=float(self._poll_interval_s),
                            )
                        except TimeoutError:
                            pass  # normal: poll_interval elapsed

                    # Inner loop exited because stop_event is set.
                    return

                except _DoubleAuthError:
                    # Already logged + recorded; outer while will re-check stop_event.
                    continue

                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 401:
                        # Should not reach here after _call_with_401_retry,
                        # but guard just in case.
                        log.error(
                            "jmap_poll[%s]: unexpected 401 escaped retry logic",
                            self._sentinel_name,
                        )
                        try:
                            await asyncio.wait_for(
                                stop_event.wait(),
                                timeout=float(self._poll_interval_s),
                            )
                        except TimeoutError:
                            pass
                        continue
                    raise


class _DoubleAuthError(Exception):
    """Internal sentinel: 401 persisted after force_refresh — outer loop continues."""


__all__ = ["JmapPollingEventSource"]
