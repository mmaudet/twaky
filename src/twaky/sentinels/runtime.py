"""SentinelRuntime — async dispatch loop for all enabled sentinels.

Architecture
------------
- One ``asyncio.Task`` per enabled sentinel runs ``_run_one``.
- ``_run_one`` opens the right ``EventSource`` via ``_build_source``,
  then spawns one ``_dispatch`` task per received event, bounded by a
  per-sentinel ``asyncio.Semaphore``.
- ``_process_with_bookkeeping`` handles idempotency, DB bookkeeping, timeout,
  and error capture for one event.
- A housekeeping task purges old ``sentinel_run`` rows hourly.
- A config-listener task invalidates the ``SentinelRegistry`` cache on
  ``sentinel_changed`` NOTIFY so config-toggle takes effect within seconds.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from twaky.oauth.config_listener import run_oauth_config_listener
from twaky.sentinels import repository
from twaky.sentinels.base import Context, Event, Outcome, Sentinel
from twaky.sentinels.config_listener import run_config_listener
from twaky.sentinels.delegation import Delegation
from twaky.sentinels.discovery import discover_sentinels
from twaky.sentinels.emitter import MissionEmitter
from twaky.sentinels.models import SentinelConfig
from twaky.sentinels.registry import get_registry
from twaky.sentinels.sources.base import Ack, EventSource

if TYPE_CHECKING:
    from twaky.config import Settings

log = logging.getLogger(__name__)

_ERROR_REPR_MAX = 8192  # bytes — truncate tracebacks at 8 KiB


class SentinelRuntime:
    """Discover sentinels, subscribe to their event sources, dispatch events.

    Parameters
    ----------
    settings:
        The ``Settings`` instance to use for DB DSN, RabbitMQ URL, JMAP
        config, and concurrency limits.
    """

    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings

    async def run(self, *, stop_event: asyncio.Event) -> None:
        """Start all sub-tasks, block until ``stop_event`` is set, then cancel.

        Steps
        -----
        1. Start the ``config_listener`` task — invalidates registry on NOTIFY.
        2. Start the ``housekeeping`` task — hourly purge of old runs.
        3. Discover sentinel classes via ``discovery.discover_sentinels()``.
        4. For each enabled sentinel in the registry:
           - Warn and skip if no matching class was discovered.
           - Instantiate the class.
           - Build a ``Context``.
           - Spawn a ``_run_one`` task.
        5. Await ``stop_event``.
        6. Cancel all tasks; swallow ``CancelledError`` / ``Exception`` on await.
        """
        settings = self._settings
        registry = get_registry()

        tasks: list[asyncio.Task] = []

        # 1. Config listener
        tasks.append(
            asyncio.create_task(
                run_config_listener(settings.pg_dsn, registry, stop_event=stop_event),
                name="sentinel:config_listener",
            )
        )

        # 1b. OAuth credential listener
        oauth_listener_task = asyncio.create_task(
            run_oauth_config_listener(self._settings.pg_dsn, stop_event=stop_event),
            name="oauth-config-listener",
        )
        tasks.append(oauth_listener_task)

        # 2. Housekeeping
        tasks.append(
            asyncio.create_task(
                _housekeeping(settings, stop_event),
                name="sentinel:housekeeping",
            )
        )

        # 3. Discover sentinel classes
        discovered: dict[str, type[Sentinel]] = discover_sentinels()
        log.info("discovered sentinel classes: %s", list(discovered.keys()))

        # 4. Spawn one task per enabled sentinel
        enabled_cfgs: list[SentinelConfig] = registry.list_enabled()
        for cfg in enabled_cfgs:
            cls = discovered.get(cfg.name)
            if cls is None:
                log.warning(
                    "sentinel DB row %r has no matching sub-package — skipping",
                    cfg.name,
                )
                continue

            inst = cls()
            ctx = _build_context(inst, cfg, settings)

            tasks.append(
                asyncio.create_task(
                    _run_one(inst, ctx, settings, stop_event),
                    name=f"sentinel:{cfg.name}",
                )
            )
            log.info(
                "sentinel %r started (source=%s)",
                cfg.name,
                cfg.config_values.get("event_source", inst.event_source_kind),
            )

        # 5. Block until stop
        await stop_event.wait()
        log.info("stop_event set — cancelling %d sentinel tasks", len(tasks))

        # 6. Cancel all tasks
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                log.debug(
                    "sentinel task %r exited with error on shutdown", t.get_name()
                )


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------


def _build_context(inst: Sentinel, cfg: SentinelConfig, settings: Settings) -> Context:
    """Build a ``Context`` for one sentinel instance.

    - ``db_pool`` is ``None`` at this stage; sentinels that need direct DB access
      can call ``twaky.db.get_pool()`` directly (T4/T5 helpers do this internally).
    - ``mission_emitter`` and ``delegation`` are pre-built helpers.
    - ``sentinel_row`` is the ``SentinelConfig`` from the registry.
    - ``logger`` is bound to ``twaky.sentinels.<name>``.
    """
    return Context(
        db_pool=None,
        mission_emitter=MissionEmitter(inst.name),
        delegation=Delegation(inst.name, settings.pg_dsn),
        sentinel_row=cfg,
        logger=logging.getLogger(f"twaky.sentinels.{inst.name}"),
    )


# ---------------------------------------------------------------------------
# _build_source
# ---------------------------------------------------------------------------


def _build_source(inst: Sentinel, ctx: Context, settings: Settings) -> EventSource:
    """Instantiate the correct ``EventSource`` for *inst*.

    The ``event_source`` key in ``ctx.sentinel_row.config_values`` takes
    precedence; falls back to ``inst.event_source_kind``.

    Raises
    ------
    ValueError
        If the resolved kind is neither ``"rabbitmq"`` nor ``"jmap_poll"``.
    """
    from twaky.sentinels.sources.jmap_poll import JmapPollingEventSource
    from twaky.sentinels.sources.rabbitmq import RabbitMQEventSource

    cfg: SentinelConfig = ctx.sentinel_row
    kind: str = cfg.config_values.get("event_source", inst.event_source_kind)

    if kind == "rabbitmq":
        bindings: list = cfg.config_values.get("bindings", [])
        return RabbitMQEventSource(
            sentinel_name=inst.name,
            rabbit_url=settings.rabbitmq_url,
            bindings=bindings,
        )

    if kind == "jmap_poll":
        return JmapPollingEventSource(
            sentinel_name=inst.name,
            session_url=settings.jmap_session_url,
            bearer_token=settings.jmap_bearer_token,
            account_email=settings.jmap_account_email,
            poll_interval_s=settings.jmap_poll_interval_s,
        )

    raise ValueError(
        f"Unknown event_source kind {kind!r} for sentinel {inst.name!r}. "
        "Supported: 'rabbitmq', 'jmap_poll'."
    )


# ---------------------------------------------------------------------------
# _run_one
# ---------------------------------------------------------------------------


async def _run_one(
    inst: Sentinel,
    ctx: Context,
    settings: Settings,
    stop_event: asyncio.Event,
) -> None:
    """Iterate the EventSource and spawn per-event dispatch tasks.

    One ``asyncio.Semaphore`` limits concurrent event processing for this
    sentinel.  Each event spawns a ``_dispatch`` task that acquires the
    semaphore internally — the iteration loop itself is never blocked.
    """
    sentinel_name = inst.name
    source = _build_source(inst, ctx, settings)
    sem = asyncio.Semaphore(settings.sentinel_max_concurrent_events)

    try:
        async for event, ack in source.stream(stop_event=stop_event):
            asyncio.create_task(
                _dispatch(inst, ctx, settings, event, ack, sem),
                name=f"dispatch:{sentinel_name}:{event['message_id']}",
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception(
            "sentinel %r stream raised an exception — stopping", sentinel_name
        )


async def _dispatch(
    inst: Sentinel,
    ctx: Context,
    settings: Settings,
    event: Event,
    ack: Ack,
    sem: asyncio.Semaphore,
) -> None:
    """Acquire the semaphore, run bookkeeping, ack on success."""
    async with sem:
        outcome = await _process_with_bookkeeping(inst, ctx, settings, event)
        if outcome is not Outcome.ERROR:
            await ack()


# ---------------------------------------------------------------------------
# _process_with_bookkeeping
# ---------------------------------------------------------------------------


async def _process_with_bookkeeping(
    inst: Sentinel,
    ctx: Context,
    settings: Settings,
    event: Event,
) -> Outcome:
    """Idempotency check → DB insert → run process() → update row.

    Returns the ``Outcome`` of this event.

    Idempotency
    -----------
    If a ``sentinel_run`` row already exists for the same ``event_ref`` in
    the last 24 hours, insert a fresh row marked ``ignored`` and return
    ``Outcome.IGNORED`` without calling ``process()``.

    Timeout
    -------
    ``asyncio.timeout`` wraps ``asyncio.to_thread(inst.process, event, ctx)``.
    A ``TimeoutError`` maps to ``Outcome.ERROR`` with the traceback in
    ``error_repr``.  Any other exception is also caught and mapped to
    ``Outcome.ERROR``.

    Ack policy (enforced by the caller ``_dispatch``)
    --------------------------------------------------
    The *ack* callable is NOT invoked here — ``_dispatch`` calls it only
    when outcome is not ERROR.

    Error retry policy — at-most-once (deliberate)
    -----------------------------------------------
    Sentinel errors are at-most-once.  A ``sentinel_run`` row with
    ``outcome='error'`` still counts as processed for idempotency: broker
    redelivery of the same event will be detected by ``find_run_by_event_ref``
    and marked ``ignored``.  This is deliberate — unbounded LLM retries would
    blow the cost budget and mask systemic failures.  Operators should monitor
    ``outcome='error'`` rows via the /sentinels API.  Changing to at-least-once
    (retry N times before permanent error) is deferred to SP6b.
    """
    sentinel_name = inst.name
    event_ref = f"{event['source_ref']}:{event['message_id']}"

    # ------------------------------------------------------------------
    # Idempotency check
    # ------------------------------------------------------------------
    existing = await asyncio.to_thread(
        repository.find_run_by_event_ref, sentinel_name, event_ref, 24
    )
    if existing is not None:
        log.debug(
            "sentinel %r: duplicate event_ref=%r — marking ignored",
            sentinel_name,
            event_ref,
        )
        ignored_run = await asyncio.to_thread(
            repository.insert_run,
            {
                "sentinel_name": sentinel_name,
                "event_ref": event_ref,
                "outcome": Outcome.IGNORED.value,
                "error_repr": "already_processed",
                "llm_calls": 0,
            },
        )
        await asyncio.to_thread(
            repository.update_run,
            ignored_run.id,
            {
                "completed_at": datetime.now(tz=UTC),
                "duration_ms": 0,
            },
        )
        return Outcome.IGNORED

    # ------------------------------------------------------------------
    # Insert a pending run row
    # ------------------------------------------------------------------
    started_at = datetime.now(tz=UTC)
    run = await asyncio.to_thread(
        repository.insert_run,
        {
            "sentinel_name": sentinel_name,
            "event_ref": event_ref,
            "outcome": Outcome.PROCESSED.value,  # optimistic; updated below
            "started_at": started_at,
            "llm_calls": 0,
        },
    )

    # ------------------------------------------------------------------
    # Run process() under timeout
    # ------------------------------------------------------------------
    outcome = Outcome.PROCESSED
    error_repr: str | None = None

    try:
        async with asyncio.timeout(float(settings.sentinel_timeout_s)):
            result: Outcome = await asyncio.to_thread(inst.process, event, ctx)
        outcome = result
    except TimeoutError:
        tb = traceback.format_exc()
        error_repr = tb[:_ERROR_REPR_MAX]
        outcome = Outcome.ERROR
        log.error(
            "sentinel %r: event_ref=%r timed out after %ds",
            sentinel_name,
            event_ref,
            settings.sentinel_timeout_s,
        )
    except Exception:
        tb = traceback.format_exc()
        error_repr = tb[:_ERROR_REPR_MAX]
        outcome = Outcome.ERROR
        log.exception(
            "sentinel %r: event_ref=%r raised exception",
            sentinel_name,
            event_ref,
        )

    # ------------------------------------------------------------------
    # Update the run row
    # ------------------------------------------------------------------
    completed_at = datetime.now(tz=UTC)
    duration_ms = int((completed_at - started_at).total_seconds() * 1000)

    patch: dict = {
        "outcome": outcome.value,
        "completed_at": completed_at,
        "duration_ms": duration_ms,
    }
    if error_repr is not None:
        patch["error_repr"] = error_repr

    await asyncio.to_thread(repository.update_run, run.id, patch)

    return outcome


# ---------------------------------------------------------------------------
# _housekeeping
# ---------------------------------------------------------------------------


async def _housekeeping(settings: Settings, stop_event: asyncio.Event) -> None:
    """Hourly housekeeping: purge old sentinel_run rows.

    Exits when ``stop_event`` is set.
    """
    # Style note — two timeout patterns are used intentionally in this file:
    # * asyncio.wait_for(stop_event.wait(), timeout=N) — used for interruptible
    #   sleeps where normal completion (timeout elapsed) is the common path.
    #   wait_for returns None on coroutine completion and raises TimeoutError
    #   when the timeout fires; the inverted semantics (timeout = "do work") map
    #   more clearly onto a "sleep until stop or N seconds" pattern.
    # * asyncio.timeout(N) context manager — used around process() calls where
    #   we want a hard deadline and the timeout path is the exceptional case.
    # Both are correct; mixing them avoids wrapping the common-path branch in an
    # extra try/except just to silence the expected TimeoutError.
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=3600.0)
            # stop_event fired — exit cleanly.
            return
        except TimeoutError:
            pass  # normal: one hour elapsed

        if stop_event.is_set():
            return

        try:
            deleted = await asyncio.to_thread(
                repository.purge_old_runs, settings.sentinel_run_retention_days
            )
            log.info("housekeeping: purged %d old sentinel_run rows", deleted)
        except Exception:
            log.exception("housekeeping: purge_old_runs failed")

        try:
            from twaky.sentinels.mail.store import memories as mail_memories

            expired = await asyncio.to_thread(mail_memories.purge_expired)
            log.info(
                "housekeeping: purged %d expired mail_sentinel_memory rows", expired
            )
        except Exception:
            log.exception("housekeeping: purge_expired failed")


__all__ = [
    "SentinelRuntime",
    "_build_context",
    "_build_source",
    "_dispatch",
    "_housekeeping",
    "_process_with_bookkeeping",
    "_run_one",
]
