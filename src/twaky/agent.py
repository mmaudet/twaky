"""Natural-language → Cypher → answer, over the AGE graph.

Uses langchain_community.chains.graph_qa.cypher.GraphCypherQAChain, backed
by AGEGraph, with a ChatLiteLLM model. The result is wrapped in a small
container that also carries the Langfuse trace URL (if enabled) so callers
can jump directly to the trace.

Also exposes `ask_graph` as a LangGraph @tool for reuse in agentic flows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import structlog
from langchain_community.chains.graph_qa.cypher import GraphCypherQAChain
from langchain_core.prompts import PromptTemplate
from langchain_litellm import ChatLiteLLM

from twaky import observability
from twaky.config import settings
from twaky.graph import get_graph

# AGEGraph.query() derives result column names from tokens after RETURN.
# For expressions like `p.email` the derived name is `p.email`, which
# breaks the AS clause it appends. Force the LLM to alias every RETURN
# item with a plain identifier.
CYPHER_PROMPT = PromptTemplate.from_template(
    """Task: Generate an openCypher query for Apache AGE.

Schema of the graph:
{schema}

Rules:
- Only generate a query — no prose, no code fences, no trailing semicolon.
- MATCH-only. Never generate CREATE, MERGE, DELETE, DROP, SET, REMOVE.
- **Every RETURN expression MUST be aliased with `AS <plain_identifier>`.**
  Example: `RETURN p.email AS email, p.fn AS fn` — NEVER `RETURN p.email, p.fn`.
- Prefer natural-key matches (email for Person, uid for CalendarEvent).

Question: {question}

Cypher query:"""
)

logging.basicConfig(level=logging.INFO)
log = structlog.get_logger("twaky.agent")


@dataclass
class AgentAnswer:
    question: str
    answer: str
    cypher: str | None
    trace_url: str | None
    intermediate: dict[str, Any]

    def __str__(self) -> str:
        parts = [f"Q: {self.question}", f"A: {self.answer}"]
        if self.cypher:
            parts.append(f"Cypher generated:\n  {self.cypher}")
        if self.trace_url:
            parts.append(f"Trace: {self.trace_url}")
        return "\n".join(parts)


def _build_chain() -> GraphCypherQAChain:
    llm = ChatLiteLLM(
        model=settings.model,
        api_base=settings.litellm_api_base,
    )
    graph = get_graph()
    try:
        graph.refresh_schema()
    except Exception as e:  # noqa: BLE001
        log.warning("refresh_schema failed (empty graph?)", err=str(e))
    return GraphCypherQAChain.from_llm(
        llm=llm,
        graph=graph,
        cypher_prompt=CYPHER_PROMPT,
        verbose=True,
        return_intermediate_steps=True,
        allow_dangerous_requests=True,  # required by langchain-community 0.4
        top_k=10,
    )


def ask(question: str) -> AgentAnswer:
    observability.configure()

    chain = _build_chain()
    callbacks = []
    handler = observability.get_langchain_callback()
    if handler is not None:
        callbacks.append(handler)

    lf = observability.get_client()
    trace_id: str | None = None

    def _invoke() -> dict[str, Any]:
        return chain.invoke(
            {"query": question},
            config={"callbacks": callbacks, "metadata": {"source": "twaky.cli.ask"}},
        )

    if lf is not None:
        with lf.start_as_current_span(name="twaky.ask") as span:
            span.update(input={"question": question})
            result = _invoke()
            trace_id = lf.get_current_trace_id()
            span.update(output={"answer": result.get("result")})
        # Ensure the trace is flushed before we exit (CLI process may end fast).
        try:
            lf.flush()
        except Exception:  # noqa: BLE001, S110
            pass
        if trace_id:
            try:
                lf.create_score(
                    trace_id=trace_id,
                    name="cypher_generated",
                    value=1 if result.get("intermediate_steps") else 0,
                )
                lf.flush()
            except Exception:  # noqa: BLE001, S110
                pass
    else:
        result = _invoke()

    intermediate = result.get("intermediate_steps") or []
    cypher = None
    for step in intermediate:
        if isinstance(step, dict) and step.get("query"):
            cypher = step["query"]
            break

    return AgentAnswer(
        question=question,
        answer=result.get("result", ""),
        cypher=cypher,
        trace_url=observability.public_trace_url(trace_id) if trace_id else None,
        intermediate={"steps": intermediate},
    )


# LangGraph exposure: a plain tool other graphs can wire in.
try:
    from langchain_core.tools import tool

    @tool
    def ask_graph(question: str) -> str:
        """Ask the Twake knowledge graph a natural-language question and return the answer."""
        return ask(question).answer
except Exception:  # noqa: BLE001  # pragma: no cover - langchain_core always present in prod
    ask_graph = None  # type: ignore[assignment]


__all__ = ["AgentAnswer", "ask", "ask_graph"]
