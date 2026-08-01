"""Atlas tools — each delegate compiles + invokes a sub-agent."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from twaky.agents.atlas import tools as at


def test_delegate_to_chronos_returns_string():
    with patch("twaky.agents.atlas.tools._chronos") as build:
        graph = MagicMock()
        graph.invoke.return_value = {
            "messages": [MagicMock(content="You have 2 events tomorrow.")]
        }
        build.return_value = graph
        out = at.delegate_to_chronos.invoke({"query": "tomorrow?"})
    assert out == "You have 2 events tomorrow."


def test_finish_mission_signals_end():
    out = at.finish_mission.invoke({"final_answer": "all done", "outcome": "done"})
    # The tool returns a sentinel dict-string so the router can route to END.
    assert "all done" in out


def test_delegate_passthrough_of_pending_user_input():
    with patch("twaky.agents.atlas.tools._plume") as build:
        graph = MagicMock()
        graph.invoke.return_value = {
            "messages": [
                MagicMock(
                    content='{"answer":"draft ready","pending_user_input":'
                    '{"kind":"approve_draft","artifact":{"draft":"hi"}}}'
                )
            ]
        }
        build.return_value = graph
        out = at.delegate_to_plume.invoke({"query": "draft one"})
    assert "pending_user_input" in out
