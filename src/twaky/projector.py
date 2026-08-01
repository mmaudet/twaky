"""event_log → AGE graph projector.

Polls `event_log` for `pending` rows, dispatches each to the mapper matching
its exchange, executes the generated Cypher MERGE statements against the
named graph via AGE, then marks the row `processed`. All MERGEs are keyed on
natural ids (email, event uid) so replaying the whole log is safe.

Runs as a long-lived worker (`twaky project`) or one-shot (`--once`).
"""

from __future__ import annotations

import logging
import signal
import time
from collections.abc import Iterable

import structlog

from twaky.config import settings
from twaky.db import get_pool
from twaky.mappers import get_mapper

logging.basicConfig(level=logging.INFO)
log = structlog.get_logger("twaky.projector")

_POLL_INTERVAL = 1.0  # seconds when no rows are pending


def _fetch_batch(batch_size: int) -> list[tuple[int, str, dict]]:
    """Lock and fetch a batch of pending rows."""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, exchange, payload
                FROM event_log
                WHERE status = 'pending'
                ORDER BY id
                LIMIT %s
                FOR UPDATE SKIP LOCKED
                """,
                (batch_size,),
            )
            rows = cur.fetchall()
        # We commit here (no update yet); rows are unlocked on commit.
        # To keep the row exclusive across mapping + graph write we
        # instead do everything in a single txn per row (see _process_row).
        conn.rollback()
    return [(r[0], r[1], r[2]) for r in rows]


_SAFE_TAG = "$CQR$"


def _execute_cypher(cur, stmts: Iterable[str]) -> None:
    """Run each MERGE via AGE cypher() function against the configured graph.

    AGE requires BOTH arguments of `cypher()` to be literals — a name
    constant for the graph, and a dollar-quoted string constant for the
    Cypher body. No SQL parameter placeholders are accepted. We inline
    both, with strict validation: the graph name must be alphanumeric,
    and the Cypher body must not contain our chosen dollar-quote tag.
    """
    graph_name = settings.twaky_graph_name
    if not graph_name.replace("_", "").isalnum():
        raise ValueError(f"unsafe graph name: {graph_name!r}")

    for stmt in stmts:
        wrapped = f"{stmt} RETURN 1"
        if _SAFE_TAG in wrapped:
            raise ValueError(f"cypher body contains reserved tag {_SAFE_TAG!r}")
        sql = (
            f"SELECT * FROM cypher('{graph_name}', "
            f"{_SAFE_TAG}{wrapped}{_SAFE_TAG}) "
            f"AS (v ag_catalog.agtype);"
        )
        cur.execute(sql)
        cur.fetchall()


def _process_row(row_id: int, exchange: str, payload: dict) -> tuple[bool, str | None]:
    mapper = get_mapper(exchange)
    if mapper is None:
        # No mapper yet for this exchange — mark processed with a note so we don't loop.
        with get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE event_log SET status='processed', processed_at=now(), "
                    "error='no mapper' WHERE id=%s AND status='pending'",
                    (row_id,),
                )
            conn.commit()
        return False, "no mapper"

    try:
        stmts = mapper(payload)
        with get_pool().connection() as conn:
            with conn.cursor() as cur:
                if stmts:
                    _execute_cypher(cur, stmts)
                cur.execute(
                    "UPDATE event_log SET status='processed', processed_at=now() "
                    "WHERE id=%s AND status='pending'",
                    (row_id,),
                )
            conn.commit()
        return True, None
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        log.exception("mapper/AGE failure", row_id=row_id, exchange=exchange)
        with get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE event_log SET status='error', processed_at=now(), error=%s "
                    "WHERE id=%s AND status='pending'",
                    (msg[:2000], row_id),
                )
            conn.commit()
        return False, msg


def run(batch_size: int = 50, once: bool = False) -> None:
    log.info(
        "starting projector",
        graph=settings.twaky_graph_name,
        batch=batch_size,
        once=once,
    )
    stop = False

    def _handle(sig, frame):
        nonlocal stop
        stop = True
        log.info("shutdown requested", sig=sig)

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    processed_total = 0
    while not stop:
        batch = _fetch_batch(batch_size)
        if not batch:
            if once:
                break
            time.sleep(_POLL_INTERVAL)
            continue

        for row_id, exchange, payload in batch:
            ok, err = _process_row(row_id, exchange, payload)
            processed_total += 1 if ok else 0
            if err:
                log.warning("row noted", row_id=row_id, note=err)
            else:
                log.info("projected", row_id=row_id, exchange=exchange)

        if once:
            break

    log.info("projector stopped", processed=processed_total)


def replay_from(from_id: int) -> int:
    """Mark rows >= from_id as pending again; returns count of updated rows."""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE event_log SET status='pending', processed_at=NULL, error=NULL "
                "WHERE id >= %s AND status IN ('processed','error')",
                (from_id,),
            )
            n = cur.rowcount
        conn.commit()
    log.info("replay marked", from_id=from_id, count=n)
    return n


__all__ = ["replay_from", "run"]
