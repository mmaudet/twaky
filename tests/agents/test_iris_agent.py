"""Iris StateGraph — LLM scripted, tools mocked or not called."""

from __future__ import annotations

from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

from tests.agents._fakes import scripted
from twaky.agents.iris.agent import build_iris_agent


def test_iris_answers_directly():
    llm = scripted([AIMessage(content="Acme Corp makes widgets.")])
    with patch("twaky.agents.iris.agent._make_llm", return_value=llm):
        g = build_iris_agent()
        out = g.invoke({"messages": [HumanMessage(content="what does acme do?")]})
    assert "widgets" in out["messages"][-1].content.lower()
