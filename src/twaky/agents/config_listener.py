"""LISTEN for `agent_config_changed` NOTIFYs and invalidate the registry cache.

The daemon spawns this as an asyncio task alongside its mission scheduler.
Reuses the shared listen() helper from twaky.daemon.notify — same shape as
mission_declared / mission_resumed subscribers.
"""

from __future__ import annotations

import asyncio
import logging

from twaky.agents import registry
from twaky.config import settings
from twaky.daemon.notify import listen

log = logging.getLogger("twaky.agents.config_listener")


async def run(stop_event: asyncio.Event) -> None:
    """Long-running task: LISTEN agent_config_changed, invalidate on payload."""
    log.info("agent config listener starting")
    try:
        async for ch, payload in listen(["agent_config_changed"], settings.pg_dsn):
            if stop_event.is_set():
                return
            if ch == "agent_config_changed":
                log.info("invalidating agent cache for %s", payload)
                registry.invalidate(payload)
    except asyncio.CancelledError:
        log.info("agent config listener cancelled")
        raise
    except Exception:
        log.exception("agent config listener crashed")
        raise


__all__ = ["run"]
