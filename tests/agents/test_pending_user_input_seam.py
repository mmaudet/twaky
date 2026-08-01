"""Test the pending_user_input parser used by the daemon."""

from __future__ import annotations

import json

from twaky.agents.atlas.pending import extract_pending_from_output


def _msg(content):
    class M:
        type = "tool"

    m = M()
    m.content = content
    return m


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
