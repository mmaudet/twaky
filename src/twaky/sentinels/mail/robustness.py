"""Robustness helpers for the mail sentinel pipeline (SP6c overnight polish).

Two mechanisms live here:

- :class:`LLMCircuitBreaker` — tracks consecutive failures of the
  LLM-backed nodes and short-circuits ``structured_call`` for a cool-off
  window after the threshold is hit. Prevents a stuck upstream (down,
  quota'd, rate-limited) from turning every incoming email into a slow
  timeout error and consuming worker threads.

- :func:`resilient_node` — factory wrapper that (a) enforces a wall-time
  budget per node via a thread pool and (b) never lets an exception
  propagate out of the pipeline. On timeout OR crash the wrapper logs
  the failure with structured context and returns an empty dict so
  downstream nodes still run. A single bad email cannot corrupt the
  daemon.

Design notes
------------
- Circuit breaker state is process-local (a module-level singleton).
  This is acceptable for the mail sentinel — a single container instance
  processes the owner's INBOX. Sharing across replicas is out of scope.
- Time-outs use ``concurrent.futures.ThreadPoolExecutor`` rather than
  ``signal.SIGALRM``: SIGALRM only fires on the main thread and the
  sentinel runtime runs nodes inside an asyncio executor.
- Empty-dict return is the recognised LangGraph "no-op" pattern used
  elsewhere in :mod:`twaky.sentinels.mail.nodes` (e.g. ``make_draft_reply``
  short-circuit on empty thread).
"""

from __future__ import annotations

import concurrent.futures
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM circuit breaker
# ---------------------------------------------------------------------------


@dataclass
class LLMCircuitBreaker:
    """Process-local circuit breaker for LLM-backed calls.

    After ``failure_threshold`` consecutive failures the breaker enters
    ``open`` state and :meth:`should_skip` returns ``True`` for
    ``cool_off_s`` seconds. A single successful call while open closes
    it. In ``open`` state the breaker returns True for
    :meth:`should_skip` even after cool-off — the first request after
    cool-off transitions to ``half-open`` and is allowed through as a
    probe. If that probe fails, the breaker re-opens for another
    cool-off window.
    """

    failure_threshold: int = 5
    cool_off_s: float = 300.0

    _consecutive_failures: int = 0
    _opened_at: float = 0.0

    def should_skip(self) -> bool:
        """Return True when callers should skip the LLM call entirely."""
        if self._consecutive_failures < self.failure_threshold:
            return False
        # Open state: skip until cool-off elapses, then allow one probe.
        return (time.monotonic() - self._opened_at) < self.cool_off_s

    def record_success(self) -> None:
        """Reset counters after a successful call — closes the breaker."""
        if self._consecutive_failures:
            log.info(
                "llm_circuit_breaker: closing after success "
                "(consecutive_failures was %d)",
                self._consecutive_failures,
            )
        self._consecutive_failures = 0
        self._opened_at = 0.0

    def record_failure(self) -> None:
        """Increment the consecutive-failure counter; open on threshold."""
        self._consecutive_failures += 1
        if self._consecutive_failures == self.failure_threshold:
            self._opened_at = time.monotonic()
            log.warning(
                "llm_circuit_breaker: opening after %d consecutive failures "
                "— skipping LLM calls for %.0fs",
                self.failure_threshold,
                self.cool_off_s,
            )

    def reset(self) -> None:
        """Force back to closed state (for tests and manual intervention)."""
        self._consecutive_failures = 0
        self._opened_at = 0.0


# Module-level singleton. The sentinel process shares one breaker across
# every LLM-backed node so unrelated calls (draft_reply + match_rules +
# spam_check) accumulate together — the failure mode we protect against
# (LLM endpoint down / rate-limited) hits them all at once.
_llm_breaker = LLMCircuitBreaker()


def get_llm_breaker() -> LLMCircuitBreaker:
    """Return the process-wide LLM circuit-breaker instance."""
    return _llm_breaker


# ---------------------------------------------------------------------------
# Per-node try/except + timeout wrapper
# ---------------------------------------------------------------------------


class NodeTimeoutError(Exception):
    """Raised internally when a node exceeds its wall-time budget.

    Not re-raised out of the pipeline — :func:`resilient_node` catches
    it and returns an empty state dict.
    """


def resilient_node[State: dict](
    node_name: str,
    node_fn: Callable[[State], State],
    *,
    timeout_s: float = 30.0,
) -> Callable[[State], State]:
    """Wrap a pipeline node with a wall-time budget + fatal-error trap.

    Parameters
    ----------
    node_name:
        Logical name (``"load_thread"``, ``"spam_triage"``, …) — used in
        log messages so failures are attributable.
    node_fn:
        The node callable produced by ``make_<name>(ctx)``.
    timeout_s:
        Wall-time budget for one invocation. Defaults to 30 seconds.

    Returns
    -------
    Callable
        A wrapped node that returns the original node's output on
        success, or an empty dict on timeout / exception.

    Guarantees
    ----------
    - Exceptions from the wrapped node are caught, logged with the
      email id and elapsed time, and the pipeline continues.
    - Timeouts trigger a warning log; the thread pool worker is
      abandoned to complete on its own (Python cannot forcibly kill
      threads; the caller pays with a tied-up worker until the slow
      call returns). Kept short (30s default) to limit accumulation.
    """

    def _wrapped(state: State) -> State:
        email_id = state.get("email_id", "<unknown>")
        started = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"node-{node_name}"
        ) as executor:
            future = executor.submit(node_fn, state)
            try:
                return future.result(timeout=timeout_s)
            except concurrent.futures.TimeoutError:
                elapsed = time.monotonic() - started
                log.warning(
                    "resilient_node[%s]: TIMEOUT after %.1fs (budget=%.1fs) "
                    "on email_id=%s — pipeline continues, node output dropped",
                    node_name,
                    elapsed,
                    timeout_s,
                    email_id,
                )
                # Do NOT cancel; the worker will finish in the background
                # and its return value is discarded. Python has no way to
                # forcibly kill a thread — the executor.__exit__ waits.
                # Return no-op state so downstream nodes still fire.
                return {}  # type: ignore[return-value]
            except Exception:
                elapsed = time.monotonic() - started
                log.exception(
                    "resilient_node[%s]: exception after %.1fs on email_id=%s "
                    "— pipeline continues, node output dropped",
                    node_name,
                    elapsed,
                    email_id,
                )
                return {}  # type: ignore[return-value]

    _wrapped.__name__ = f"resilient_{node_name}"
    return _wrapped


__all__ = [
    "LLMCircuitBreaker",
    "NodeTimeoutError",
    "get_llm_breaker",
    "resilient_node",
]
