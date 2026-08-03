"""Test the pending_user_input parser used by the daemon."""

from __future__ import annotations

import json

from twaky.agents.atlas.pending import extract_pending_from_output


def _msg(content, type: str = "tool"):
    class M:
        pass

    m = M()
    m.type = type
    m.content = content
    return m


def _tool(content):
    return _msg(content, type="tool")


def _human(content):
    return _msg(content, type="human")


def _ai(content):
    return _msg(content, type="ai")


def test_parses_json_pending_from_tool_message():
    payload = {
        "answer": "Draft ready",
        "pending_user_input": {"kind": "approve_draft", "artifact": {"draft": "Hi"}},
    }
    out = extract_pending_from_output({"messages": [_msg(json.dumps(payload))]})
    assert out == {"kind": "approve_draft", "artifact": {"draft": "Hi"}}


def test_returns_none_when_no_pending():
    out = extract_pending_from_output({"messages": [_msg("all done")]})
    assert out is None


def test_returns_none_when_json_but_no_key():
    out = extract_pending_from_output({"messages": [_msg('{"answer":"x"}')]})
    assert out is None


def test_returns_none_when_human_message_follows_pending():
    """Regression: after engine.resume seeds a HumanMessage with the
    user's response, the old tool-emitted pending JSON is still in
    message history — must NOT re-trigger awaiting_user."""
    payload = json.dumps(
        {
            "answer": "Please approve",
            "pending_user_input": {"kind": "approve_x", "artifact": {"k": "v"}},
        }
    )
    msgs = [
        _tool(payload),
        _human('{"approved": true}'),  # engine.resume seed
        _ai("Great, I'll finish."),
    ]
    out = extract_pending_from_output({"messages": msgs})
    assert out is None, (
        "found stale pending after a HumanMessage — mission would loop "
        "back to awaiting_user forever after user approval"
    )


def test_returns_pending_when_no_human_response_after():
    """Sanity: fresh pending (no HumanMessage after) still fires."""
    payload = json.dumps(
        {
            "answer": "Please approve",
            "pending_user_input": {"kind": "approve_x", "artifact": {"k": "v"}},
        }
    )
    msgs = [_ai("Calling tool"), _tool(payload)]
    out = extract_pending_from_output({"messages": msgs})
    assert out == {"kind": "approve_x", "artifact": {"k": "v"}}


def test_returns_latest_pending_when_multiple_and_no_response():
    """If the graph emitted two pending JSONs and no HumanMessage after,
    the LATEST one wins (usual case: LLM iterated and refined its ask)."""
    p1 = json.dumps(
        {"answer": "A", "pending_user_input": {"kind": "k1", "artifact": {}}}
    )
    p2 = json.dumps(
        {"answer": "B", "pending_user_input": {"kind": "k2", "artifact": {}}}
    )
    msgs = [_tool(p1), _ai("retry"), _tool(p2)]
    out = extract_pending_from_output({"messages": msgs})
    assert out == {"kind": "k2", "artifact": {}}
