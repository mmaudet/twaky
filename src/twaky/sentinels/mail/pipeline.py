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


def _route_after_match(state: MailAgentState) -> str:
    return "learn_pattern" if state.get("matched_by") == "ai" else "apply_actions"


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

    # Each node wrapped by ``resilient_node`` for a 30s wall-time budget
    # and a fatal-error trap — a single crashing email cannot bring down
    # the pipeline for subsequent emails. See ``robustness.py``.
    def _add(name: str, factory: Any) -> None:
        graph.add_node(name, resilient_node(name, factory(ctx)))  # type: ignore[call-overload]

    _add("load_thread", make_load_thread)
    _add("spam_triage", make_spam_triage)
    _add("match_rules", make_match_rules)
    _add("learn_pattern", make_learn_pattern)
    _add("apply_actions", make_apply_actions)
    _add("thread_status", make_thread_status)
    _add("select_memories", make_select_memories)
    _add("draft_reply", make_draft_reply)

    graph.add_edge(START, "load_thread")
    graph.add_edge("load_thread", "spam_triage")
    graph.add_conditional_edges(
        "spam_triage",
        _route_after_spam_triage,
        {"match_rules": "match_rules", END: END},
    )
    graph.add_conditional_edges(
        "match_rules",
        _route_after_match,
        {"learn_pattern": "learn_pattern", "apply_actions": "apply_actions"},
    )
    graph.add_edge("learn_pattern", "apply_actions")
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
