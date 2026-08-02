"""Iris StateGraph — LLM scripted, tools mocked or not called."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from tests.agents._fakes import scripted, stub_registry_for
from twaky.agents.iris.agent import build_iris_agent
from twaky.skills import registry as skills_registry


@pytest.fixture(autouse=True)
def _stub_skills_for(monkeypatch):
    """Prevent test agents from touching real Postgres for skill loading."""
    monkeypatch.setattr(skills_registry, "_repository_get_bound", lambda agent_id: [])
    skills_registry.invalidate_all()
    yield
    skills_registry.invalidate_all()


def test_iris_answers_directly():
    llm = scripted([AIMessage(content="Acme Corp makes widgets.")])
    with (
        stub_registry_for("iris"),
        patch("twaky.agents.iris.agent._make_llm", return_value=llm),
    ):
        g = build_iris_agent()
        out = g.invoke({"messages": [HumanMessage(content="what does acme do?")]})
    assert "widgets" in out["messages"][-1].content.lower()
