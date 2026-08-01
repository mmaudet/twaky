"""AGE graph wrapper for the QA chain.

Wraps langchain-community's AGEGraph so the rest of twaky doesn't need to
know about the AGE-specific Cypher wrapping (`cypher('twake', $$ ... $$)`).
The graph schema is auto-refreshed on init and injected in the prompt of
GraphCypherQAChain.
"""

from __future__ import annotations

from functools import lru_cache

from langchain_community.graphs.age_graph import AGEGraph

from twaky.config import settings


@lru_cache(maxsize=1)
def get_graph() -> AGEGraph:
    conf = {
        "database": settings.twaky_pg_db,
        "user": settings.twaky_pg_user,
        "password": settings.twaky_pg_password,
        "host": settings.twaky_pg_host,
        "port": settings.twaky_pg_port,
    }
    graph = AGEGraph(graph_name=settings.twaky_graph_name, conf=conf, create=False)
    return graph
