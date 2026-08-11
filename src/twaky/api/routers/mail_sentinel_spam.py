"""Mail-sentinel spam REST endpoints.

Provides 3 endpoints under /mail-sentinel/spam that expose the spam decision
list, restore, and stats to the Twaky owner.
All routes are protected by the ``require_owner`` dependency.

Restore semantics (JMAP-first, two-phase):
1. Fetch decision row — 404 if missing.
2. Check not already restored — 409 if so.
3. Patch JMAP keywords ($junk=False, nonjunk=True, __spam__=False,
   newsletter=False) — 502 if JMAP fails (DB not touched).
4. Update DB via spam_decisions.restore().
5. Return the updated SpamDecision row.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Query
from starlette.responses import Response

from twaky.api.deps import require_owner
from twaky.api.errors import error_response
from twaky.api.schemas.spam import SpamDecision, SpamStats
from twaky.config import settings
from twaky.oauth.refresh_manager import get_manager
from twaky.sentinels.mail.adapter import JmapMailAdapter
from twaky.sentinels.mail.store import spam_decisions

router = APIRouter(prefix="/mail-sentinel/spam", tags=["mail-sentinel-spam"])

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JMAP adapter builder
# ---------------------------------------------------------------------------


def _get_mail_adapter() -> JmapMailAdapter:
    """Build a JmapMailAdapter from current settings and the mail RefreshManager.

    Resolves the JMAP session once (synchronous GET to jmap_session_url) to
    obtain ``accountId`` and ``apiUrl``.  Uses ``RefreshManager`` for token
    provisioning so expired tokens are refreshed transparently.

    Mirrors ``MailSentinel._build_adapter`` from sentinel.py (SP6 T24).
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

    primary_accounts: dict = session.get("primaryAccounts") or {}
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


# ---------------------------------------------------------------------------
# Mapping helper
# ---------------------------------------------------------------------------


def _to_schema(d: spam_decisions.SpamDecision) -> SpamDecision:
    return SpamDecision(
        id=d.id,
        email_id=d.email_id,
        thread_id=d.thread_id,
        sender_email=d.sender_email,
        subject=d.subject,
        received_at=d.received_at,
        bucket=d.bucket,
        signal_source=d.signal_source,
        score=d.score,
        reason=d.reason,
        restored_at=d.restored_at,
        restored_by=d.restored_by,
        decided_at=d.decided_at,
    )


# ---------------------------------------------------------------------------
# GET /mail-sentinel/spam
# ---------------------------------------------------------------------------


@router.get("", response_model=list[SpamDecision])
def list_spam(
    bucket: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    before: datetime | None = None,
    _email: str = Depends(require_owner),
) -> list[SpamDecision]:
    """Return recent spam decisions ordered by decided_at DESC.

    Optionally filter by ``bucket`` (``spam``, ``newsletter``,
    ``phishing-alert``) and/or cursor ``before`` (ISO-8601 datetime).
    ``limit`` is bounded 1..500, defaulting to 50.
    """
    rows = spam_decisions.list_recent(bucket=bucket, limit=limit, before=before)
    return [_to_schema(r) for r in rows]


# ---------------------------------------------------------------------------
# POST /mail-sentinel/spam/{decision_id}/restore
# ---------------------------------------------------------------------------


@router.post("/{decision_id}/restore", response_model=SpamDecision)
def restore(
    decision_id: UUID,
    _email: str = Depends(require_owner),
) -> SpamDecision | Response:
    """Restore a spam-archived email to the inbox.

    JMAP-first two-phase: patches keywords on the JMAP server before touching
    the DB, so a JMAP failure leaves state consistent (DB not modified).

    Error codes
    -----------
    404 spam_decision_not_found
        No decision row with the given id.
    409 already_restored
        The decision was already restored.
    502 jmap_restore_failed
        JMAP server returned an error; DB was not modified.
    """
    # Phase 1 — fetch and validate
    d = spam_decisions.get(decision_id)
    if d is None:
        return error_response(
            code="spam_decision_not_found",
            message="no such decision",
            status_code=404,
        )
    if d.restored_at is not None:
        return error_response(
            code="already_restored",
            message="already restored",
            status_code=409,
        )

    # Phase 2 — JMAP restore: clear spam markers + re-add to INBOX.
    # The mail may have been archived by ``match_rules`` firing a rule with
    # an ``archive`` action after ``spam_triage`` set the bucket — so we
    # can't rely on the original design assumption that mailboxIds is
    # untouched. All patches go in ONE Email/set for atomicity.
    try:
        adapter = _get_mail_adapter()
        inbox_id: str | None = None
        resolver = getattr(adapter, "resolve_role_mailbox_id", None)
        if callable(resolver):
            try:
                inbox_id = resolver("inbox")
            except Exception:  # noqa: BLE001
                inbox_id = None  # keep going; keywords still get cleared
        mailbox_patches: dict[str, bool] = (
            {inbox_id: True} if inbox_id else {}
        )
        adapter.set_keywords_bulk(
            d.email_id,
            {
                # Spam / nonjunk flags
                "$junk": False,
                "nonjunk": True,
                # Label keywords: the sentinel writes labels via
                # ``adapter.label()`` which prefixes ``$label-``. Legacy
                # unprefixed names kept for backward compatibility with any
                # decisions written before this fix.
                "$label-__spam__": False,
                "$label-newsletter": False,
                "__spam__": False,
                "newsletter": False,
            },
            mailbox_patches=mailbox_patches,
        )
    except Exception as e:
        log.exception("JMAP restore failed for decision %s", decision_id)
        return error_response(
            code="jmap_restore_failed",
            message=f"{type(e).__name__}: JMAP server rejected the restore request",
            status_code=502,
        )

    # Phase 3 — DB update
    try:
        updated = spam_decisions.restore(decision_id, _email)
    except spam_decisions.AlreadyRestored:
        return error_response(
            code="already_restored",
            message="already restored",
            status_code=409,
        )

    return _to_schema(updated)


# ---------------------------------------------------------------------------
# GET /mail-sentinel/spam/stats
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=SpamStats)
def get_stats(
    days: Annotated[int, Query(ge=1, le=3650)] = 30,
    _email: str = Depends(require_owner),
) -> SpamStats:
    """Return aggregated spam decision counts for the last ``days`` days.

    Returns counts for ``spam``, ``newsletter``, ``phishing_alert``,
    ``restored``, and ``total_processed`` buckets.
    """
    raw = spam_decisions.stats(days=days)
    return SpamStats(
        spam=raw["spam"],
        newsletter=raw["newsletter"],
        phishing_alert=raw["phishing_alert"],
        restored=raw["restored"],
        total_processed=raw["total_processed"],
    )


__all__ = ["router"]
