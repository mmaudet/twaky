"""Sentinel ABC, Outcome enum, Event TypedDict, and Context dataclass.

This module defines the framework contract that every sentinel vertical must
implement. Concrete types (db_pool, mission_emitter, delegation) arrive in
later tasks (T4/T5); Context uses Any placeholders guarded by TYPE_CHECKING
imports so the module stays importable before those packages exist.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar, Literal, TypedDict


class Outcome(str, Enum):
    """Terminal outcome of one sentinel event processing run.

    Values MUST match the DB CHECK constraint on sentinel_run.outcome
    (sql/008_init_sentinels.sh).  A regression test in tests/sentinels/
    test_base.py asserts {o.value for o in Outcome} == DB_CHECK_SET.
    """

    IGNORED = "ignored"
    PROCESSED = "processed"
    MISSION_CREATED = "mission_created"
    DELEGATED = "delegated"
    ERROR = "error"


class Event(TypedDict):
    """Normalised event payload delivered to every sentinel.

    Produced by an EventSource (RabbitMQEventSource or JmapPollingEventSource)
    and handed verbatim to Sentinel.process().
    """

    source_kind: Literal["rabbitmq", "jmap_poll"]
    source_ref: str  # exchange:routing_key  OR  jmap accountId
    message_id: str  # RabbitMQ message-id  OR  JMAP email id
    payload: dict[str, Any]


@dataclass
class Context:
    """Runtime context injected by the dispatcher into each process() call.

    Sentinels MUST NOT construct Context themselves — the runtime creates it.

    Fields
    ------
    db_pool
        Active psycopg_pool.ConnectionPool for direct DB access
        (type is Any until T4 wires the concrete pool type).
    mission_emitter
        Callable / helper for creating Twaky missions on behalf of the
        sentinel (concrete type lands in T4 emitter.py).
    delegation
        Helper for delegating to Atlas (concrete type lands in T5
        delegation.py).
    sentinel_row
        The `SentinelConfig` dataclass for the currently active sentinel,
        carrying config_values and metadata fetched from the DB at boot.
    logger
        A stdlib logger pre-bound to the sentinel name; use it instead of
        creating your own.
    """

    db_pool: Any
    mission_emitter: Any
    delegation: Any
    sentinel_row: Any
    logger: logging.Logger


class Sentinel(ABC):
    """Background autonomous agent subscribed to an event source.

    Subclasses declare their event bindings via ClassVars and implement
    ``process(event, ctx)``.  The runtime handles consumption, retry,
    logging, and sentinel_run bookkeeping.

    Class attributes
    ----------------
    name
        Matches the primary key in the ``sentinel`` DB table
        (e.g. ``"mail"``).
    version
        Semantic version of this sentinel implementation (e.g. ``"1.0.0"``).
        Logged on startup; surfaced in the ``sentinel`` row's ``version``
        column for observability.
    event_source_kind
        Which event source the runtime should wire this sentinel to.
        ``"rabbitmq"`` → RabbitMQEventSource; ``"jmap_poll"`` →
        JmapPollingEventSource.
    """

    name: ClassVar[str]
    version: ClassVar[str]
    event_source_kind: ClassVar[Literal["rabbitmq", "jmap_poll"]]

    @abstractmethod
    def process(self, event: Event, ctx: Context) -> Outcome:
        """Handle one event. Returns an Outcome for observability."""

    def should_process(self, event: Event, ctx: Context) -> bool:
        """Cheap pre-filter executed before spinning up the main pipeline.

        Override to skip events quickly (e.g. routing-key mismatch, event
        already handled).  Default returns True (process everything).
        """
        return True

    def config_schema(self) -> dict[str, Any]:
        """JSON Schema for the /sentinels UI config form. Override as needed."""
        return {}


__all__ = [
    "Context",
    "Event",
    "Outcome",
    "Sentinel",
]
