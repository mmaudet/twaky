"""Atlas StateGraph — LLM scripted."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage

from tests.agents._fakes import scripted
from twaky.agents.atlas.agent import build_atlas_agent
from twaky.agents.atlas.tools import FINISH_MARKER


def test_atlas_delegates_then_finishes():
    llm = scripted(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "delegate_to_chronos",
                        "id": "c1",
                        "args": {"query": "events tomorrow?"},
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "finish_mission",
                        "id": "c2",
                        "args": {
                            "final_answer": "0 events tomorrow.",
                            "outcome": "done",
                        },
                    }
                ],
            ),
        ]
    )
    with (
        patch("twaky.agents.atlas.agent._make_llm", return_value=llm),
        patch("twaky.agents.atlas.tools._chronos") as ch,
    ):
        graph = MagicMock()
        graph.invoke.return_value = {
            "messages": [MagicMock(content="No events tomorrow.")]
        }
        ch.return_value = graph
        atlas = build_atlas_agent()
        out = atlas.invoke(
            {
                "mission_id": uuid4(),
                "owner_email": "a@x",
                "intent_text": "Résume ma journée de demain",
                "messages": [HumanMessage(content="Résume ma journée de demain")],
                "artifacts": [],
                "step_count": 0,
                "pending_user_input": None,
            }
        )
    # The last tool call was finish_mission — the last ToolMessage should carry the marker.
    tool_msgs = [m for m in out["messages"] if getattr(m, "type", "") == "tool"]
    assert any(FINISH_MARKER in getattr(m, "content", "") for m in tool_msgs)
