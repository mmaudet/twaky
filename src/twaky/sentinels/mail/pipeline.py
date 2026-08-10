"""LangGraph pipeline assembly for the mail sentinel.

Graph shape (see spec §6.10):

    load_thread
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

from langgraph.graph import END, START, StateGraph

from twaky.sentinels.mail.nodes import (
    NodeContext,
    make_apply_actions,
    make_draft_reply,
    make_learn_pattern,
    make_load_thread,
    make_match_rules,
    make_select_memories,
    make_thread_status,
)
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

    graph.add_node("load_thread", make_load_thread(ctx))  # type: ignore[call-overload]
    graph.add_node("match_rules", make_match_rules(ctx))  # type: ignore[call-overload]
    graph.add_node("learn_pattern", make_learn_pattern(ctx))  # type: ignore[call-overload]
    graph.add_node("apply_actions", make_apply_actions(ctx))  # type: ignore[call-overload]
    graph.add_node("thread_status", make_thread_status(ctx))  # type: ignore[call-overload]
    graph.add_node("select_memories", make_select_memories(ctx))  # type: ignore[call-overload]
    graph.add_node("draft_reply", make_draft_reply(ctx))  # type: ignore[call-overload]

    graph.add_edge(START, "load_thread")
    graph.add_edge("load_thread", "match_rules")
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
