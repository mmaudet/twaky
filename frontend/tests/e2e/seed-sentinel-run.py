"""Seed a sentinel_run row for E2E testing.

Inserts a completed run for the 'mail' sentinel with a fake event_ref
and a minimal trace. Prints {"id": ..., "event_ref": ...} as JSON.

The caller needs the event_ref, not just the id: runs accumulate in the
database, so a spec that locates its row by the shared "e2e-seed-" prefix
matches every previous run too and trips Playwright's strict mode on the
second execution.

Usage (from inside the twaky-api container):
    python /tmp/seed-sentinel-run.py
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from twaky.sentinels import repository


def seed_sentinel_run() -> dict[str, str]:
    event_ref = f"test.exchange::e2e-seed-{uuid.uuid4().hex[:12]}"
    started_at = datetime.now(tz=UTC)
    run = repository.insert_run(
        {
            "sentinel_name": "mail",
            "event_ref": event_ref,
            "outcome": "processed",
            "started_at": started_at,
            "llm_calls": 0,
            "trace": [
                {"node": "match_rules", "ts": started_at.isoformat()},
                {"node": "done", "ts": datetime.now(tz=UTC).isoformat()},
            ],
        }
    )
    repository.update_run(
        run.id,
        {
            "completed_at": datetime.now(tz=UTC),
            "duration_ms": 42,
        },
    )
    return {"id": str(run.id), "event_ref": event_ref}


if __name__ == "__main__":
    print(json.dumps(seed_sentinel_run()))
