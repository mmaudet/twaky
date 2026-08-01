"""Iris sub-agent StateGraph — research via web + graph."""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_litellm import ChatLiteLLM
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from twaky.agents.iris.tools import TOOLS
from twaky.agents.state import AgentState
from twaky.config import settings

_SYSTEM = (
    "You are Iris, a research specialist. Use web_search to look things up, "
    "read_url to fetch a page's main text, and ask_graph to cross-reference "
    "with the Twake knowledge graph. Be concise. Never invent."
)


def _make_llm() -> BaseChatModel:
    return ChatLiteLLM(
        model=settings.iris_model or settings.model,
        api_base=settings.litellm_api_base,
    )


def _agent_node(state: AgentState):
    llm = _make_llm().bind_tools(TOOLS)
    messages = state.get("messages", [])
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=_SYSTEM), *messages]
    return {"messages": [llm.invoke(messages)]}


def build_iris_agent():
    g = StateGraph(AgentState)
    g.add_node("agent", _agent_node)
    g.add_node("tools", ToolNode(TOOLS))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    g.add_edge("tools", "agent")
    return g.compile()


__all__ = ["build_iris_agent"]
