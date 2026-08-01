"""NL-to-Cypher on the AGE graph, exposed as a LangChain @tool.

Refactored out of the old src/twaky/agent.py. The @tool is imported by any
agent that needs graph queries (Iris uses it directly). The `ask()` CLI
command is removed — user-facing queries now go through `twaky mission
declare` or `twaky tools graph-qa` for one-shot debugging.
"""

from __future__ import annotations

from langchain_community.chains.graph_qa.cypher import GraphCypherQAChain
from langchain_core.prompts import PromptTemplate
from langchain_core.tools import tool
from langchain_litellm import ChatLiteLLM

from twaky.config import settings
from twaky.graph import get_graph

CYPHER_PROMPT = PromptTemplate.from_template(
    """Task: Generate an openCypher query for Apache AGE.

Schema of the graph:
{schema}

Domain conventions:
- Natural identifiers: CalendarEvent → uid (slug); Person → email.
- Relationship directions: (:Person)-[:ORGANIZED|:ATTENDED]->(:CalendarEvent);
  (:Person)-[:WORKS_AT]->(:Organization).
- ATTENDED.status is lowercased: "accepted"|"declined"|"tentative"|"unknown".
- deleted is BOOLEAN on Person/CalendarEvent/Email.

Rules:
- MATCH-only. Never CREATE/MERGE/DELETE/DROP/SET/REMOVE.
- Every RETURN column MUST be aliased with AS <plain_identifier>.

Question: {question}

Cypher query:"""
)


def build_chain() -> GraphCypherQAChain:
    llm = ChatLiteLLM(
        model=settings.iris_model or settings.model,
        api_base=settings.litellm_api_base,
    )
    graph = get_graph()
    try:
        graph.refresh_schema()
    except Exception:  # noqa: BLE001, S110
        pass
    return GraphCypherQAChain.from_llm(
        llm=llm,
        graph=graph,
        cypher_prompt=CYPHER_PROMPT,
        verbose=False,
        return_intermediate_steps=False,
        allow_dangerous_requests=True,
        top_k=10,
    )


@tool
def ask_graph(question: str) -> str:
    """Ask a natural-language question about the Twake knowledge graph.

    Use this to look up people, calendar events, mail metadata,
    organizations, or relationships between them. Returns a text answer.
    """
    chain = build_chain()
    result = chain.invoke({"query": question})
    return result.get("result", "")


__all__ = ["ask_graph", "build_chain"]
