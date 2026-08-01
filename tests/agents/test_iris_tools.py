"""Iris tools — just re-exports."""

from __future__ import annotations


def test_tools_are_all_langchain_tools():
    from twaky.agents.iris.tools import TOOLS

    assert len(TOOLS) == 3
    names = {t.name for t in TOOLS}
    assert names == {"web_search", "read_url", "ask_graph"}
