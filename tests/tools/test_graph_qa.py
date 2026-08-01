"""Tests for the extracted graph_qa @tool (refactored from agent.py)."""

from __future__ import annotations

import os

import psycopg
import pytest

from twaky.config import settings
from twaky.tools.graph_qa import ask_graph, build_chain


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


def test_ask_graph_is_a_langchain_tool():
    # LangChain @tool objects expose .name, .description, .args_schema
    assert ask_graph.name == "ask_graph"
    assert "graph" in ask_graph.description.lower()


@pytest.mark.skipif(not _reachable(), reason="twaky-pg not reachable")
def test_build_chain_returns_graph_cypher_qa_chain():
    from langchain_community.chains.graph_qa.cypher import GraphCypherQAChain

    chain = build_chain()
    assert isinstance(chain, GraphCypherQAChain)
