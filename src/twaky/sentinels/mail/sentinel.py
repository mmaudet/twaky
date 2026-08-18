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
from twaky.oauth.refresh_manager import get_manager
from twaky.sentinels.base import Context, Event, Outcome, Sentinel
from twaky.sentinels.mail.adapter import JmapMailAdapter
from twaky.sentinels.mail.nodes import NodeContext
from twaky.sentinels.mail.pipeline import process_email

log = logging.getLogger(__name__)


def _emit_decision_trace(ctx: Context, email_id: str, state: Any) -> None:
    """SP5c 5.2: append a structured decision trace to ``ctx.trace``.

    Reconstructed post-hoc from the final pipeline state — no need to
    instrument every node individually. Each entry names the pipeline
    stage plus the fields that explain WHY the pipeline took its
    decision, so the /sentinels/mail/runs/[id] page can render them.

    Order of entries reflects execution order in the SP5c pipeline:
    load_thread → match_rules → [spam_triage] → apply_actions →
    thread_status → [select_memories → draft_reply].
    """
    thread = state.get("thread") or []
    latest = thread[-1] if thread else {}
    sender = ""
    from_field = latest.get("from") or []
    if isinstance(from_field, list) and from_field:
        first = from_field[0]
        if isinstance(first, dict):
            sender = str(first.get("email") or "").lower()

    ctx.trace.append(
        {
            "node": "load_thread",
            "email_id": email_id,
            "sender": sender,
            "subject": latest.get("subject") or "",
            "thread_len": len(thread),
        }
    )

    matched_by = state.get("matched_by")
    rule_name = state.get("rule_name")
    match_entry: dict[str, Any] = {
        "node": "match_rules",
        "matched_by": matched_by,
        "rule_name": rule_name,
    }
    # Flag learned-pattern short-circuits explicitly so the UI can
    # emphasize "no LLM was called for this decision".
    if matched_by == "learned_pattern":
        match_entry["short_circuit"] = True
        if state.get("skip_spam_triage"):
            match_entry["skip_spam_triage"] = True
        if state.get("bucket"):
            match_entry["forced_bucket"] = state["bucket"]
    ctx.trace.append(match_entry)

    # spam_triage only runs on non-learned_pattern paths in SP5c.
    if matched_by != "learned_pattern":
        ctx.trace.append(
            {
                "node": "spam_triage",
                "spam_bucket": state.get("spam_bucket"),
                "spam_reason": state.get("spam_reason"),
                "spam_score": state.get("spam_score"),
            }
        )

    ctx.trace.append(
        {
            "node": "apply_actions",
            "actions_applied": state.get("actions_applied") or [],
        }
    )

    status = state.get("status")
    ctx.trace.append(
        {
            "node": "thread_status",
            "status": str(status) if status is not None else None,
        }
    )

    memories = state.get("memories") or []
    if memories:
        ctx.trace.append(
            {
                "node": "select_memories",
                "count": len(memories),
                "memory_ids": [m.get("id") for m in memories if isinstance(m, dict)][
                    :16
                ],
            }
        )

    draft = state.get("draft")
    if draft:
        ctx.trace.append(
            {
                "node": "draft_reply",
                "draft_language": state.get("draft_language"),
                "draft_preview": (draft[:200] + "…") if len(draft) > 200 else draft,
            }
        )


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

        # SP5c 5.2: emit a decision trace into ctx.trace so the runtime
        # can persist it into sentinel_run.trace for the UI.
        _emit_decision_trace(ctx, email_id, state)

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
        ``accountId`` and ``apiUrl``.  Uses ``RefreshManager`` for token
        provisioning so that expired tokens are refreshed transparently.

        Parameters
        ----------
        ctx:
            Base sentinel context (unused here; kept for future caching on ctx).
        """
        session_url = settings.jmap_session_url
        manager = get_manager("mail")

        resp = httpx.get(
            session_url,
            headers={"Authorization": f"Bearer {manager.sync_get_access_token()}"},
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
            token_provider=manager.sync_get_access_token,
            refresh_now=manager.sync_force_refresh,
            account_id=account_id,
            api_url=api_url,
        )


# T7 discovery contract: module-level alias
SentinelClass = MailSentinel

__all__ = ["MailSentinel", "SentinelClass"]
