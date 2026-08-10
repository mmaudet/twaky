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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from twaky.sentinels.mail.adapter import MailAdapter
from twaky.sentinels.mail.llm.hardening import Hardening
from twaky.sentinels.mail.llm.invoke import structured_call
from twaky.sentinels.mail.llm.tiers import UseCase
from twaky.sentinels.mail.prompts.rules import choose_rule_prompt
from twaky.sentinels.mail.schemas import ChooseRuleOutput
from twaky.sentinels.mail.state import MailAgentState
from twaky.sentinels.mail.store import learned_patterns as lp_store
from twaky.sentinels.mail.store import rules as rules_store

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


def _rule_matches_static(email: dict[str, Any], rule: MailRule) -> bool | None:
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
            verdict = _rule_matches_static(latest, rule)
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


__all__ = ["NodeContext", "make_load_thread", "make_match_rules"]
