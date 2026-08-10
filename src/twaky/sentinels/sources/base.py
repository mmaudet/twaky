"""EventSource ABC and Ack type alias for the sentinels framework.

Every concrete event source (RabbitMQ, JMAP polling, …) must inherit from
``EventSource`` and implement the ``stream`` async generator method.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from twaky.sentinels.base import Event

#: Callable that acknowledges (or nacks) one message delivery.
Ack = Callable[[], Awaitable[None]]


async def _noop_ack() -> None:
    """No-op acknowledgement used when the transport does not require acks."""
    return


class EventSource(ABC):
    """Wire events onto the runtime dispatcher.

    Concrete implementations must yield ``(event, ack)`` pairs.  The caller
    is responsible for invoking ``ack()`` after the event has been
    successfully processed (or explicitly dropping it).
    """

    @abstractmethod
    def stream(self, *, stop_event: asyncio.Event) -> AsyncIterator[tuple[Event, Ack]]:
        """Yield ``(Event, Ack)`` pairs until ``stop_event`` is set.

        The method is an **async generator** — subclasses must use
        ``yield`` inside an ``async def`` decorated with no special marker
        (Python 3.10+ supports async generator ABCs natively via structural
        typing).

        Parameters
        ----------
        stop_event:
            When set, the generator must exit cleanly before the next
            ``yield``.  Callers may set this from a signal handler or a
            test harness.
        """
        ...  # pragma: no cover


__all__ = [
    "Ack",
    "EventSource",
    "_noop_ack",
]
