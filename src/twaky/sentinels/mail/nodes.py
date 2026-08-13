"""Mail sentinel pipeline nodes.

Processes a mail event through a sequence of transformations: loads the email
thread, classifies it, learns patterns, drafts replies, and applies actions.

Nodes in this module:
- ``make_load_thread`` — fetch email & thread context (T17)
- ``make_match_rules`` — 4-stage cascade matcher (T18)
- more nodes to follow (T19-T23).
"""

from __future__ import annotations

import fnmatch
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from twaky.config import settings
from twaky.sentinels.mail.adapter import MailAdapter
from twaky.sentinels.mail.llm.hardening import Hardening
from twaky.sentinels.mail.llm.invoke import structured_call
from twaky.sentinels.mail.llm.tiers import UseCase
from twaky.sentinels.mail.prompts.draft_reply import draft_reply_prompt
from twaky.sentinels.mail.prompts.memories import select_memories_prompt
from twaky.sentinels.mail.prompts.rules import choose_rule_prompt, learn_pattern_prompt
from twaky.sentinels.mail.prompts.spam_check import spam_check_prompt
from twaky.sentinels.mail.prompts.thread_status import thread_status_prompt
from twaky.sentinels.mail.schemas import (
    ChooseRuleOutput,
    DraftReplyOutput,
    LearnPatternOutput,
    SelectMemoriesOutput,
    SpamCheckOutput,
    ThreadStatusOutput,
)
from twaky.sentinels.mail.state import MailAgentState, ThreadStatus
from twaky.sentinels.mail.store import learned_patterns as lp_store
from twaky.sentinels.mail.store import memories as mem_store
from twaky.sentinels.mail.store import rules as rules_store
from twaky.sentinels.mail.store import spam_decisions

if TYPE_CHECKING:
    from twaky.sentinels.base import Context
    from twaky.sentinels.mail.store.rules import MailRule

log = logging.getLogger(__name__)


@dataclass
class NodeContext:
    """Mail-specific execution context for pipeline nodes.

    Extends the base sentinel Context with mail-adapter access and owner
    email address. Passed to every node factory.

    Attributes
    ----------
    base
        The base sentinel Context (db_pool, mission_emitter, logger, etc.).
    mail
        The MailAdapter for fetching emails and threads.
    owner_email
        Email address of the sentinel owner (used for reply attribution).
    """

    base: Context
    mail: MailAdapter
    owner_email: str


def make_load_thread(ctx: NodeContext) -> Callable[[MailAgentState], MailAgentState]:
    """Factory for the load_thread node.

    Fetches the email by id, then loads its thread context:
    - If the email has a threadId, fetches all emails in that thread.
    - Otherwise, returns a single-entry thread with just the email.

    Returns a node function that takes the current state and returns
    a partial state dict with the ``thread`` key.

    Parameters
    ----------
    ctx
        Execution context with mail adapter.

    Returns
    -------
    Callable
        A node function ``(MailAgentState) -> MailAgentState``.
    """

    def _node(state: MailAgentState) -> MailAgentState:
        email_id = state["email_id"]
        email = ctx.mail.get_email(email_id)
        thread_id = email.get("threadId")
        thread = ctx.mail.get_thread(thread_id) if thread_id else [email]
        return {"thread": thread}

    return _node


# ---------------------------------------------------------------------------
# Private helpers for make_match_rules
# ---------------------------------------------------------------------------


def _sender_email(email: dict[str, Any]) -> str:
    """Return the lowercased sender email address from the email dict."""
    return email["from"][0]["email"].lower()


def _field_value(email: dict[str, Any], field: str) -> str:
    """Extract the string value of a named field from an email dict.

    Supported fields:
    - ``from`` — lowercased sender email (first entry).
    - ``to`` — comma-joined lowercased recipient emails.
    - ``subject`` — email subject string.
    - ``body`` — text body; falls back to ``preview`` if absent.
    - ``header:<name>`` — case-insensitive lookup in ``email["headers"]``.
    """
    if field == "from":
        return _sender_email(email)
    if field == "to":
        recipients = email.get("to") or []
        return ", ".join(r["email"].lower() for r in recipients)
    if field == "subject":
        return str(email.get("subject") or "")
    if field == "body":
        # textBody / bodyValues or fall back to preview
        body_values: dict[str, Any] = email.get("bodyValues") or {}
        text_parts: list[dict[str, Any]] = email.get("textBody") or []
        if text_parts and body_values:
            part_id = text_parts[0].get("partId", "")
            part = body_values.get(part_id) or {}
            text = part.get("value", "")
            if text:
                return str(text)
        return str(email.get("preview") or "")
    if field.startswith("header:"):
        header_name = field[len("header:") :].lower()
        headers: list[dict[str, Any]] = email.get("headers") or []
        for h in headers:
            if h.get("name", "").lower() == header_name:
                return str(h.get("value") or "")
        return ""
    return ""


def _condition_matches(email: dict[str, Any], cond: dict[str, Any]) -> bool:
    """Return True if *email* satisfies the single condition *cond*.

    Dispatches on ``cond["operator"]``: equals, contains, regex, glob.
    Field value and condition value are compared case-insensitively where
    applicable (contains, equals).
    """
    field: str = cond.get("field", "")
    operator: str = cond.get("operator", "")
    value: str = cond.get("value", "")

    field_val = _field_value(email, field)

    if operator == "equals":
        return field_val.lower() == value.lower()
    if operator == "contains":
        return value.lower() in field_val.lower()
    if operator == "regex":
        return bool(re.search(value, field_val, re.IGNORECASE))
    if operator == "glob":
        return fnmatch.fnmatch(field_val.lower(), value.lower())
    return False


def rule_matches_static(email: dict[str, Any], rule: MailRule) -> bool | None:
    """Evaluate a rule's static conditions against an email.

    Returns
    -------
    None
        If the rule has no conditions (deferred to AI stage).
    True
        If all (AND) or at least one (OR) condition matches.
    False
        Otherwise.
    """
    if not rule.conditions:
        return None

    if rule.combinator == "AND":
        for cond in rule.conditions:
            if not _condition_matches(email, cond):
                return False
        return True
    else:  # OR
        for cond in rule.conditions:
            if _condition_matches(email, cond):
                return True
        return False


# ---------------------------------------------------------------------------
# make_match_rules — 4-stage cascade node
# ---------------------------------------------------------------------------


def make_match_rules(ctx: NodeContext) -> Callable[[MailAgentState], MailAgentState]:
    """Factory for the match_rules node.

    Applies a 4-stage cascade to find the best rule for the latest email in
    the thread:

    1. **Thread continuity** — if any prior email in the thread has
       ``_matched_rule`` set and the named rule is active with
       ``run_on_threads=True``, re-apply it immediately.
    2. **Learned pattern** — ``lp_store.by_sender`` for the sender; if an
       active pattern is found, apply its rule.
    3. **Static conditions** — evaluate each enabled rule's conditions in
       priority order; first True wins.  Rules with empty conditions are
       deferred to Stage 4.
    4. **AI on residual** — call the LLM (``structured_call``) on rules
       that had no static conditions.  If none exist, return
       ``matched_by="none"``.

    Returns a node function that takes the current state and returns a
    partial state dict with ``matched_by`` and ``rule_name``.

    Parameters
    ----------
    ctx
        Execution context (owner_email used for prompt).

    Returns
    -------
    Callable
        A node function ``(MailAgentState) -> MailAgentState``.
    """

    def _node(state: MailAgentState) -> MailAgentState:
        thread: list[dict[str, Any]] = state.get("thread") or []
        if not thread:
            return {"matched_by": "none", "rule_name": None}

        latest = thread[-1]
        sender = _sender_email(latest)

        # ------------------------------------------------------------------
        # Stage 0 — Thread continuity
        # ------------------------------------------------------------------
        for prior in thread[:-1]:
            prior_rule_name: str | None = prior.get("_matched_rule")
            if prior_rule_name:
                prior_rule = rules_store.by_name(prior_rule_name)
                if prior_rule and prior_rule.enabled and prior_rule.run_on_threads:
                    log.debug("match_rules: thread_continuity → %r", prior_rule_name)
                    return {
                        "matched_by": "thread_continuity",
                        "rule_name": prior_rule_name,
                    }

        # ------------------------------------------------------------------
        # Stage 1 — Learned pattern
        # ------------------------------------------------------------------
        pattern = lp_store.by_sender(sender)
        if pattern is not None:
            log.debug("match_rules: learned_pattern → %r", pattern.rule_name)
            return {
                "matched_by": "learned_pattern",
                "rule_name": pattern.rule_name,
            }

        # ------------------------------------------------------------------
        # Stage 2 — Static conditions
        # ------------------------------------------------------------------
        all_rules = rules_store.list_all(enabled_only=True)
        residual: list[MailRule] = []

        for rule in all_rules:
            verdict = rule_matches_static(latest, rule)
            if verdict is True:
                log.debug("match_rules: static → %r", rule.name)
                return {"matched_by": "static", "rule_name": rule.name}
            if verdict is None:
                residual.append(rule)
            # verdict is False → skip (do not defer to AI)

        # ------------------------------------------------------------------
        # Stage 3 — AI on residual
        # ------------------------------------------------------------------
        if not residual:
            return {"matched_by": "none", "rule_name": None}

        residual_dicts: list[dict[str, Any]] = [
            {
                "name": r.name,
                "criteria": r.description,
                "enabled": r.enabled,
            }
            for r in residual
        ]

        prompt = choose_rule_prompt(
            dict(state),
            residual_dicts,
            owner_email=ctx.owner_email,
        )
        output: ChooseRuleOutput = structured_call(
            prompt,
            ChooseRuleOutput,
            hardening=Hardening.FULL,
            use_case=UseCase.MATCH_RULES_AI,
        )

        if output.rule:
            log.debug("match_rules: ai → %r", output.rule)
            return {"matched_by": "ai", "rule_name": output.rule}

        return {"matched_by": "none", "rule_name": None}

    return _node


# ---------------------------------------------------------------------------
# make_learn_pattern — fires only when matched_by == "ai"
# ---------------------------------------------------------------------------


def make_learn_pattern(ctx: NodeContext) -> Callable[[MailAgentState], MailAgentState]:
    """Factory for the learn_pattern node.

    Fires only when ``matched_by == "ai"`` (routing handled by pipeline).
    Assembles pattern history from existing records for the sender + current
    decision; only fires LLM if ``len(history) >= 3``.

    Uses ``learn_pattern_prompt`` + ``structured_call(hardening=COMPACT,
    use_case=LEARN_PATTERN)``. If ``should_learn AND confidence >=
    ACTIVATION_THRESHOLD`` → ``lp_store.record_decision(sender, rule,
    confidence_hint=out.confidence)`` and returns ``{"learned_pattern": {...}}``.

    Returns a node function that takes the current state and returns a
    partial state dict with ``learned_pattern`` key (or empty dict if no
    pattern is learned).

    Parameters
    ----------
    ctx
        Execution context (unused, included for consistency with other nodes).

    Returns
    -------
    Callable
        A node function ``(MailAgentState) -> MailAgentState``.
    """

    def _node(state: MailAgentState) -> MailAgentState:
        # Guard: skip if rule_name is None
        rule_name = state.get("rule_name")
        if rule_name is None:
            return {}

        # Guard: skip if thread is empty
        thread: list[dict[str, Any]] = state.get("thread") or []
        if not thread:
            return {}

        latest = thread[-1]

        # Guard: skip if sender missing
        try:
            sender = _sender_email(latest)
        except (KeyError, IndexError, TypeError):
            return {}

        # Build history: fetch all patterns for sender, filter in-process,
        # format each + append current decision
        all_patterns = lp_store.list_all(active_only=False)
        sender_patterns = [p for p in all_patterns if p.sender_email == sender]

        history: list[dict[str, Any]] = [
            {
                "received_at": p.last_confirmed.isoformat(),
                "subject": "",
                "rule_name": p.rule_name,
            }
            for p in sender_patterns
        ]

        # Append current decision
        history.append(
            {
                "received_at": latest.get("receivedAt", ""),
                "subject": latest.get("subject", ""),
                "rule_name": rule_name,
            }
        )

        # Threshold gate: count total decisions (evidence_count from patterns + current)
        # If < 3 → no LLM call
        total_decisions = sum(p.evidence_count for p in sender_patterns) + 1
        if total_decisions < 3:
            return {}

        # LLM call
        prompt = learn_pattern_prompt(sender_email=sender, recent_history=history)
        output: LearnPatternOutput = structured_call(
            prompt,
            LearnPatternOutput,
            hardening=Hardening.COMPACT,
            use_case=UseCase.LEARN_PATTERN,
        )

        # Persist only if should_learn AND confidence >= ACTIVATION_THRESHOLD
        if output.should_learn and output.confidence >= float(
            lp_store.ACTIVATION_THRESHOLD
        ):
            p = lp_store.record_decision(
                sender,
                rule_name,
                confidence_hint=output.confidence,
            )
            log.debug(
                "learn_pattern: recorded pattern for %s → %s (confidence=%.2f)",
                sender,
                rule_name,
                p.confidence,
            )
            return {
                "learned_pattern": {
                    "sender": sender,
                    "rule": rule_name,
                    "confidence": float(p.confidence),
                }
            }

        return {}

    return _node


# ---------------------------------------------------------------------------
# make_apply_actions — dispatches side-effects for matched rule's action list
# ---------------------------------------------------------------------------


def make_apply_actions(ctx: NodeContext) -> Callable[[MailAgentState], MailAgentState]:
    """Factory for the apply_actions node.

    Reads ``rule_name`` and ``thread`` from state. Fetches the rule from the
    store; skips silently if not found or disabled.

    For each action in ``rule.actions`` dispatches the corresponding
    side-effect:

    - ``"archive"`` → ``ctx.mail.archive(email_id)``
    - ``"mark_read"`` → ``ctx.mail.mark_read(email_id)``
    - ``"label:<name>"`` → ``ctx.mail.label(email_id, name)``
    - ``"notify"`` → ``ctx.base.mission_emitter.emit(...)``
    - ``"delegate_to_atlas"`` → ``ctx.base.delegation.delegate(...)``
    - ``"draft_reply"`` → marker only (T23 handles actual save)
    - unknown → ``log.warning`` only, not appended to result

    Returns a node function that takes the current state and returns a
    partial state dict with ``actions_applied`` (list of executed action
    strings).

    Parameters
    ----------
    ctx
        Execution context with mail adapter and base sentinel context.

    Returns
    -------
    Callable
        A node function ``(MailAgentState) -> MailAgentState``.
    """

    def _node(state: MailAgentState) -> MailAgentState:
        rule_name: str | None = state.get("rule_name")
        thread: list[dict[str, Any]] = state.get("thread") or []

        # Skip conditions: rule_name missing OR thread empty
        if not rule_name or not thread:
            return {"actions_applied": []}

        # Fetch rule from store
        rule = rules_store.by_name(rule_name)
        if rule is None or not rule.enabled:
            return {"actions_applied": []}

        latest = thread[-1]
        email_id: str = latest.get("id", "")
        subject: str = latest.get("subject", "(no subject)")

        actions_applied: list[str] = []

        for action in rule.actions:
            if action == "archive":
                ctx.mail.archive(email_id)
                actions_applied.append("archive")

            elif action == "mark_read":
                ctx.mail.mark_read(email_id)
                actions_applied.append("mark_read")

            elif action.startswith("label:"):
                _, label_name = action.split(":", 1)
                ctx.mail.label(email_id, label_name)
                actions_applied.append(action)

            elif action == "notify":
                ctx.base.mission_emitter.emit(
                    intent_text=f"Mail: {subject}",
                    reason=f"rule '{rule_name}' matched — please review",
                    artifact={
                        "kind": "sentinel_evidence",
                        "sentinel": "mail",
                        "evidence": {
                            "email_id": email_id,
                            "rule": rule_name,
                        },
                    },
                )
                actions_applied.append("notify")

            elif action == "delegate_to_atlas":
                ctx.base.delegation.delegate(
                    intent_text=f"Handle mail: {subject}",
                    artifact={"email_id": email_id, "rule": rule_name},
                    timeout_s=60.0,
                )
                actions_applied.append("delegate_to_atlas")

            elif action == "draft_reply":
                # Marker only — T23 handles the actual draft save
                actions_applied.append("draft_reply")

            else:
                log.warning(
                    "apply_actions: unknown action %r in rule %r — skipping",
                    action,
                    rule_name,
                )

        return {"actions_applied": actions_applied}

    return _node


def make_thread_status(ctx: NodeContext) -> Callable[[MailAgentState], MailAgentState]:
    """Factory for the thread_status node.

    4-way classifier for email thread status: TO_REPLY, ACTIONED, FYI, AWAITING_REPLY.
    Uses DEFAULT tier LLM with COMPACT hardening.

    If the thread is empty, returns ``{"status": ThreadStatus.FYI}`` without
    calling the LLM. Otherwise, calls ``structured_call(thread_status_prompt(...),
    ThreadStatusOutput, hardening=COMPACT, use_case=THREAD_STATUS)`` and returns
    ``{"status": output.status}``.

    Returns a node function that takes the current state and returns a
    partial state dict with ``status`` key.

    Parameters
    ----------
    ctx
        Execution context with owner_email.

    Returns
    -------
    Callable
        A node function ``(MailAgentState) -> MailAgentState``.
    """

    def _node(state: MailAgentState) -> MailAgentState:
        thread: list[dict[str, Any]] = state.get("thread") or []

        # Empty thread → return FYI without LLM call
        if not thread:
            return {"status": ThreadStatus.FYI}

        # Call LLM to classify
        prompt = thread_status_prompt(dict(state), owner_email=ctx.owner_email)
        output: ThreadStatusOutput = structured_call(
            prompt,
            ThreadStatusOutput,
            hardening=Hardening.COMPACT,
            use_case=UseCase.THREAD_STATUS,
        )

        return {"status": output.status}

    return _node


def make_select_memories(
    ctx: NodeContext,
) -> Callable[[MailAgentState], MailAgentState]:
    """Factory for the select_memories node.

    Two-stage pipeline: candidate_pool → LLM narrow.

    1. If thread is empty, return ``{"memory_ids": []}`` without DB/LLM calls.
    2. Extract sender email from latest email in thread.
    3. Read config: ``pool_size = ctx.base.sentinel_row.config_values.get("memory_candidate_pool", 100)``
       and ``max_inject = ctx.base.sentinel_row.config_values.get("memory_inject_max", 16)``.
    4. Query ``mem_store.candidate_pool(sender, limit=pool_size)`` for non-expired memories.
    5. If pool is empty, return ``{"memory_ids": []}`` without LLM call.
    6. Otherwise call LLM with ``select_memories_prompt(state, pool_dict_list)``
       → ``SelectMemoriesOutput`` with ``hardening=COMPACT, use_case=SELECT_MEMORIES``.
    7. Return ``{"memory_ids": out.memory_ids[:max_inject]}``.

    Returns a node function that takes the current state and returns a
    partial state dict with ``memory_ids`` key.

    Parameters
    ----------
    ctx
        Execution context (base.sentinel_row.config_values, mail adapter).

    Returns
    -------
    Callable
        A node function ``(MailAgentState) -> MailAgentState``.
    """

    def _node(state: MailAgentState) -> MailAgentState:
        thread: list[dict[str, Any]] = state.get("thread") or []

        # Stage 0: Empty thread → return early
        if not thread:
            return {"memory_ids": []}

        latest = thread[-1]

        # Stage 1: Extract sender
        try:
            sender = _sender_email(latest)
        except (KeyError, IndexError, TypeError):
            return {"memory_ids": []}

        # Stage 2: Read config
        pool_size = ctx.base.sentinel_row.config_values.get(
            "memory_candidate_pool", 100
        )
        max_inject = ctx.base.sentinel_row.config_values.get("memory_inject_max", 16)

        # Stage 3: Query candidate pool
        pool = mem_store.candidate_pool(sender, limit=pool_size)

        # Stage 4: Empty pool → return early
        if not pool:
            return {"memory_ids": []}

        # Stage 5: Build pool dicts for LLM
        pool_dicts = [
            {
                "id": str(m.id),
                "kind": m.kind,
                "scope": m.scope,
                "scope_value": m.scope_value,
                "content": m.content,
            }
            for m in pool
        ]

        # Stage 6: Call LLM
        prompt = select_memories_prompt(dict(state), pool_dicts)
        output: SelectMemoriesOutput = structured_call(
            prompt,
            SelectMemoriesOutput,
            hardening=Hardening.COMPACT,
            use_case=UseCase.SELECT_MEMORIES,
        )

        # Stage 7: Return bounded memory ids
        return {"memory_ids": output.memory_ids[:max_inject]}

    return _node


def _build_reply_quote(latest: dict[str, Any], language: str) -> str:
    """Format the original message as a quoted block for the reply body.

    Follows the widely-accepted mail-client convention: an attribution line
    (``On <date>, <sender> wrote:``) followed by each line of the original
    body prefixed with ``> ``. Localised to French when ``language == "fr"``.
    Empty body → returns the attribution line only.
    """
    from_list: list[dict[str, str]] = latest.get("from") or []
    sender_display = ""
    if from_list:
        sender_display = str(from_list[0].get("name") or "").strip() or str(
            from_list[0].get("email") or ""
        )
    received = str(latest.get("receivedAt") or "").split("T")[0]
    if language.lower().startswith("fr"):
        attribution = (
            f"Le {received}, {sender_display} a écrit :"
            if received
            else f"{sender_display} a écrit :"
        )
    else:
        attribution = (
            f"On {received}, {sender_display} wrote:"
            if received
            else f"{sender_display} wrote:"
        )

    # Extract the plain-text body from bodyValues.
    body_text = ""
    body_values = latest.get("bodyValues") or {}
    text_body = latest.get("textBody") or []
    if text_body and isinstance(text_body, list):
        first_part_id = str(text_body[0].get("partId") or "")
        part = body_values.get(first_part_id) or {}
        body_text = str(part.get("value") or "")
    # Fallback: any first value we can find.
    if not body_text and body_values:
        first = next(iter(body_values.values()))
        body_text = str((first or {}).get("value") or "")
    # Last-ditch fallback: preview.
    if not body_text:
        body_text = str(latest.get("preview") or "")

    if not body_text.strip():
        return attribution
    quoted_lines = [f"> {ln}" if ln else ">" for ln in body_text.splitlines()]
    return attribution + "\n" + "\n".join(quoted_lines)


def make_draft_reply(ctx: NodeContext) -> Callable[[MailAgentState], MailAgentState]:
    """Factory for the draft_reply node.

    Generates a draft reply to the latest email using LLM, injecting selected
    memories when available. Saves the draft and emits a mission with evidence.

    Empty thread → returns ``{}`` (no-op, no LLM, no emit).

    Workflow:
    1. Guard: skip if thread is empty.
    2. Fetch memories by id from ``state["memory_ids"]`` (optional; defaults to []).
    3. Build memory dicts for LLM injection: ``[{"kind", "scope", "scope_value", "content"}, ...]``
    4. Call ``structured_call(draft_reply_prompt(...), DraftReplyOutput, ...)``
    5. Save draft via ``ctx.mail.save_draft(in_reply_to=latest["id"], ...)``
    6. Emit mission with ``sentinel_evidence`` artifact containing email_id, draft_id, etc.
    7. Return ``{"draft": out.body, "draft_language": out.language}``.

    Returns a node function that takes the current state and returns a
    partial state dict with ``draft`` and ``draft_language`` keys (or empty dict
    on empty thread).

    Parameters
    ----------
    ctx
        Execution context with mail adapter and base sentinel context.

    Returns
    -------
    Callable
        A node function ``(MailAgentState) -> MailAgentState``.
    """

    def _node(state: MailAgentState) -> MailAgentState:
        thread: list[dict[str, Any]] = state.get("thread") or []

        # Guard: empty thread → no-op
        if not thread:
            return {}

        latest = thread[-1]

        # Fetch memories if memory_ids provided
        memory_ids = state.get("memory_ids") or []
        memories = mem_store.get_many(memory_ids) if memory_ids else []

        # Build memory dicts for LLM
        memories_dicts = [
            {
                "kind": m.kind,
                "scope": m.scope,
                "scope_value": m.scope_value,
                "content": m.content,
            }
            for m in memories
        ]

        # Call LLM to generate draft
        prompt = draft_reply_prompt(
            dict(state),
            memories=memories_dicts,
            owner_email=ctx.owner_email,
        )
        out: DraftReplyOutput = structured_call(
            prompt,
            DraftReplyOutput,
            hardening=Hardening.FULL,
            use_case=UseCase.DRAFT_REPLY,
        )

        # --- Compute reply envelope from the message being replied to ---
        # Reply-To wins over From per RFC 5322; falls back to From otherwise.
        reply_target: list[dict[str, str]] = (
            latest.get("replyTo") or latest.get("from") or []
        )
        # Reply-all CC = original To + Cc minus (owner + reply_target duplicates).
        # Standard mail-client behaviour: keep every recipient in the loop.
        owner_email_lc = (ctx.owner_email or "").lower()
        reply_target_lc = {str(a.get("email", "")).lower() for a in reply_target}
        cc_addr: list[dict[str, str]] = []
        seen_cc: set[str] = set()
        for src_field in ("to", "cc"):
            for addr in latest.get(src_field) or []:
                email_lc = str(addr.get("email", "")).lower()
                if not email_lc or email_lc == owner_email_lc:
                    continue
                if email_lc in reply_target_lc or email_lc in seen_cc:
                    continue
                seen_cc.add(email_lc)
                cc_addr.append(addr)
        # RFC 5322 subject convention: "Re: <subject>" unless already prefixed.
        original_subject = str(latest.get("subject") or "").strip()
        reply_subject = (
            original_subject
            if original_subject.lower().startswith(("re:", "re :"))
            else f"Re: {original_subject}"
        )
        # In-Reply-To + References must be the RFC 5322 Message-Id, NOT the
        # JMAP email id. JMAP exposes it via the ``messageId`` property or the
        # ``Message-ID`` header. We check both.
        message_id_list: list[str] = latest.get("messageId") or []
        original_message_id: str = ""
        if message_id_list:
            original_message_id = str(message_id_list[0])
        else:
            for h in latest.get("headers") or []:
                if str(h.get("name", "")).lower() == "message-id":
                    original_message_id = str(h.get("value", "")).strip("<> ")
                    break
        # References = existing References + Message-Id of parent (RFC 5322 §3.6.4)
        prior_refs: list[str] = []
        for h in latest.get("headers") or []:
            if str(h.get("name", "")).lower() == "references":
                # Space-separated <id> tokens per RFC 5322
                prior_refs = [
                    tok.strip("<> ")
                    for tok in str(h.get("value", "")).split()
                    if tok.strip("<> ")
                ]
                break
        references = prior_refs + ([original_message_id] if original_message_id else [])
        # From = the owner's identity. Prefer the configured display name;
        # fall back to the local-part of the email.
        owner_display = settings.twaky_owner_name or (
            ctx.owner_email.split("@")[0] if ctx.owner_email else ""
        )
        from_addr = (
            [{"name": owner_display, "email": ctx.owner_email}]
            if ctx.owner_email
            else []
        )

        # --- Compose the final body: LLM reply + signature + quoted original ---
        # Post-append the configured signature — the LLM is instructed NOT to
        # invent one but often does anyway; overriding here ensures the real
        # signature (title, phone, legal notice) is always present.
        final_body = out.body.rstrip()
        signature = (settings.mail_sentinel_signature or "").strip()
        if signature:
            final_body = f"{final_body}\n\n{signature}"
        # Quote the original message underneath (standard mail-client behaviour).
        quoted = _build_reply_quote(latest, out.language)
        if quoted:
            final_body = f"{final_body}\n\n{quoted}"

        # Save draft with the computed envelope + composed body
        draft_id = ctx.mail.save_draft(
            in_reply_to=original_message_id or latest.get("id", ""),
            body=final_body,
            language=out.language,
            from_addr=from_addr,
            to_addr=reply_target,
            cc_addr=cc_addr,
            subject=reply_subject,
            references=references,
        )

        # Emit mission with evidence
        ctx.base.mission_emitter.emit(
            intent_text=f"Draft ready: {latest.get('subject', '(no subject)')}",
            reason=f"rule '{state.get('rule_name') or 'ai'}' matched; draft awaiting review",
            artifact={
                "kind": "sentinel_evidence",
                "sentinel": "mail",
                "evidence": {
                    "email_id": latest.get("id"),
                    "draft_id": draft_id,
                    "language": out.language,
                    "rule": state.get("rule_name"),
                    "matched_by": state.get("matched_by"),
                },
                "hints": {
                    "draft_body": out.body,
                },
            },
        )

        return {"draft": out.body, "draft_language": out.language}

    return _node


# ---------------------------------------------------------------------------
# make_spam_triage — 5-stage rspamd-first spam filter node (SP6c T6)
# ---------------------------------------------------------------------------

# Heuristic score thresholds
_HEURISTIC_NEWSLETTER_MAX_SCORE = 5
_HEURISTIC_GREY_MIN_SCORE = 4

# Regex to parse rspamd action from org.apache.james.rspamd.status header.
# James emits "actions=<action>" (plural). Accept both spellings to stay
# robust across upstream deployments. Stop at whitespace-followed-by-key=
# so "actions=no action score=X" returns "no action", not "no action score".
_RSPAMD_ACTION_RE = re.compile(r"actions?=([\w\s]+?)(?:;|\s+\w+=|$)", re.IGNORECASE)

# Numeric score component of the same header, e.g. "score=5.47559".
_RSPAMD_SCORE_RE = re.compile(r"score=(-?[\d.]+)", re.IGNORECASE)


def _parse_rspamd_status(headers: list[dict[str, Any]]) -> str | None:
    """Parse the rspamd action from org.apache.james.rspamd.status header.

    Returns the action string (lowercased, stripped) or None if the header
    is absent or the action component is missing.
    """
    for h in headers:
        if h.get("name", "").lower() == "org.apache.james.rspamd.status":
            m = _RSPAMD_ACTION_RE.search(h.get("value", ""))
            if m:
                return m.group(1).strip().lower()
    return None


def _parse_rspamd_score(headers: list[dict[str, Any]]) -> float | None:
    """Parse the numeric score from org.apache.james.rspamd.status header.

    Returns the float score or None if the header is absent or unparseable.
    """
    for h in headers:
        if h.get("name", "").lower() == "org.apache.james.rspamd.status":
            m = _RSPAMD_SCORE_RE.search(h.get("value", ""))
            if m:
                try:
                    return float(m.group(1))
                except ValueError:
                    return None
    return None


# rspamd score threshold above which we force LLM grey-zone even when
# James set ``nonjunk`` (because James's requiredScore is often set very
# high — 12+ — so ``nonjunk`` alone is a weak "not-spam" signal).
_RSPAMD_SCORE_GREY_MIN = 3.0


# ---------------------------------------------------------------------------
# Brand-impersonation detection
# ---------------------------------------------------------------------------
# Brands frequently impersonated in phishing. Each key maps to a tuple of
# domain SUFFIXES that legitimately send on behalf of that brand. Any
# email that mentions the brand (in subject/from-name/preview) but is NOT
# sent from one of those suffixes is a strong impersonation signal.
#
# Suffixes are matched with `.endswith("." + suffix) or == suffix` so that
# sub-domains ``updates.paypal.com`` correctly count as ``paypal.com``.
_BRAND_LEGIT_DOMAINS: dict[str, tuple[str, ...]] = {
    # Payments
    "paypal": ("paypal.com", "paypal.fr", "paypalcorp.com"),
    "stripe": ("stripe.com",),
    "revolut": ("revolut.com",),
    # French banks
    "bnp": ("bnpparibas.com", "bnpparibas.fr", "bnpparibas.net"),
    "societe generale": ("societegenerale.fr", "societegenerale.com"),
    "credit agricole": ("credit-agricole.fr", "ca-technologies.fr"),
    "boursorama": ("boursorama.com", "boursobank.com"),
    "banque postale": ("labanquepostale.fr",),
    "hsbc": ("hsbc.com", "hsbc.fr"),
    "lcl": ("lcl.fr", "lcl.com"),
    # Retail / big tech
    "amazon": (
        "amazon.com",
        "amazon.fr",
        "amazon.co.uk",
        "amazon.de",
        "amazon.it",
        "amazon.es",
        "amazon.ca",
        "amazon.co.jp",
        "email.amazon.com",
    ),
    "apple": ("apple.com", "email.apple.com", "insideapple.apple.com"),
    "microsoft": (
        "microsoft.com",
        "email.microsoftemail.com",
        "accountprotection.microsoft.com",
        "microsoftonline.com",
        "office.com",
        "office365.com",
    ),
    "google": ("google.com", "gmail.com", "accounts.google.com", "youtube.com"),
    "netflix": ("netflix.com",),
    "dropbox": ("dropbox.com", "dropboxmail.com"),
    "meta": ("meta.com", "facebook.com", "facebookmail.com", "instagram.com"),
    "facebook": ("facebook.com", "facebookmail.com"),
    "linkedin": ("linkedin.com",),
    # French telecom
    "orange": ("orange.fr", "orange.com"),
    "sfr": ("sfr.fr", "sfr.com"),
    "free": ("free.fr", "free-mobile.fr"),
    "bouygues": ("bouyguestelecom.fr",),
    # Delivery / parcel
    "colissimo": ("colissimo.fr", "laposte.fr"),
    "chronopost": ("chronopost.fr",),
    "dhl": ("dhl.com", "dhl.fr"),
    "fedex": ("fedex.com",),
    "ups": ("ups.com",),
    "la poste": ("laposte.fr", "laposte.net"),
}

# Urgency + credential-harvest phrases in FR + EN, ordered by frequency
# observed in phishing samples.
_URGENCY_PATTERNS = (
    # French
    "sera fermé",
    "sera fermée",
    "sera suspendu",
    "sera bloqué",
    "sera bloquée",
    "vérifier votre compte",
    "vérifier vos informations",
    "confirmer votre compte",
    "confirmer vos informations",
    "mise à jour de sécurité",
    "action requise",
    "dernière chance",
    "expire aujourd",
    "attention requise",
    "compte inactif",
    "identifiants expirés",
    # English
    "will be closed",
    "will be suspended",
    "will be blocked",
    "verify your account",
    "verify your information",
    "confirm your account",
    "confirm your information",
    "security update",
    "action required",
    "final notice",
    "expires today",
    "unusual activity",
    "unauthorized access",
    # Delivery scams
    "delivery failed",
    "livraison échouée",
    "notification de livraison",
    "parcel status",
    "colis en attente",
    "colis bloqué",
)


def _brand_impersonation_signal(email: dict[str, Any]) -> tuple[bool, str]:
    """Detect brand-impersonation signals in an email.

    Returns (fires, reason). ``fires`` is True when ONE of:
    - A known brand is mentioned (subject / from-name / preview) but the
      sender domain is NOT in the brand's legitimate suffix list.
    - A known brand is mentioned AND the subject contains one of the
      urgency / credential-harvest phrases (regardless of whether the
      sender domain matches — real companies rarely combine both).

    ``reason`` is a short string suitable for the ``mail_sentinel_spam_decision``
    reason column, e.g. ``"brand=paypal + urgency 'sera fermé'"``.
    """
    from_list = email.get("from") or []
    from_email = from_list[0].get("email", "").lower() if from_list else ""
    from_name = from_list[0].get("name", "").lower() if from_list else ""
    from_domain = from_email.split("@")[-1] if "@" in from_email else ""
    subject = (email.get("subject") or "").lower()
    preview = (email.get("preview") or "").lower()

    haystack = f"{from_name}\n{subject}\n{preview}"

    for brand, legit_suffixes in _BRAND_LEGIT_DOMAINS.items():
        if brand not in haystack:
            continue
        domain_ok = any(
            from_domain == suf or from_domain.endswith("." + suf)
            for suf in legit_suffixes
        )
        urgency_hit = next(
            (p for p in _URGENCY_PATTERNS if p in subject or p in preview),
            None,
        )
        if not domain_ok:
            return True, f"brand={brand} + domain mismatch ({from_domain!r})"
        if urgency_hit:
            return True, f"brand={brand} + urgency {urgency_hit!r}"
    return False, ""


@dataclass
class _HeuristicResult:
    """Result of the header-based heuristic scoring."""

    total_score: int
    newsletter_signal: bool
    summary: dict[str, Any] = field(default_factory=dict)


# TLDs heavily abused by cold-outreach + spam. Whitelist by exception if
# a legitimate business ever uses one.
_SUSPICIOUS_TLDS = frozenset(
    {
        ".click",
        ".buzz",
        ".homes",
        ".online",
        ".top",
        ".xyz",
        ".deals",
        ".info",
        ".tk",
        ".ml",
        ".ga",
        ".cf",
    }
)

# Match runs of 3+ consecutive consonants — real English/French words
# occasionally hit 3 (e.g. "STRing", "SCReen") but rarely alongside a
# low overall vowel density; bot aliases pair both.
_CONSONANT_CLUSTER_RE = re.compile(r"[bcdfghjklmnpqrstvwxz]{3,}", re.IGNORECASE)


def _is_random_local_part(local: str) -> bool:
    """True when the local part looks bot-generated.

    Criteria (all must hold):
    - 5–10 alpha characters (no digits, dots, dashes, underscores).
    - Vowel ratio ≤ 30% (real words like ``support`` 28%, ``michel`` 33%,
      cluster around here).
    - Contains a 3+ consecutive consonant cluster (rules out short
      low-vowel words like ``bytes`` that lack a real cluster).

    Catches bulk-mail bot patterns from 2026-08-13 UAT (``oltiwbr``,
    ``eclybnm``, ``ecbarmd``). Rejects ``support``, ``michel``, common
    English/French names, and dotted / digit-containing local parts.
    """
    if len(local) < 5 or len(local) > 10:
        return False
    if not local.isalpha():
        return False
    ll = local.lower()
    vowels = sum(1 for c in ll if c in "aeiouy")
    ratio_low = vowels / len(ll) <= 0.30
    has_cluster = bool(_CONSONANT_CLUSTER_RE.search(ll))
    return ratio_low and has_cluster


def _has_suspicious_tld(domain: str) -> bool:
    """True when the sender domain ends with a TLD abused by cold outreach."""
    d = domain.lower()
    return any(d.endswith(t) for t in _SUSPICIOUS_TLDS)


def _header_heuristic_score(email: dict[str, Any]) -> _HeuristicResult:
    """Compute a small integer heuristic score from email headers.

    Score contributions:
      +2  if list-unsubscribe header present (SP6c required list-unsub-post
          too; loosened 2026-08-13 after seeing legit newsletters send only
          the single header — was missing ~20 newsletters per 256 mails)
      +3  if dkim-signature absent
      +3  if return-path domain != from domain (sender mismatch)
      +2  if hasAttachment AND dkim absent
      +2  if sender TLD in _SUSPICIOUS_TLDS (.click, .buzz, .homes, …)
      +2  if sender local part looks random-generated (bot-style)

    ``newsletter_signal`` is set when list-unsubscribe is present (any form).
    """
    headers: list[dict[str, Any]] = email.get("headers") or []
    # Build a lower-cased header-name → value dict (last value wins on dup)
    h: dict[str, str] = {}
    for hdr in headers:
        name = hdr.get("name", "").lower()
        if name:
            h[name] = hdr.get("value", "")

    # Loosened 2026-08-13: single list-unsubscribe header is enough to
    # mark as newsletter (spec §5.3 required BOTH which missed ~20 legit
    # newsletters per 256-mail sample). The rule handler still gets to
    # decide the action.
    list_unsub_present = "list-unsubscribe" in h
    list_unsub_post_present = "list-unsubscribe-post" in h
    dkim_present = "dkim-signature" in h
    has_attachment = bool(email.get("hasAttachment", False))

    # Extract from domain + local
    from_list = email.get("from") or []
    from_email = from_list[0].get("email", "") if from_list else ""
    from_local = from_email.split("@")[0].lower() if "@" in from_email else ""
    from_domain = from_email.split("@")[-1].lower() if "@" in from_email else ""

    # Extract return-path domain
    return_path_val = h.get("return-path", "")
    rp_email = return_path_val.strip("<>").strip()
    rp_domain = rp_email.split("@")[-1].lower() if "@" in rp_email else ""
    return_path_mismatch = bool(from_domain and rp_domain and from_domain != rp_domain)

    suspicious_tld = _has_suspicious_tld(from_domain)
    random_local = _is_random_local_part(from_local)

    score = 0
    if list_unsub_present:
        score += 2
    if not dkim_present:
        score += 3
    if return_path_mismatch:
        score += 3
    if has_attachment and not dkim_present:
        score += 2
    if suspicious_tld:
        score += 2
    if random_local:
        score += 2

    summary: dict[str, Any] = {
        "list_unsubscribe": list_unsub_present,
        "list_unsubscribe_post": list_unsub_post_present,
        "dkim_present": dkim_present,
        "return_path_mismatch": return_path_mismatch,
        "has_attachment": has_attachment,
        "suspicious_tld": suspicious_tld,
        "random_local": random_local,
        "total_score": score,
    }

    return _HeuristicResult(
        total_score=score,
        newsletter_signal=list_unsub_present,
        summary=summary,
    )


def _parse_iso(value: str | None) -> datetime:
    """Parse an ISO 8601 string to a timezone-aware datetime.

    Falls back to now(UTC) if the value is absent or unparseable.
    Python 3.11+ natively parses the ``Z`` suffix so we do not replace it.
    """
    if not value:
        return datetime.now(UTC)
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, AttributeError):
        return datetime.now(UTC)


_ENVELOPE_HEADER_KEYS = frozenset(
    [
        "from",
        "to",
        "subject",
        "list-unsubscribe",
        "list-id",
        "authentication-results",
    ]
)


def _extract_envelope_headers(email: dict[str, Any]) -> dict[str, str]:
    """Extract a whitelisted subset of headers from the JMAP email dict.

    The JMAP ``headers`` property is a list of ``{"name": ..., "value": ...}``
    dicts. We collect only the keys in ``_ENVELOPE_HEADER_KEYS`` (lower-cased),
    taking the first occurrence of each and copying the raw string value.
    """
    result: dict[str, str] = {}
    for h in email.get("headers") or []:
        name = h.get("name", "").lower()
        if name in _ENVELOPE_HEADER_KEYS and name not in result:
            result[name] = str(h.get("value") or "")
    return result


def _terminate(
    ctx: NodeContext,
    email: dict[str, Any],
    *,
    bucket: str,
    signal: str,
    score: float | None,
    reason: str,
) -> MailAgentState:
    """Apply adapter side-effects + persist spam decision + emit mission (phishing only).

    Per spec §5.3:
    - spam/phishing-alert: label __spam__ + set_keyword $junk=True
    - newsletter: label newsletter + set_keyword nonjunk=True
    - phishing-alert only: emit mission via mission_emitter

    For spam/phishing-alert also captures provenance (origin mailbox + envelope
    headers) before the Junk move and passes them to the store.

    Returns the state dict triggering pipeline routing (END for spam/phishing-alert,
    continue for newsletter).
    """
    email_id: str = email["id"]

    # ------------------------------------------------------------------
    # Provenance capture for spam / phishing-alert (SP6d T1 D2)
    # Must happen BEFORE the Junk move so mailboxIds still show origin.
    # ------------------------------------------------------------------
    origin_mailbox_id: str | None = None
    origin_mailbox_role: str | None = None
    envelope_headers: dict[str, str] | None = None

    if bucket in {"spam", "phishing-alert"}:
        # Determine origin mailbox (first mailboxId that is NOT Junk).
        try:
            junk_resolver = getattr(ctx.mail, "resolve_role_mailbox_id", None)
            junk_id_for_provenance: str | None = (
                junk_resolver("junk") if callable(junk_resolver) else None
            )
        except Exception:  # noqa: BLE001
            junk_id_for_provenance = None

        current_mbox_ids: dict[str, bool] = email.get("mailboxIds") or {}
        for mid in current_mbox_ids:
            if mid != junk_id_for_provenance:
                origin_mailbox_id = mid
                break
        # If all mailboxIds == junk (unusual), origin_mailbox_id remains None.

        # Resolve origin mailbox role via the new inverse helper.
        if origin_mailbox_id is not None:
            try:
                role_resolver = getattr(ctx.mail, "resolve_mailbox_role_by_id", None)
                origin_mailbox_role = (
                    role_resolver(origin_mailbox_id)
                    if callable(role_resolver)
                    else None
                )
            except Exception:  # noqa: BLE001
                origin_mailbox_role = None

        # Extract whitelisted envelope headers from the already-fetched email.
        envelope_headers = _extract_envelope_headers(email) or None

    if bucket in {"spam", "phishing-alert"}:
        # Label + $junk keyword + move to the Junk mailbox (RFC 8621 role="junk").
        # Bundle mailbox move with keywords in ONE Email/set for atomicity —
        # otherwise a partial success could leave the mail labeled but still
        # in INBOX, confusing both the user and any downstream filter.
        # Restore (mail_sentinel_spam.py) uses the same generic
        # ``resolve_role_mailbox_id`` helper to bring the mail back to INBOX.
        ctx.mail.label(email_id, "__spam__")
        actions_applied = ["label:__spam__", "keyword:$junk"]
        junk_move_ok = False
        try:
            resolver = getattr(ctx.mail, "resolve_role_mailbox_id", None)
            junk_id = resolver("junk") if callable(resolver) else None
            if junk_id:
                # Fetch current mailboxIds to know what to unset (typically INBOX).
                current_email = ctx.mail.get_email(email_id)
                current_mboxes: dict[str, bool] = current_email.get("mailboxIds") or {}
                mbox_patches: dict[str, bool] = {
                    mid: False for mid in current_mboxes if mid != junk_id
                }
                mbox_patches[junk_id] = True
                ctx.mail.set_keywords_bulk(
                    email_id,
                    {"$junk": True},
                    mailbox_patches=mbox_patches,
                )
                actions_applied.append(f"move:junk({junk_id})")
                junk_move_ok = True
        except Exception:
            log.exception(
                "spam_triage: failed to move email=%s to Junk mailbox — "
                "falling back to $junk keyword only",
                email_id,
            )
        if not junk_move_ok:
            # Fallback: at least set the $junk keyword so client-side filters
            # (Twake Mail, Thunderbird) can still route the mail.
            ctx.mail.set_keyword(email_id, "$junk", True)
    else:  # newsletter
        ctx.mail.label(email_id, "newsletter")
        ctx.mail.set_keyword(email_id, "nonjunk", True)
        actions_applied = ["label:newsletter", "keyword:nonjunk"]

    decision_id: UUID = spam_decisions.insert(
        email_id=email_id,
        thread_id=email.get("threadId"),
        sender_email=_sender_email(email),
        subject=email.get("subject", ""),
        received_at=_parse_iso(email.get("receivedAt")),
        bucket=bucket,
        signal_source=signal,
        score=score,
        reason=reason,
        origin_mailbox_id=origin_mailbox_id,
        origin_mailbox_role=origin_mailbox_role,
        envelope_headers=envelope_headers,
    )

    if bucket == "phishing-alert":
        preview = (email.get("preview") or "")[:500]
        ctx.base.mission_emitter.emit(
            intent_text=f"Phishing suspected: {email.get('subject', '(no subject)')}",
            reason="phishing-alert bucket auto-archived by spam_triage",
            artifact={
                "kind": "phishing_alert",
                "evidence": {
                    "email_id": email_id,
                    "sender": _sender_email(email),
                    "reason": reason,
                    "score": score,
                    "spam_decision_id": str(decision_id),
                },
                "hints": {"body_preview": preview},
            },
        )

    # Per spec §5.3: newsletter node returns only spam_bucket + spam_decision_id
    # (pipeline continues; actions_applied would conflict with downstream nodes).
    # Terminal buckets (spam, phishing-alert) include actions_applied to surface
    # what the sentinel did (pipeline ends, no downstream node overwrites this).
    if bucket == "newsletter":
        return {
            "spam_bucket": bucket,
            "spam_decision_id": decision_id,
        }
    return {
        "spam_bucket": bucket,
        "spam_decision_id": decision_id,
        "actions_applied": actions_applied,
    }


def make_spam_triage(ctx: NodeContext) -> Callable[[MailAgentState], MailAgentState]:
    """Factory for the spam_triage node.

    Implements a 5-stage first-match-wins cascade to classify incoming mail
    into one of three buckets (spam, phishing-alert, newsletter) or pass it
    through (bucket=None).

    Stage 1 — Trust upstream rspamd via JMAP keywords ($junk / nonjunk).
    Stage 2 — Trust upstream rspamd via org.apache.james.rspamd.status header.
    Stage 3 — Header heuristics (list-unsubscribe, DKIM, return-path mismatch).
    Stage 4 — LLM grey-zone check (only if grey_zone=True from stages 2–3).
    Stage 5 — Default pass-through.

    Gate check (spec §5.5): if spam_filter_enabled=False in config_values,
    returns {"spam_bucket": None} immediately — zero cost.

    Parameters
    ----------
    ctx
        Execution context with mail adapter, base context, and owner_email.

    Returns
    -------
    Callable
        A node function ``(MailAgentState) -> MailAgentState``.
    """

    def _node(state: MailAgentState) -> MailAgentState:
        cfg = ctx.base.sentinel_row.config_values

        # Gate check — FIRST, before any work (spec §5.5)
        if not cfg.get("spam_filter_enabled", False):
            return {"spam_bucket": None}

        thread: list[dict[str, Any]] = state.get("thread") or []
        if not thread:
            return {"spam_bucket": None}

        latest = thread[-1]

        # ----------------------------------------------------------------
        # Stage 1 — Trust upstream rspamd $junk marker only
        # ----------------------------------------------------------------
        # $junk is either (a) a user-set spam mark or (b) rspamd's
        # unambiguous reject. Both are strong signals — terminate.
        # nonjunk is NOT trusted blindly: on James deployments with a
        # high requiredScore (12+), rspamd sets nonjunk on obvious spam
        # too (paypal phishing at score 0.0, list-mail at score 1.3),
        # so the old hard pass-through here missed everything below the
        # reject threshold. The score-aware override below (after Stage
        # 2 parses the numeric score) forces LLM grey-zone for anything
        # >= _RSPAMD_SCORE_GREY_MIN even when nonjunk is set.
        kw: dict[str, Any] = latest.get("keywords") or {}
        if kw.get("$junk"):
            return _terminate(
                ctx,
                latest,
                bucket="spam",
                signal="rspamd_junk_keyword",
                score=None,
                reason="upstream rspamd marked $junk",
            )

        # ----------------------------------------------------------------
        # Stage 2 — Trust upstream rspamd via org.apache.james.rspamd.status header
        # ----------------------------------------------------------------
        rspamd_headers = latest.get("headers") or []
        rspamd_action = _parse_rspamd_status(rspamd_headers)
        rspamd_score = _parse_rspamd_score(rspamd_headers)
        if rspamd_action in {"reject", "soft reject"}:
            return _terminate(
                ctx,
                latest,
                bucket="spam",
                signal="rspamd_status_reject",
                score=None,  # score column stores 0..1 LLM confidence, not raw rspamd
                reason=f"rspamd action={rspamd_action} score={rspamd_score}",
            )
        if rspamd_action == "rewrite subject":
            return _terminate(
                ctx,
                latest,
                bucket="spam",
                signal="rspamd_status_rewrite",
                score=None,
                reason=f"rspamd action=rewrite subject score={rspamd_score}",
            )
        grey_zone = rspamd_action in {"add header", "greylist"}

        # ----------------------------------------------------------------
        # Brand impersonation — computed FIRST so it can defeat the
        # nonjunk-based early return below. Real PayPal / banks /
        # carriers rarely combine brand mention with urgency phrasing;
        # if the signal fires, the LLM decides the final bucket even
        # when rspamd trusts the mail.
        # ----------------------------------------------------------------
        brand_fires, brand_reason = _brand_impersonation_signal(latest)

        # ----------------------------------------------------------------
        # Score-aware grey-zone override: even if James set nonjunk (=1),
        # a non-trivial rspamd score means the classifier saw signals
        # worth double-checking. Route to LLM. Below the threshold and
        # with nonjunk set → keep the pass-through (avoids blasting the
        # LLM budget on obvious ham like calendar invites) UNLESS brand
        # impersonation fires.
        if (
            rspamd_score is not None and rspamd_score >= _RSPAMD_SCORE_GREY_MIN
        ) or brand_fires:
            grey_zone = True
        elif kw.get("nonjunk") and (
            rspamd_score is None or rspamd_score < _RSPAMD_SCORE_GREY_MIN
        ):
            # Genuinely clean per rspamd (or no rspamd verdict AND
            # explicit nonjunk keyword — user marked it OK). Skip.
            return {"spam_bucket": None}

        # ----------------------------------------------------------------
        # Stage 3 — Header heuristics
        # ----------------------------------------------------------------
        h = _header_heuristic_score(latest)
        if h.newsletter_signal and h.total_score < _HEURISTIC_NEWSLETTER_MAX_SCORE:
            return _terminate(
                ctx,
                latest,
                bucket="newsletter",
                signal="heuristic_newsletter",
                score=None,
                reason=f"list-unsubscribe present, heuristic score={h.total_score}",
            )
        if h.total_score >= _HEURISTIC_GREY_MIN_SCORE:
            grey_zone = True

        # ----------------------------------------------------------------
        # Stage 4 — LLM grey-zone check (only if grey_zone=True)
        # ----------------------------------------------------------------
        if not grey_zone:
            return {"spam_bucket": None}

        # Build a compact headers summary string for the prompt
        headers_summary_lines = [f"{k}: {v}" for k, v in h.summary.items()]
        if brand_fires:
            headers_summary_lines.append(f"brand_impersonation: {brand_reason}")
        headers_summary = "\n".join(headers_summary_lines)

        prompt = spam_check_prompt(
            dict(state),
            headers_summary=headers_summary,
            rspamd_action=rspamd_action,
            owner_email=ctx.owner_email,
        )
        out: SpamCheckOutput = structured_call(
            prompt,
            SpamCheckOutput,
            hardening=Hardening.COMPACT,
            use_case=UseCase.SPAM_CHECK,
        )

        # Default lowered 2026-08-13 from 0.85 → 0.70 after UAT: Mistral
        # returned confidence 0.7-0.8 on borderline spam (cold outreach,
        # brand impersonation) and pipeline pass-throughed. Operator-tunable
        # via config_values.spam_llm_confidence_threshold.
        spam_thresh = float(cfg.get("spam_llm_confidence_threshold", 0.70))
        news_thresh = float(cfg.get("spam_llm_newsletter_threshold", 0.70))

        if out.bucket in {"spam", "phishing-alert"} and out.confidence >= spam_thresh:
            return _terminate(
                ctx,
                latest,
                bucket=out.bucket,
                signal="llm_grey_zone",
                score=out.confidence,
                reason=out.reason,
            )
        if out.bucket == "newsletter" and out.confidence >= news_thresh:
            return _terminate(
                ctx,
                latest,
                bucket="newsletter",
                signal="llm_grey_zone",
                score=out.confidence,
                reason=out.reason,
            )

        # Stage 5 — default pass-through
        return {"spam_bucket": None}

    return _node


__all__ = [
    "NodeContext",
    "make_apply_actions",
    "make_draft_reply",
    "make_learn_pattern",
    "make_load_thread",
    "make_match_rules",
    "make_select_memories",
    "make_spam_triage",
    "make_thread_status",
    "rule_matches_static",
]
