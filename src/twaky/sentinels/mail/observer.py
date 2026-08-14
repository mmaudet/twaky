"""SP5b + SP5c observer: extended JMAP poller for user actions.

Runs once per tick from the mail sentinel poll loop. SP5c redesign:
- ONE global ``Email/changes`` call per tick (was N per mailbox, all
  returning the same global delta).
- Dispatch by each email's current ``mailboxIds`` (was: by the polled
  mailbox's role, causing false dispatches across watched mailboxes).
- ``unmarked_spam`` detection: mail in INBOX with an open
  ``mail_sentinel_spam_decision`` row → dispatched as
  ``extract_reclassification(direction="out")``.

Global state row uses the magic key ``"__global__"`` in
``mail_sentinel_mailbox_state`` — no schema change needed. Per-mailbox
rows written by the SP5b path become stale but harmless.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from twaky.config import settings
from twaky.db import get_pool
from twaky.sentinels.mail.extractors.draft_diff import extract_draft_diff
from twaky.sentinels.mail.extractors.folder_move import extract_folder_move
from twaky.sentinels.mail.extractors.reclassification import extract_reclassification
from twaky.sentinels.mail.store import learned_patterns as lp_store
from twaky.sentinels.mail.store import mailbox_state as ms_store
from twaky.sentinels.mail.store.observations import ExtractionOutcome

log = logging.getLogger(__name__)

_GLOBAL_STATE_KEY = "__global__"

_SYSTEM_FOLDER_NAMES: frozenset[str] = frozenset(
    {
        "inbox",
        "drafts",
        "templates",
        "outbox",
        "archive",
        "sent",
        "trash",
        "junk",
    }
)


@dataclass
class ObserverTickResult:
    mailboxes_polled: int = 0
    observations_created: int = 0
    memories_created: int = 0
    patterns_updated: int = 0
    llm_calls: int = 0
    errors: list[str] = field(default_factory=list)


def _first_email_address(field_value: Any) -> str:
    """From JMAP addresses list: [{name, email}] → 'email' or ''."""
    if isinstance(field_value, list) and field_value:
        entry = field_value[0]
        if isinstance(entry, dict):
            return str(entry.get("email") or "").lower()
    return ""


def _header(headers: list[dict], name: str) -> str | None:
    lname = name.lower()
    for h in headers or []:
        if str(h.get("name", "")).lower() == lname:
            v = h.get("value")
            return str(v) if v is not None else None
    return None


def _extract_body_text(email: dict) -> str:
    """Best-effort: assemble text body from bodyValues+textBody."""
    text_body = email.get("textBody") or []
    body_values = email.get("bodyValues") or {}
    parts: list[str] = []
    for tb in text_body:
        pid = tb.get("partId")
        if not pid:
            continue
        bv = body_values.get(pid)
        if bv and bv.get("value"):
            parts.append(str(bv["value"]))
    return "\n".join(parts)


def _has_open_spam_decision(email_id: str) -> bool:
    """Return True iff a ``mail_sentinel_spam_decision`` exists for *email_id*
    with ``restored_at IS NULL`` (i.e. Twaky classified as spam, user has
    not yet restored). Used to detect ``unmarked_spam``.
    """
    sql = (
        "SELECT 1 FROM mail_sentinel_spam_decision "
        "WHERE email_id = %s AND restored_at IS NULL LIMIT 1"
    )
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (email_id,))
            return cur.fetchone() is not None
    except Exception as e:  # noqa: BLE001 — never block observer on DB errors
        log.warning("observer: spam_decision check failed for %s: %r", email_id, e)
        return False


class MailObserver:
    """SP5c: single global tick + dispatch by current ``mailboxIds``."""

    async def run_tick(self, adapter: Any, owner_email: str) -> ObserverTickResult:
        if not settings.mail_sentinel_observer_enabled:
            return ObserverTickResult()

        result = ObserverTickResult()

        # Resolve all mailboxes (id → (role, name)) — cached per tick.
        try:
            all_mbx = await adapter.query_mailboxes()
        except Exception as e:  # noqa: BLE001
            log.warning("observer: query_mailboxes failed: %r", e)
            result.errors.append(f"query_mailboxes: {e!r}")
            return result

        mbx_by_id: dict[str, dict[str, str | None]] = {
            m["id"]: {
                "role": (m.get("role") or "").lower() or None,
                "name": m.get("name") or "",
            }
            for m in all_mbx
        }
        result.mailboxes_polled = len(mbx_by_id)

        # Global tick state (single row, magic key __global__).
        stored = ms_store.get(_GLOBAL_STATE_KEY)
        if stored is None:
            # Bootstrap: read current global state, no replay of history.
            try:
                current = await adapter.get_global_state()
            except Exception as e:  # noqa: BLE001
                log.warning("observer: get_global_state (bootstrap) failed: %r", e)
                result.errors.append(f"bootstrap: {e!r}")
                return result
            ms_store.upsert(
                mailbox_id=_GLOBAL_STATE_KEY,
                jmap_state=current,
                role=None,
                name=_GLOBAL_STATE_KEY,
            )
            return result

        try:
            changes = await adapter.changes(stored.jmap_state)
        except Exception as e:  # noqa: BLE001
            log.warning("observer: Email/changes failed: %r", e)
            result.errors.append(f"changes: {e!r}")
            return result

        new_state = changes.get("newState") or stored.jmap_state
        email_ids = list(changes.get("created", [])) + list(changes.get("updated", []))
        for email_id in email_ids:
            try:
                await self._dispatch(
                    adapter, email_id, mbx_by_id, owner_email, result
                )
            except Exception as e:  # noqa: BLE001
                log.warning("observer: dispatch failed for %s: %r", email_id, e)
                result.errors.append(f"{email_id}: {e!r}")

        ms_store.upsert(
            mailbox_id=_GLOBAL_STATE_KEY,
            jmap_state=new_state,
            role=None,
            name=_GLOBAL_STATE_KEY,
        )

        # SP7 style analysis (unchanged): fires if Sent-delta reached.
        sent_mailbox = self._sent_mailbox(mbx_by_id)
        if sent_mailbox is not None:
            try:
                await self._maybe_run_style_analysis(
                    adapter, owner_email, sent_mailbox
                )
            except Exception as e:  # noqa: BLE001
                log.warning("observer: style analysis failed: %r", e)
                result.errors.append(f"style_analysis: {e!r}")

        log.info(
            "observer_tick_done polled=%d changes=%d obs=%d mem=%d pat=%d errs=%d",
            result.mailboxes_polled,
            len(email_ids),
            result.observations_created,
            result.memories_created,
            result.patterns_updated,
            len(result.errors),
        )
        return result

    def _sent_mailbox(
        self, mbx_by_id: dict[str, dict[str, str | None]]
    ) -> dict[str, Any] | None:
        for mid, meta in mbx_by_id.items():
            if meta.get("role") == "sent":
                return {"id": mid, **meta}
        return None

    async def _dispatch(
        self,
        adapter: Any,
        email_id: str,
        mbx_by_id: dict[str, dict[str, str | None]],
        owner_email: str,
        result: ObserverTickResult,
    ) -> None:
        """Fetch email + route to extractor based on current mailboxIds.

        Priority order :
        1. In Sent → draft_diff.
        2. In Junk → marked_spam.
        3. In Inbox AND has an open spam_decision → unmarked_spam.
        4. In a custom (non-system) folder → moved_to_custom.
        """
        email = await adapter.get_email(email_id)
        if email is None:
            return

        mailbox_ids = email.get("mailboxIds") or {}
        # JMAP mailboxIds shape: {mailbox_id: True}
        if isinstance(mailbox_ids, dict):
            active_mids = [mid for mid, present in mailbox_ids.items() if present]
        else:
            active_mids = list(mailbox_ids)

        current_roles: dict[str, str | None] = {}
        current_names: dict[str, str | None] = {}
        for mid in active_mids:
            meta = mbx_by_id.get(mid, {})
            current_roles[mid] = meta.get("role")
            current_names[mid] = meta.get("name")

        from_email = _first_email_address(email.get("from"))
        to_email = _first_email_address(email.get("to"))
        role_set = {r for r in current_roles.values() if r}

        # 1. Sent → draft_diff
        if "sent" in role_set:
            sent_mid = next(
                mid for mid, r in current_roles.items() if r == "sent"
            )
            headers = email.get("headers") or []
            in_reply_to = _header(headers, "In-Reply-To") or _header(
                headers, "References"
            )
            body = _extract_body_text(email)
            r = extract_draft_diff(
                email_id=email_id,
                mailbox_id=sent_mid,
                sender_email=to_email or from_email,
                recipient_email=to_email or from_email,
                shipped_body=body,
                subject=email.get("subject") or "",
                in_reply_to=in_reply_to,
                owner_email=owner_email,
            )
            self._tally(result, r)
            return

        # 2. Junk → marked_spam
        if "junk" in role_set:
            junk_mid = next(
                mid for mid, r in current_roles.items() if r == "junk"
            )
            r = extract_reclassification(
                email_id=email_id,
                mailbox_id=junk_mid,
                sender_email=from_email,
                direction="in",
            )
            self._tally(result, r)
            return

        # 3. Inbox AND open spam_decision → unmarked_spam (SP5c Fix B)
        if "inbox" in role_set and _has_open_spam_decision(email_id):
            inbox_mid = next(
                mid for mid, r in current_roles.items() if r == "inbox"
            )
            r = extract_reclassification(
                email_id=email_id,
                mailbox_id=inbox_mid,
                sender_email=from_email,
                direction="out",
            )
            self._tally(result, r)
            return

        # 4. Custom folder (role IS NULL AND name not standard)
        for mid in active_mids:
            role = current_roles.get(mid)
            name = current_names.get(mid) or ""
            if role is None and name and name.lower() not in _SYSTEM_FOLDER_NAMES:
                history = len(
                    [p for p in lp_store.list_all() if p.sender_email == from_email]
                )
                r = extract_folder_move(
                    email_id=email_id,
                    mailbox_id=mid,
                    sender_email=from_email,
                    folder_name=name,
                    subject=email.get("subject") or "",
                    history_count=history,
                )
                self._tally(result, r)
                return

    async def _maybe_run_style_analysis(
        self, adapter: Any, owner_email: str, sent_mailbox: dict
    ) -> None:
        """Trigger style analysis when the Sent-delta threshold is reached."""
        from twaky.sentinels.mail import analyze_style as az

        if not owner_email:
            return

        sent_mailbox_id = sent_mailbox["id"]
        current_total = await adapter.get_mailbox_total(sent_mailbox_id)
        if not az.should_analyze(owner_email, current_total):
            return

        raw_samples = await adapter.list_recent_emails(
            sent_mailbox_id, limit=az.SAMPLE_SIZE
        )
        samples = [
            {
                "subject": s.get("subject") or "",
                "body": _extract_body_text(s),
            }
            for s in raw_samples
        ]
        display_name = owner_email.split("@")[0]

        log.info(
            "analyze_style: triggered for %s (total=%d, samples=%d)",
            owner_email,
            current_total,
            len(samples),
        )
        stored = az.run_analysis(
            owner_email=owner_email,
            display_name=display_name,
            current_sent_count=current_total,
            samples=samples,
        )
        if stored:
            log.info(
                "analyze_style: profile stored for %s (sample_size=%d)",
                owner_email,
                stored.sample_size,
            )

    def _tally(self, result: ObserverTickResult, r: Any) -> None:
        if r is None:
            return
        result.observations_created += 1
        result.memories_created += len(getattr(r, "memory_ids", []) or [])
        result.patterns_updated += len(getattr(r, "pattern_ids", []) or [])
        if getattr(r, "outcome", None) == ExtractionOutcome.ERROR:
            result.errors.append(str(getattr(r, "error_repr", "unknown error")))


__all__ = ["MailObserver", "ObserverTickResult"]
