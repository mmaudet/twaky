"""SP5b observer: extended JMAP poller for user actions.

Runs once per tick from the mail sentinel poll loop. Iterates watched
mailboxes, queries Email/changes since the last stored state, classifies
each change (draft_sent / marked_spam / unmarked_spam / moved_to_custom),
and dispatches to the appropriate extractor.

On bootstrap (no prior state for a mailbox), stores the current JMAP
state without replaying history — conservative rollout.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from twaky.config import settings
from twaky.sentinels.mail.extractors.draft_diff import extract_draft_diff
from twaky.sentinels.mail.extractors.folder_move import extract_folder_move
from twaky.sentinels.mail.extractors.reclassification import extract_reclassification
from twaky.sentinels.mail.store import learned_patterns as lp_store
from twaky.sentinels.mail.store import mailbox_state as ms_store
from twaky.sentinels.mail.store.observations import ExtractionOutcome

log = logging.getLogger(__name__)

_SYSTEM_FOLDER_NAMES: frozenset[str] = frozenset(
    {"Inbox", "Drafts", "Templates", "Outbox", "Archive", "Sent", "Trash", "Junk"}
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


class MailObserver:
    """Polls watched mailboxes and dispatches user actions to extractors."""

    async def run_tick(self, adapter: Any, owner_email: str) -> ObserverTickResult:
        if not settings.mail_sentinel_observer_enabled:
            return ObserverTickResult()

        result = ObserverTickResult()
        mailboxes = await self._watched_mailboxes(adapter)
        result.mailboxes_polled = len(mailboxes)

        sent_mailbox: dict | None = None
        for mbx in mailboxes:
            if (mbx.get("role") or "").lower() == "sent":
                sent_mailbox = mbx
            try:
                await self._tick_mailbox(adapter, mbx, owner_email, result)
            except Exception as e:  # noqa: BLE001
                log.warning("observer: mailbox %s failed: %r", mbx.get("id"), e)
                result.errors.append(f"{mbx.get('id')}: {e!r}")
                continue

        # SP7 / Task 141: writing-style analysis (best-effort, gated on
        # `should_analyze` — no LLM call unless delta threshold reached).
        if sent_mailbox is not None:
            try:
                await self._maybe_run_style_analysis(
                    adapter, owner_email, sent_mailbox
                )
            except Exception as e:  # noqa: BLE001
                log.warning("observer: style analysis failed: %r", e)
                result.errors.append(f"style_analysis: {e!r}")

        log.info(
            "observer_tick_done polled=%d obs=%d mem=%d pat=%d errs=%d",
            result.mailboxes_polled,
            result.observations_created,
            result.memories_created,
            result.patterns_updated,
            len(result.errors),
        )
        return result

    async def _maybe_run_style_analysis(
        self, adapter: Any, owner_email: str, sent_mailbox: dict
    ) -> None:
        """Trigger style analysis when the Sent-delta threshold is reached.

        Fetches ``totalEmails`` for the Sent mailbox and delegates to
        ``analyze_style.should_analyze`` for the actual gating. When it
        fires, pulls the last ``SAMPLE_SIZE`` sent mails and runs the
        LLM analysis + upsert.
        """
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

    async def _watched_mailboxes(self, adapter: Any) -> list[dict]:
        all_mbx = await adapter.query_mailboxes()
        watched_roles = set(settings.watched_mailbox_roles_list)
        watched = []
        for mbx in all_mbx:
            role = (mbx.get("role") or "").lower() or None
            name = mbx.get("name") or ""
            if role in watched_roles:
                watched.append(mbx)
                continue
            # Custom folders: role IS NULL AND name not standard
            if role is None and name and name not in _SYSTEM_FOLDER_NAMES:
                watched.append(mbx)
        return watched

    async def _tick_mailbox(
        self,
        adapter: Any,
        mbx: dict,
        owner_email: str,
        result: ObserverTickResult,
    ) -> None:
        mailbox_id = mbx["id"]
        role = (mbx.get("role") or "").lower() or None
        name = mbx.get("name")

        stored = ms_store.get(mailbox_id)
        if stored is None:
            # Bootstrap: read current state, store it, no replay.
            current = await adapter.get_mailbox_state(mailbox_id)
            ms_store.upsert(
                mailbox_id=mailbox_id, jmap_state=current, role=role, name=name
            )
            return

        changes = await adapter.changes(mailbox_id, stored.jmap_state)
        new_state = changes.get("newState") or stored.jmap_state
        for email_id in list(changes.get("created", [])) + list(changes.get("updated", [])):
            try:
                await self._dispatch(adapter, mailbox_id, role, name, email_id, owner_email, result)
            except Exception as e:  # noqa: BLE001
                log.warning("observer: dispatch failed for %s: %r", email_id, e)
                result.errors.append(f"{email_id}: {e!r}")

        ms_store.upsert(
            mailbox_id=mailbox_id, jmap_state=new_state, role=role, name=name
        )

    async def _dispatch(
        self,
        adapter: Any,
        mailbox_id: str,
        role: str | None,
        folder_name: str | None,
        email_id: str,
        owner_email: str,
        result: ObserverTickResult,
    ) -> None:
        email = await adapter.get_email(email_id)
        if email is None:
            return
        from_email = _first_email_address(email.get("from"))
        to_email = _first_email_address(email.get("to"))

        if role == "sent":
            headers = email.get("headers") or []
            in_reply_to = _header(headers, "In-Reply-To") or _header(headers, "References")
            body = _extract_body_text(email)
            r = extract_draft_diff(
                email_id=email_id,
                mailbox_id=mailbox_id,
                sender_email=to_email or from_email,
                recipient_email=to_email or from_email,
                shipped_body=body,
                subject=email.get("subject") or "",
                in_reply_to=in_reply_to,
                owner_email=owner_email,
            )
            self._tally(result, r)
            return

        if role == "junk":
            r = extract_reclassification(
                email_id=email_id,
                mailbox_id=mailbox_id,
                sender_email=from_email,
                direction="in",
            )
            self._tally(result, r)
            return

        # Custom folder (role is None)
        if role is None and folder_name and folder_name not in _SYSTEM_FOLDER_NAMES:
            history = len(
                [p for p in lp_store.list_all() if p.sender_email == from_email]
            )
            r = extract_folder_move(
                email_id=email_id,
                mailbox_id=mailbox_id,
                sender_email=from_email,
                folder_name=folder_name,
                subject=email.get("subject") or "",
                history_count=history,
            )
            self._tally(result, r)

        # Note: unmarked_spam (mail LEAVING junk) is detected as a
        # non-junk mailbox `updated` event; the classify path would need
        # cross-mailbox tracking to detect movement. MVP handles the
        # marked_spam direction only; unmarked_spam is a follow-up.

    def _tally(self, result: ObserverTickResult, r: Any) -> None:
        if r is None:
            return
        result.observations_created += 1
        result.memories_created += len(getattr(r, "memory_ids", []) or [])
        result.patterns_updated += len(getattr(r, "pattern_ids", []) or [])
        if getattr(r, "outcome", None) == ExtractionOutcome.ERROR:
            result.errors.append(str(getattr(r, "error_repr", "unknown error")))


__all__ = ["MailObserver", "ObserverTickResult"]
