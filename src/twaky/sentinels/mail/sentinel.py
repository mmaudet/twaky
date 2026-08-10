"""MailSentinel — concrete Sentinel implementation for the mail vertical.

Wired to the ``jmap_poll`` event source. Builds a per-call ``NodeContext``
from the injected ``Context``, resolves the email id from the event, runs the
LangGraph pipeline, and translates the final state into an ``Outcome``.

``SentinelClass = MailSentinel`` at module level satisfies the T7 discovery
contract (``twaky.sentinels.discovery``).
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar, Literal

import httpx

from twaky.config import settings
from twaky.sentinels.base import Context, Event, Outcome, Sentinel
from twaky.sentinels.mail.adapter import JmapMailAdapter
from twaky.sentinels.mail.nodes import NodeContext
from twaky.sentinels.mail.pipeline import process_email

log = logging.getLogger(__name__)


class MailSentinel(Sentinel):
    """Sentinel that classifies incoming mail and drafts replies.

    Class attributes
    ----------------
    name
        ``"mail"`` — matches the sentinel DB row primary key and the
        sub-package directory name (required for discovery).
    version
        Semantic version surfaced in observability and the ``sentinel`` row.
    event_source_kind
        ``"jmap_poll"`` — wired to ``JmapPollingEventSource`` by the runtime.
    """

    name: ClassVar[str] = "mail"
    version: ClassVar[str] = "1.0.0"
    event_source_kind: ClassVar[Literal["rabbitmq", "jmap_poll"]] = "jmap_poll"

    # ------------------------------------------------------------------
    # Sentinel contract
    # ------------------------------------------------------------------

    def process(self, event: Event, ctx: Context) -> Outcome:
        """Process one incoming mail event.

        Resolves the email id from the event payload (multiple fallbacks),
        builds a ``NodeContext``, runs the LangGraph pipeline, and maps the
        final state to an ``Outcome``.

        Email id resolution order:
        1. ``event["payload"]["email"]["id"]``
        2. ``event["payload"]["email_id"]``
        3. ``event["message_id"]``
        """
        email_id = self._resolve_email_id(event)

        mail_adapter = self._build_adapter(ctx)
        node_ctx = NodeContext(
            base=ctx,
            mail=mail_adapter,
            owner_email=settings.twaky_owner_email,
        )

        state = process_email(node_ctx, email_id)

        if state.get("draft"):
            return Outcome.MISSION_CREATED
        if "delegate_to_atlas" in (state.get("actions_applied") or []):
            return Outcome.DELEGATED
        return Outcome.PROCESSED

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_email_id(self, event: Event) -> str:
        """Extract the email id from the event, trying three fallbacks."""
        payload: dict[str, Any] = event.get("payload") or {}

        # 1. payload.email.id
        email_obj = payload.get("email")
        if isinstance(email_obj, dict) and email_obj.get("id"):
            return str(email_obj["id"])

        # 2. payload.email_id
        if payload.get("email_id"):
            return str(payload["email_id"])

        # 3. message_id (JMAP email id at top level)
        return str(event["message_id"])

    def _build_adapter(self, ctx: Context) -> JmapMailAdapter:
        """Resolve the JMAP session once and build a ``JmapMailAdapter``.

        Performs one synchronous GET to ``jmap_session_url`` to retrieve
        ``accountId`` and ``apiUrl``.  A longer-lived cache belongs to SP6b.

        Parameters
        ----------
        ctx:
            Base sentinel context (unused here; kept for future caching on ctx).
        """
        session_url = settings.jmap_session_url
        bearer_token = settings.jmap_bearer_token

        resp = httpx.get(
            session_url,
            headers={"Authorization": f"Bearer {bearer_token}"},
            timeout=10.0,
        )
        resp.raise_for_status()
        session = resp.json()

        # Resolve accountId: primary account for the mail capability
        primary_accounts: dict[str, Any] = session.get("primaryAccounts") or {}
        account_id: str = primary_accounts.get(
            "urn:ietf:params:jmap:mail", ""
        ) or primary_accounts.get("urn:ietf:params:jmap:core", "")

        api_url: str = session.get("apiUrl", "")

        return JmapMailAdapter(
            session_url=session_url,
            bearer_token=bearer_token,
            account_id=account_id,
            api_url=api_url,
        )


# T7 discovery contract: module-level alias
SentinelClass = MailSentinel

__all__ = ["MailSentinel", "SentinelClass"]
