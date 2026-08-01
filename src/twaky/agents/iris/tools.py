"""Iris research toolset — shared @tools re-exported for one-line import."""

from __future__ import annotations

from twaky.tools.graph_qa import ask_graph
from twaky.tools.read_url import read_url
from twaky.tools.web_search import web_search

TOOLS = [web_search, read_url, ask_graph]

__all__ = ["TOOLS", "ask_graph", "read_url", "web_search"]
