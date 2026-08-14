"""LangGraph pipeline assembly for the mail sentinel.

Graph shape (see spec §6.10):

    load_thread
        │
    spam_triage
        │
        ├─ bucket in {spam, phishing-alert} ──> END
        │
    match_rules ─────────────────────────────┐
        │ (matched_by == "ai")               │
    learn_pattern                            │
        │                                    │
    apply_actions ◄──────────────────────────┘
        │
    thread_status
        │
        ├─ TO_REPLY + (rule is None OR draft_reply in rule.actions) ──> select_memories ──> draft_reply
        └─ else ────────────────────────────────────────────────────────────────────────────> END
"""

from __future__ import annotations

import time
from typing import Any

from langgraph.graph import END, START, StateGraph

from twaky.sentinels.mail.nodes import (
    NodeContext,
    make_apply_actions,
    make_draft_reply,
    make_learn_pattern,
    make_load_thread,
    make_match_rules,
    make_select_memories,
    make_spam_triage,
    make_thread_status,
)
from twaky.sentinels.mail.robustness import resilient_node
from twaky.sentinels.mail.state import MailAgentState, ThreadStatus
from twaky.sentinels.mail.store import rules as rules_store


def _route_after_match_rules(state: MailAgentState) -> str:
    """SP5c: route after match_rules based on how the rule was chosen.

    - ``learned_pattern`` → straight to ``apply_actions`` (skip spam_triage).
      The pattern is trusted (evidence >= 3, confidence >= 0.9), and this
      is the whole point of the short-circuit: no LLM, no spam check on
      senders we already trust or explicitly want to block.
    - ``ai`` → ``learn_pattern`` first, then ``spam_triage``, then ``apply_actions``.
    - Everything else (static, none, thread_continuity) → ``spam_triage``,
      then ``apply_actions`` if not spam.
    """
    matched_by = state.get("matched_by")
    if matched_by == "learned_pattern":
        return "apply_actions"
    if matched_by == "ai":
        return "learn_pattern"
    return "spam_triage"


def _route_after_spam_triage_new(state: MailAgentState) -> str:
    """SP5c: after spam_triage, route to apply_actions unless spam bucket set."""
    if state.get("spam_bucket") in {"spam", "phishing-alert"}:
        return END
    return "apply_actions"


def build_graph(ctx: NodeContext):
    """Build and compile the mail-sentinel LangGraph pipeline.

    Parameters
    ----------
    ctx:
        Mail-specific node context (base sentinel context + mail adapter +
        owner email).

    Returns
    -------
    CompiledStateGraph
        A compiled LangGraph app ready for ``.invoke()``.
    """

    # NOTE: legacy inner router kept for reference — SP5c uses
    # ``_route_after_spam_triage_new`` at module scope. See ``build_graph``
    # edges below.
    def _route_after_spam_triage(state: MailAgentState) -> str:
        if state.get("spam_bucket") in {"spam", "phishing-alert"}:
            return END
        return "match_rules"

    def _route_after_status(state: MailAgentState) -> str:
        if state.get("status") is not ThreadStatus.TO_REPLY:
            return END
        name = state.get("rule_name")
        rule = rules_store.by_name(name) if name else None
        # Draft if the rule requests it, or if no rule matched but the thread
        # clearly expects a reply (AI-only path).
        if rule is None or "draft_reply" in rule.actions:
            return "select_memories"
        return END

    graph: StateGraph = StateGraph(MailAgentState)

    # Each node wrapped by ``resilient_node`` for a wall-time budget +
    # fatal-error trap — a single crashing email cannot bring down the
    # pipeline for subsequent emails. See ``robustness.py``.
    #
    # Timeouts (2026-08-13 tuning after UAT):
    #  - LLM-heavy nodes: 90s (structured_output can retry on JSON parse
    #    failures; Mistral is fast at ~1s/call but 3-5 retries + prompt
    #    tokenisation on a long thread can approach the old 30s cap).
    #  - Non-LLM nodes: 30s (default) — anything longer is a bug.
    _LLM_TIMEOUT = 90.0

    def _add(name: str, factory: Any, timeout_s: float = 30.0) -> None:
        graph.add_node(
            name,
            resilient_node(name, factory(ctx), timeout_s=timeout_s),  # type: ignore[call-overload]
        )

    _add("load_thread", make_load_thread)
    _add("spam_triage", make_spam_triage, timeout_s=_LLM_TIMEOUT)
    _add("match_rules", make_match_rules, timeout_s=_LLM_TIMEOUT)
    _add("learn_pattern", make_learn_pattern, timeout_s=_LLM_TIMEOUT)
    _add("apply_actions", make_apply_actions)
    _add("thread_status", make_thread_status, timeout_s=_LLM_TIMEOUT)
    _add("select_memories", make_select_memories, timeout_s=_LLM_TIMEOUT)
    _add("draft_reply", make_draft_reply, timeout_s=_LLM_TIMEOUT)

    # SP5c pipeline order: match_rules first. Learned patterns short-circuit
    # spam_triage entirely (trusted senders / block_sender / label:X). AI
    # matches go through learn_pattern → spam_triage → apply_actions. Static
    # / no-match go straight through spam_triage → apply_actions.
    graph.add_edge(START, "load_thread")
    graph.add_edge("load_thread", "match_rules")
    graph.add_conditional_edges(
        "match_rules",
        _route_after_match_rules,
        {
            "apply_actions": "apply_actions",
            "learn_pattern": "learn_pattern",
            "spam_triage": "spam_triage",
        },
    )
    graph.add_edge("learn_pattern", "spam_triage")
    graph.add_conditional_edges(
        "spam_triage",
        _route_after_spam_triage_new,
        {"apply_actions": "apply_actions", END: END},
    )
    graph.add_edge("apply_actions", "thread_status")
    graph.add_conditional_edges(
        "thread_status",
        _route_after_status,
        {"select_memories": "select_memories", END: END},
    )
    graph.add_edge("select_memories", "draft_reply")
    graph.add_edge("draft_reply", END)

    return graph.compile()


def process_email(ctx: NodeContext, email_id: str) -> MailAgentState:
    """Execute the graph on a single email and return the final state.

    Parameters
    ----------
    ctx:
        Mail-specific node context.
    email_id:
        JMAP email id to process.

    Returns
    -------
    MailAgentState
        The accumulated state after all nodes have run.
    """
    app = build_graph(ctx)
    return app.invoke({"email_id": email_id, "started_at": time.monotonic()})


__all__ = ["build_graph", "process_email"]
