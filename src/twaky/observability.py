"""Wire up Langfuse tracing for LiteLLM + LangChain.

If LANGFUSE_HOST + keys are absent, everything is a no-op.

Otherwise:
- `langfuse.langchain.CallbackHandler()` — attached to LangChain runs
  via `chain.invoke(config={"callbacks": [handler]})`. Produces the
  GENERATION observation with model + tokens.
- A `Langfuse` client is exposed via `get_client()`. Wrap agentic
  invocations in `with client.start_as_current_span(name=...) as span`
  and use `client.get_current_trace_id()` after — that ID matches the
  one the LangChain handler used, so a single trace groups the whole
  invocation.

Note: LiteLLM's native Langfuse callback (v1.94 → langfuse v2 API) is
incompatible with our langfuse v3 client (`sdk_integration` kwarg was
removed). We do NOT enable it — the LangChain callback alone gives
identical coverage (GENERATION per LLM call, tokens, model, cost).
"""

from __future__ import annotations

import os
from typing import Any

import structlog

from twaky.config import settings

log = structlog.get_logger("twaky.observability")

_configured = False


def _langfuse_enabled() -> bool:
    return bool(
        settings.langfuse_host
        and settings.langfuse_public_key
        and settings.langfuse_secret_key
    )


def configure() -> None:
    """Populate Langfuse env vars once per process for the SDK to pick up."""
    global _configured
    if _configured or not _langfuse_enabled():
        return

    os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key or ""
    os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key or ""
    os.environ["LANGFUSE_HOST"] = settings.langfuse_host or ""
    log.info("langfuse env configured", host=settings.langfuse_host)
    _configured = True


def get_client() -> Any | None:
    if not _langfuse_enabled():
        return None
    try:
        from langfuse import get_client  # v3

        return get_client()
    except Exception as e:  # noqa: BLE001
        log.warning("langfuse client unavailable", err=str(e))
        return None


def get_langchain_callback() -> Any | None:
    if not _langfuse_enabled():
        return None
    try:
        from langfuse.langchain import CallbackHandler

        return CallbackHandler()
    except Exception as e:  # noqa: BLE001
        log.warning("langfuse CallbackHandler unavailable", err=str(e))
        return None


def public_trace_url(trace_id: str) -> str:
    base = settings.langfuse_public_url or settings.langfuse_host or ""
    project = os.environ.get(
        "LANGFUSE_INIT_PROJECT_ID",
        settings.langfuse_public_key and "twaky-project" or "",
    )
    if not base:
        return ""
    return f"{base.rstrip('/')}/project/{project}/traces/{trace_id}"
