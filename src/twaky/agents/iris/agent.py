"""Iris sub-agent StateGraph — research via web + graph."""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_litellm import ChatLiteLLM
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from twaky.agents.iris.tools import TOOLS
from twaky.agents.registry import load_agent_config
from twaky.agents.state import AgentState
from twaky.agents_config.models import AgentConfig
from twaky.config import settings
from twaky.skills.agent_tools import merged_tools_for


def _make_llm(cfg: AgentConfig) -> BaseChatModel:
    kwargs: dict = {
        "model": cfg.model or settings.model,
        "api_base": settings.litellm_api_base,
    }
    if cfg.temperature is not None:
        kwargs["temperature"] = cfg.temperature
    return ChatLiteLLM(**kwargs)


def _agent_node(state: AgentState):
    cfg = load_agent_config("iris")
    llm = _make_llm(cfg).bind_tools(merged_tools_for("iris", TOOLS))
    messages = state.get("messages", [])
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=cfg.system_prompt), *messages]
    return {"messages": [llm.invoke(messages)]}


def _iris_tools_node(state: AgentState):
    tools = merged_tools_for("iris", TOOLS)
    return ToolNode(tools).invoke(state)


def build_iris_agent():
    g = StateGraph(AgentState)
    g.add_node("agent", _agent_node)
    g.add_node("tools", _iris_tools_node)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    g.add_edge("tools", "agent")
    return g.compile()


__all__ = ["build_iris_agent"]
