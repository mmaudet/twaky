"""Plume StateGraph — script the LLM, tools mocked at import level."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage

from tests.agents._fakes import scripted, stub_registry_for
from twaky.agents.plume.agent import build_plume_agent


def test_plume_reads_and_drafts():
    llm = scripted(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "read_email", "id": "c1", "args": {"message_id": "m1"}}
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "draft_reply",
                        "id": "c2",
                        "args": {"message_id": "m1", "tone": "casual"},
                    }
                ],
            ),
            AIMessage(content='{"draft":"Hi Bob","to":"bob@x","subject":"Re: hi"}'),
        ]
    )
    with (
        stub_registry_for("plume"),
        patch("twaky.agents.plume.agent._make_llm", return_value=llm),
        patch("twaky.agents.plume.tools.JmapClient") as C,
        patch("twaky.agents.plume.tools.bearer_token_for_owner", return_value="TOK"),
        patch("twaky.agents.plume.tools._make_llm") as tool_llm,
    ):
        inst = C.return_value
        inst.email_get = AsyncMock(
            return_value=[
                {
                    "id": "m1",
                    "subject": "hi",
                    "from": [{"email": "bob@x"}],
                    "textBody": [{"partId": "1"}],
                    "bodyValues": {"1": {"value": "hello"}},
                }
            ]
        )
        tool_llm.return_value.invoke.return_value = AIMessage(content="Hi Bob")
        graph = build_plume_agent()
        out = graph.invoke({"messages": [HumanMessage(content="draft a reply to m1")]})
    final = out["messages"][-1]
    assert "draft" in final.content.lower() or "hi" in final.content.lower()
