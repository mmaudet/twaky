"""LISTEN for `skill_changed` NOTIFYs and invalidate the registry cache.

The daemon spawns this as an asyncio task alongside the mission scheduler
and SP4's agent config listener. Coarse invalidation strategy per spec §5.1:
any NOTIFY clears all per-agent caches.

Mirrors the shape of SP4's ``twaky.agents.config_listener`` — same structure,
different channel name and different ``invalidate_all`` target.
"""

from __future__ import annotations

import asyncio
import logging

from twaky.config import settings
from twaky.daemon.notify import listen
from twaky.skills import registry

log = logging.getLogger("twaky.skills.config_listener")


async def run(stop_event: asyncio.Event) -> None:
    log.info("skill config listener starting")
    try:
        async for ch, payload in listen(["skill_changed"], settings.pg_dsn):
            if stop_event.is_set():
                return
            if ch == "skill_changed":
                log.info(
                    "skill changed (payload=%s), invalidating registry cache",
                    payload,
                )
                registry.invalidate_all()
    except asyncio.CancelledError:
        log.info("skill config listener cancelled")
        raise
    except Exception:
        log.exception("skill config listener crashed")
        raise


__all__ = ["run"]
