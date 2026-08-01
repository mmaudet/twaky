"""Chronos StateGraph — script the LLM, assert it reaches the answer."""

from __future__ import annotations

from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

from tests.agents._fakes import scripted
from twaky.agents.chronos.agent import build_chronos_agent


def test_chronos_answers_direct_without_tools():
    llm = scripted([AIMessage(content="You have no events tomorrow.")])
    with patch("twaky.agents.chronos.agent._make_llm", return_value=llm):
        graph = build_chronos_agent()
        out = graph.invoke({"messages": [HumanMessage(content="what's on tomorrow?")]})
    final = out["messages"][-1]
    assert isinstance(final, AIMessage)
    assert "no events" in final.content.lower()


def test_chronos_uses_a_tool():
    llm = scripted(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "list_events",
                        "id": "c1",
                        "args": {
                            "from_iso": "2026-08-05T00:00:00+00:00",
                            "to_iso": "2026-08-05T23:59:59+00:00",
                        },
                    }
                ],
            ),
            AIMessage(content="You have 0 events on 2026-08-05."),
        ]
    )
    with (
        patch("twaky.agents.chronos.agent._make_llm", return_value=llm),
        patch("twaky.agents.chronos.tools.get_pool") as p,
    ):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchall.return_value = []
        p.return_value.connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cur
        graph = build_chronos_agent()
        out = graph.invoke(
            {"messages": [HumanMessage(content="events on 2026-08-05?")]}
        )
    final = out["messages"][-1]
    assert "0 events" in final.content
