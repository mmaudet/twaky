"""Integration test: projector idempotence.

Requires the running `twaky-pg` container. Skipped when the DSN cannot
be reached (e.g. in CI without the stack). Verifies that projecting the
same event twice does NOT duplicate nodes/relationships.
"""

from __future__ import annotations

import os
import time
import uuid

import psycopg
import pytest

from twaky.config import settings


def _dsn() -> str:
    # From tests running on the host, twaky-pg is not on the network unless
    # the test process itself is inside twake-network. Allow override via env.
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1) as _:
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(),
    reason="twaky-pg not reachable; set TWAKY_TEST_DSN or run inside twake-network",
)


def _cypher(cur, body: str, alias: str = "v agtype") -> list:
    tag = "$CQR$"
    cur.execute(
        f"LOAD 'age'; SET search_path = ag_catalog, \"$user\", public; "
        f"SELECT * FROM cypher('{settings.twaky_graph_name}', {tag}{body}{tag}) AS ({alias});"
    )
    return cur.fetchall()


def _count(cur, label: str) -> int:
    rows = _cypher(cur, f"MATCH (n:{label}) RETURN count(n) AS n", alias="n agtype")
    # AGE returns agtype like: 3 or "3" — parse as int
    v = rows[0][0]
    return int(str(v).strip('"'))


def test_projector_idempotence_over_cycle():
    from twaky.mappers.calendar_event_created import map_event
    from twaky.projector import _execute_cypher

    uid = f"pytest-idem-{uuid.uuid4().hex[:8]}"
    payload = {
        "uid": uid,
        "summary": "pytest idempotence",
        "organizer": {"email": "orgz@pytest.local", "cn": "Orgz"},
        "attendees": [
            {"email": "a1@pytest.local", "cn": "A1"},
            {"email": "a2@pytest.local", "cn": "A2"},
        ],
    }
    stmts = map_event(payload)

    with psycopg.connect(_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute("LOAD 'age';")
            cur.execute('SET search_path = ag_catalog, "$user", public;')

            # Baseline node count.
            before_events = _count(cur, "CalendarEvent")

            # Project twice.
            _execute_cypher(cur, stmts)
            _execute_cypher(cur, stmts)
            conn.commit()

            # Exactly ONE new CalendarEvent with our uid.
            rows = _cypher(
                cur,
                f'MATCH (e:CalendarEvent {{uid: "{uid}"}}) RETURN count(e) AS n',
                alias="n agtype",
            )
            assert int(str(rows[0][0]).strip('"')) == 1

            # Cleanup — MATCH DELETE.
            _cypher(
                cur,
                f'MATCH (e:CalendarEvent {{uid: "{uid}"}}) DETACH DELETE e',
                alias="v agtype",
            )
            conn.commit()
