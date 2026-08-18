"""SP5c 5.2: verify Sentinel.process emits a decision trace into ctx.trace."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from twaky.sentinels.base import Context
from twaky.sentinels.mail.sentinel import _emit_decision_trace


def _ctx() -> Context:
    return Context(
        db_pool=None,
        mission_emitter=MagicMock(),
        delegation=MagicMock(),
        sentinel_row=MagicMock(),
        logger=MagicMock(),
    )


def test_ctx_trace_default_is_empty_list():
    ctx = _ctx()
    assert ctx.trace == []


def test_trace_learned_pattern_short_circuit_flagged():
    ctx = _ctx()
    state: dict[str, Any] = {
        "thread": [{"from": [{"email": "x@y.com"}], "subject": "s"}],
        "matched_by": "learned_pattern",
        "rule_name": "block_sender",
        "bucket": "spam",
        "actions_applied": ["block_sender"],
        "status": "ThreadStatus.FYI",
    }
    _emit_decision_trace(ctx, "e1", state)

    match_entry = next(e for e in ctx.trace if e["node"] == "match_rules")
    assert match_entry["matched_by"] == "learned_pattern"
    assert match_entry["rule_name"] == "block_sender"
    assert match_entry["short_circuit"] is True
    assert match_entry["forced_bucket"] == "spam"

    # spam_triage entry MUST be absent because learned_pattern skipped it
    assert all(e["node"] != "spam_triage" for e in ctx.trace)


def test_trace_ai_matched_includes_spam_triage_entry():
    ctx = _ctx()
    state: dict[str, Any] = {
        "thread": [{"from": [{"email": "x@y.com"}], "subject": "s"}],
        "matched_by": "ai",
        "rule_name": "reply-to-all",
        "spam_bucket": None,
        "actions_applied": ["draft_reply"],
        "status": "ThreadStatus.TO_REPLY",
    }
    _emit_decision_trace(ctx, "e2", state)

    nodes = [e["node"] for e in ctx.trace]
    assert nodes == [
        "load_thread",
        "match_rules",
        "spam_triage",
        "apply_actions",
        "thread_status",
    ]


def test_trace_with_memories_and_draft():
    ctx = _ctx()
    state: dict[str, Any] = {
        "thread": [{"from": [{"email": "x@y.com"}], "subject": "s"}],
        "matched_by": "static",
        "rule_name": "reply-to-alex",
        "spam_bucket": None,
        "actions_applied": ["draft_reply"],
        "status": "ThreadStatus.TO_REPLY",
        "memories": [
            {"id": "m1", "content": "prefer Bonjour"},
            {"id": "m2", "content": "sign as Michel-Marie"},
        ],
        "draft": "Bonjour Alex, on regarde ça demain. Bien à vous, Michel-Marie",
        "draft_language": "fr",
    }
    _emit_decision_trace(ctx, "e3", state)

    mem_entry = next(e for e in ctx.trace if e["node"] == "select_memories")
    assert mem_entry["count"] == 2
    assert mem_entry["memory_ids"] == ["m1", "m2"]

    draft_entry = next(e for e in ctx.trace if e["node"] == "draft_reply")
    assert draft_entry["draft_language"] == "fr"
    assert draft_entry["draft_preview"].startswith("Bonjour Alex")


def test_trace_load_thread_captures_sender_and_subject():
    ctx = _ctx()
    state: dict[str, Any] = {
        "thread": [
            {"from": [{"email": "alice@x.com"}], "subject": "Re: hi"},
            {"from": [{"email": "alice@x.com"}], "subject": "Re: hi"},
        ],
        "matched_by": "none",
        "rule_name": None,
    }
    _emit_decision_trace(ctx, "e4", state)

    load = ctx.trace[0]
    assert load["node"] == "load_thread"
    assert load["email_id"] == "e4"
    assert load["sender"] == "alice@x.com"
    assert load["subject"] == "Re: hi"
    assert load["thread_len"] == 2


def test_trace_empty_thread_still_produces_entries():
    ctx = _ctx()
    state: dict[str, Any] = {
        "thread": [],
        "matched_by": None,
        "rule_name": None,
    }
    _emit_decision_trace(ctx, "e5", state)

    assert (
        len(ctx.trace) >= 4
    )  # load_thread + match_rules + spam_triage + apply_actions + thread_status
    load = ctx.trace[0]
    assert load["thread_len"] == 0
    assert load["sender"] == ""


def test_trace_no_draft_no_memory_entries_when_absent():
    ctx = _ctx()
    state: dict[str, Any] = {
        "thread": [{"from": [{"email": "x@y.com"}]}],
        "matched_by": "learned_pattern",
        "rule_name": "label:Facturation",
        "actions_applied": ["label:Facturation"],
        "status": "ThreadStatus.FYI",
    }
    _emit_decision_trace(ctx, "e6", state)

    node_names = [e["node"] for e in ctx.trace]
    assert "select_memories" not in node_names
    assert "draft_reply" not in node_names
