"""Synchronous delegation helper: declare a mission for Atlas and block until terminal.

``Delegation`` is designed to be called from a synchronous context — specifically
from a sentinel's ``process()`` method, which T8 runs via ``asyncio.to_thread``.
Inside a thread, ``asyncio.run(...)`` creates a fresh event loop and is safe to
use; calling ``delegate()`` from inside an already-running event loop will raise
``RuntimeError`` immediately with a clear message.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import psycopg
import structlog

from twaky.config import settings
from twaky.missions import engine, repository

log = structlog.get_logger("twaky.sentinels.delegation")


@dataclass
class DelegationResult:
    mission_id: UUID
    state: str  # "done" | "failed" | "cancelled" | "timeout"
    payload: list[dict[str, Any]] = field(default_factory=list)


class Delegation:
    """Declare a mission for Atlas and block (synchronously) until it reaches a
    terminal state or a timeout expires.

    Instantiate once per sentinel and reuse across calls::

        delegation = Delegation("mail", settings.pg_dsn)
        result = delegation.delegate(intent_text="Process this email", timeout_s=60.0)

    **Threading contract:** ``delegate()`` MUST be called from a synchronous thread
    (not from inside a running asyncio event loop).  T8 runs ``sentinel.process()``
    via ``asyncio.to_thread()``, so calling ``delegate()`` from within ``process()``
    is always safe.  Calling from inside an async coroutine will raise ``RuntimeError``
    before any DB work is done.
    """

    def __init__(self, sentinel_name: str, dsn: str) -> None:
        self.sentinel_name = sentinel_name
        self.declared_by = f"sentinel:{sentinel_name}"
        self._dsn = dsn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def delegate(
        self,
        *,
        intent_text: str,
        artifact: dict[str, Any] | None = None,
        timeout_s: float = 120.0,
    ) -> DelegationResult:
        """Declare a mission for Atlas + block until terminal.

        MUST be called from a synchronous context (thread pool) — internal
        implementation uses asyncio.run for the LISTEN loop. If Atlas hasn't
        finished within ``timeout_s``, returns state="timeout" WITHOUT
        cancelling the mission (caller's policy).

        Parameters
        ----------
        intent_text:
            Free-form description of what Atlas should do.
        artifact:
            Optional dict of contextual hints; if provided, it is serialised
            and appended to ``intent_text`` so Atlas receives it inline.
            (MVP: no separate state transition — artifact is informational.)
        timeout_s:
            Maximum seconds to wait for Atlas to reach a terminal state.
        """
        # Guard: must not be called from inside an event loop.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None  # Good — we are in a plain thread.
        if loop is not None:
            raise RuntimeError(
                "Delegation.delegate() must be called from a synchronous thread, "
                "not from inside a running event loop. "
                "Use asyncio.to_thread(sentinel.process, ...) as T8 specifies."
            )

        # Optionally embed artifact hints inline in intent_text (MVP approach).
        full_intent = intent_text
        if artifact is not None:
            full_intent = f"{intent_text}\n\nContext:\n{json.dumps(artifact, indent=2)}"

        # Declare the mission — Atlas daemon picks it up via mission_declared NOTIFY.
        mission = engine.declare(
            intent_text=full_intent,
            owner_email=settings.twaky_owner_email,
            declared_by=self.declared_by,
        )
        log.info(
            "delegation.declared",
            mission_id=str(mission.id),
            sentinel=self.sentinel_name,
        )

        # Block until terminal (or timeout).
        return asyncio.run(self._await_terminal(mission.id, timeout_s))

    # ------------------------------------------------------------------
    # Internal async machinery
    # ------------------------------------------------------------------

    async def _await_terminal(
        self, mission_id: UUID, timeout_s: float
    ) -> DelegationResult:
        """Async core: LISTEN on mission_changed, re-read on each matching NOTIFY.

        LISTEN order matters (M2, SP6 final review): we open the connection and
        issue LISTEN *before* the fast-path get().  If the order were reversed,
        Atlas could transition the mission to a terminal state between the get()
        return and the LISTEN registration, silently dropping the NOTIFY and
        causing delegate() to wait until ``timeout_s`` even though the mission
        is already done.  Opening LISTEN first guarantees no transition is missed:
        the fast-path get() that follows will catch any already-terminal state,
        and the notify loop will catch any transition that happens afterwards.
        """
        try:
            async with asyncio.timeout(timeout_s):
                async with await psycopg.AsyncConnection.connect(
                    self._dsn, autocommit=True
                ) as conn:
                    # Register LISTEN *before* the fast-path get() — see docstring.
                    await conn.execute("LISTEN mission_changed")

                    # Fast path: Atlas may have already finished before we registered.
                    current = repository.get(mission_id)
                    if current is not None and current.state.is_terminal:
                        log.info(
                            "delegation.already_terminal",
                            mission_id=str(mission_id),
                            state=current.state.value,
                        )
                        return DelegationResult(
                            mission_id=mission_id,
                            state=current.state.value,
                            payload=list(current.artifacts),
                        )

                    async for notify in conn.notifies():
                        # The mission_changed payload is JSON:
                        # {"mission_id": "<uuid>", "state": "<state>", "at": "<iso>"}
                        try:
                            data = json.loads(notify.payload)
                        except (json.JSONDecodeError, TypeError):
                            continue
                        if data.get("mission_id") != str(mission_id):
                            continue
                        # Re-read from DB — the NOTIFY is a signal, not a payload carrier.
                        mission = repository.get(mission_id)
                        if mission is None:
                            log.warning(
                                "delegation.mission_vanished",
                                mission_id=str(mission_id),
                            )
                            break
                        if mission.state.is_terminal:
                            log.info(
                                "delegation.terminal",
                                mission_id=str(mission_id),
                                state=mission.state.value,
                            )
                            return DelegationResult(
                                mission_id=mission_id,
                                state=mission.state.value,
                                payload=list(mission.artifacts),
                            )
        except TimeoutError:
            log.warning(
                "delegation.timeout",
                mission_id=str(mission_id),
                timeout_s=timeout_s,
            )
            return DelegationResult(
                mission_id=mission_id,
                state="timeout",
                payload=[],
            )

        # Defensive: loop exited without terminal state (notify stream closed).
        mission = repository.get(mission_id)
        if mission is not None and mission.state.is_terminal:
            return DelegationResult(
                mission_id=mission_id,
                state=mission.state.value,
                payload=list(mission.artifacts),
            )
        return DelegationResult(mission_id=mission_id, state="timeout", payload=[])


__all__ = ["Delegation", "DelegationResult"]
