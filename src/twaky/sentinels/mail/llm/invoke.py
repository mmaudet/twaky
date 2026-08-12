"""Structured LLM invocation with mandatory hardening and use-case declaration."""

from __future__ import annotations

import logging
from typing import Any

from langchain_litellm import ChatLiteLLM

from twaky.config import settings
from twaky.sentinels.mail.llm.hardening import Hardening, hardening_prefix
from twaky.sentinels.mail.llm.tiers import UseCase, models_for, tier_for
from twaky.sentinels.mail.robustness import get_llm_breaker

log = logging.getLogger(__name__)


class LLMCircuitOpen(RuntimeError):
    """Raised when the LLM circuit breaker is open.

    Callers (typically pipeline nodes wrapped by ``resilient_node``) catch
    this to fall back to their static / no-LLM behaviour instead of
    treating a breaker-open state as a hard failure. The wrapper's
    generic ``except Exception`` also catches it, so a call site that
    simply ignores the breaker still gets safe pipeline continuation.
    """


def structured_call[T](
    prompt: str,
    schema: type[T],
    *,
    hardening: Hardening,
    use_case: UseCase,
) -> T:
    """Invoke an LLM with structured output, mandatory hardening and use-case.

    Both ``hardening`` and ``use_case`` are keyword-only to prevent accidental
    positional misuse.  Passing a plain string instead of the enum raises
    ``TypeError`` immediately (defensive guard).
    """
    if not isinstance(hardening, Hardening):
        raise TypeError(
            f"hardening must be a Hardening enum member, got {type(hardening)!r}"
        )
    if not isinstance(use_case, UseCase):
        raise TypeError(
            f"use_case must be a UseCase enum member, got {type(use_case)!r}"
        )

    tier = tier_for(use_case)
    models = models_for(tier)
    if not models:
        raise RuntimeError(
            f"No models configured for tier {tier.value!r}. "
            "Set the corresponding MAIL_SENTINEL_*_LLMS environment variable."
        )

    # Circuit breaker: if too many consecutive LLM failures, skip until
    # cool-off. Prevents a down upstream from tying up every node's
    # thread budget with slow timeouts.
    breaker = get_llm_breaker()
    if breaker.should_skip():
        raise LLMCircuitOpen(
            "LLM circuit breaker open — too many consecutive failures; "
            "skipping this call until cool-off elapses"
        )

    full_prompt = hardening_prefix(hardening) + prompt

    llm_kwargs: dict[str, Any] = {}
    if settings.mail_sentinel_api_base:
        llm_kwargs["api_base"] = settings.mail_sentinel_api_base
    if settings.mail_sentinel_api_key:
        llm_kwargs["api_key"] = settings.mail_sentinel_api_key

    last_exc: Exception | None = None
    for model in models:
        try:
            llm = ChatLiteLLM(model=model, **llm_kwargs)
            llm_structured = llm.with_structured_output(schema)
            result = llm_structured.invoke(full_prompt)
            if not isinstance(result, schema):
                result = schema.model_validate(result)  # type: ignore[attr-defined]
            breaker.record_success()
            return result  # type: ignore[return-value]
        except Exception as exc:  # noqa: BLE001
            log.warning("Model %r failed for use_case=%r: %s", model, use_case, exc)
            last_exc = exc

    assert last_exc is not None  # always set — models list is non-empty
    breaker.record_failure()
    raise last_exc


__all__ = ["structured_call"]
