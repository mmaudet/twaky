"""Chronos sub-agent StateGraph."""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_litellm import ChatLiteLLM
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from twaky.agents.chronos.tools import (
    find_conflicts,
    get_event,
    list_events,
    next_free_slot,
)
from twaky.agents.registry import load_agent_config
from twaky.agents.state import AgentState
from twaky.agents_config.models import AgentConfig
from twaky.config import settings

TOOLS = [list_events, get_event, find_conflicts, next_free_slot]


def _make_llm(cfg: AgentConfig) -> BaseChatModel:
    kwargs: dict = {
        "model": cfg.model or settings.model,
        "api_base": settings.litellm_api_base,
    }
    if cfg.temperature is not None:
        kwargs["temperature"] = cfg.temperature
    return ChatLiteLLM(**kwargs)


def _agent_node(state: AgentState):
    cfg = load_agent_config("chronos")
    llm = _make_llm(cfg).bind_tools(TOOLS)
    messages = state.get("messages", [])
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=cfg.system_prompt), *messages]
    return {"messages": [llm.invoke(messages)]}


def build_chronos_agent():
    g = StateGraph(AgentState)
    g.add_node("agent", _agent_node)
    g.add_node("tools", ToolNode(TOOLS))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    g.add_edge("tools", "agent")
    return g.compile()


__all__ = ["build_chronos_agent"]
