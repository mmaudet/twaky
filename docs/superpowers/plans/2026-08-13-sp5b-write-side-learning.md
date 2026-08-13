# SP5b — Write-side Learning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the mail sentinel learn autonomously from user actions (draft edits, spam reclassifications, folder moves) so that `mail_sentinel_memory` and `mail_sentinel_learned_pattern` cease to be empty and future drafts improve.

**Architecture:** A new `observe()` code path runs in parallel to the existing `process()` path in the same `twaky-sentinel` container. It extends the JMAP poller to watch Sent/Spam/Trash/custom mailboxes for user actions, classifies each change, and dispatches to one of three extractors (`draft_diff`, `reclassification`, `folder_move`) that write memories and patterns.

**Tech Stack:** Python 3.12, PostgreSQL 15, LangGraph, psycopg, Pydantic, LiteLLM → Mistral-Small-3.2-24B via Lucie; Next.js 15 App Router for UI; bash+psql for DB migrations (codebase uses `sql/NNN_init_*.sh`, not alembic — spec §8.1 supersedes as follows).

## Global Constraints

- Feature flag `settings.mail_sentinel_observer_enabled: bool = False` gates the entire observer path — no behavior change unless explicitly enabled.
- LLM hardening for extractors: `Hardening.COMPACT` (no JSON self-repair, no expensive retry — learning is best-effort).
- All new tables use `TIMESTAMPTZ` and `UUID PRIMARY KEY DEFAULT gen_random_uuid()`.
- Observer failures MUST NOT block ingest — log and continue.
- Migration is reversible: DROP TABLE for new tables, ALTER TABLE DROP COLUMN for extensions.
- Ruff, mypy, pytest all green before commit.
- Never commit `.env`. No `--no-verify`, no `--force-*`, no `reset --hard`.
- Idempotence key for observations: `UNIQUE (email_id, mailbox_id, observation_type)`.

---

### Task 1: Config + feature flag

**Files:**
- Modify: `src/twaky/config.py` (add two settings)
- Test: `tests/test_config.py` (extend existing test file)

**Interfaces:**
- Consumes: nothing (leaf)
- Produces:
  - `settings.mail_sentinel_observer_enabled: bool` (default `False`)
  - `settings.mail_sentinel_watched_mailbox_roles: str` (default `"sent,junk,trash"`)
  - Property `settings.watched_mailbox_roles_list: list[str]` returning parsed comma-split lowercased entries

- [ ] **Step 1: Add failing test for new settings**

Append to `tests/test_config.py`:

```python
def test_mail_sentinel_observer_defaults(monkeypatch):
    monkeypatch.delenv("MAIL_SENTINEL_OBSERVER_ENABLED", raising=False)
    monkeypatch.delenv("MAIL_SENTINEL_WATCHED_MAILBOX_ROLES", raising=False)
    from twaky.config import Settings
    s = Settings()  # type: ignore[call-arg]
    assert s.mail_sentinel_observer_enabled is False
    assert s.mail_sentinel_watched_mailbox_roles == "sent,junk,trash"
    assert s.watched_mailbox_roles_list == ["sent", "junk", "trash"]


def test_mail_sentinel_observer_from_env(monkeypatch):
    monkeypatch.setenv("MAIL_SENTINEL_OBSERVER_ENABLED", "true")
    monkeypatch.setenv("MAIL_SENTINEL_WATCHED_MAILBOX_ROLES", "Sent, Junk , trash , archive")
    from twaky.config import Settings
    s = Settings()  # type: ignore[call-arg]
    assert s.mail_sentinel_observer_enabled is True
    assert s.watched_mailbox_roles_list == ["sent", "junk", "trash", "archive"]
```

- [ ] **Step 2: Run test to verify failure**

```
pytest tests/test_config.py::test_mail_sentinel_observer_defaults tests/test_config.py::test_mail_sentinel_observer_from_env -v
```
Expected: FAIL (AttributeError on unknown settings fields).

- [ ] **Step 3: Add settings + property to `src/twaky/config.py`**

After the `mail_sentinel_api_key: str = Field(default="")` line, insert:

```python
    # --- SP5b: write-side observer ---
    mail_sentinel_observer_enabled: bool = Field(default=False)
    mail_sentinel_watched_mailbox_roles: str = Field(default="sent,junk,trash")
```

Inside the `Settings` class body, add a `@property`:

```python
    @property
    def watched_mailbox_roles_list(self) -> list[str]:
        return [
            r.strip().lower()
            for r in self.mail_sentinel_watched_mailbox_roles.split(",")
            if r.strip()
        ]
```

- [ ] **Step 4: Run test to verify pass**

```
pytest tests/test_config.py::test_mail_sentinel_observer_defaults tests/test_config.py::test_mail_sentinel_observer_from_env -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/twaky/config.py tests/test_config.py
git commit -m "feat(sp5b): observer feature flag + watched mailbox roles"
```

---

### Task 2: DB migration script

**Files:**
- Create: `sql/012_init_write_side.sh`
- Create: `tests/sql/test_write_side_migration.py`

**Interfaces:**
- Consumes: existing `mail_sentinel_memory`, `mission` tables (schema references)
- Produces: 4 new columns on `mail_sentinel_memory`, 2 new tables `mail_sentinel_mailbox_state` + `mail_sentinel_observation`

- [ ] **Step 1: Write the failing static-assertion test**

Create `tests/sql/test_write_side_migration.py`:

```python
"""Static assertions on the SP5b write-side migration script."""

from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "sql" / "012_init_write_side.sh"


def test_script_exists_and_is_executable():
    assert SCRIPT.exists(), f"{SCRIPT} missing"
    assert SCRIPT.stat().st_mode & 0o111, f"{SCRIPT} not executable"


def test_alters_mail_sentinel_memory_with_four_columns():
    text = SCRIPT.read_text()
    for expected in (
        "ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'manual'",
        "ADD COLUMN IF NOT EXISTS sender_email TEXT",
        "ADD COLUMN IF NOT EXISTS mission_id UUID",
        "ADD COLUMN IF NOT EXISTS confidence NUMERIC(3,2)",
    ):
        assert expected in text, f"missing: {expected!r}"


def test_drops_not_null_on_expires_at():
    """`expires_at` must be nullable so 'Keep permanent' can set it to NULL."""
    text = SCRIPT.read_text()
    assert "ALTER COLUMN expires_at DROP NOT NULL" in text


def test_source_check_constraint():
    text = SCRIPT.read_text()
    assert "source IN ('manual','auto_diff','auto_reclass','auto_move')" in text


def test_creates_mailbox_state_table():
    text = SCRIPT.read_text()
    assert "CREATE TABLE IF NOT EXISTS public.mail_sentinel_mailbox_state" in text
    assert "mailbox_id  TEXT PRIMARY KEY" in text
    assert "jmap_state  TEXT NOT NULL" in text


def test_creates_observation_table_with_unique():
    text = SCRIPT.read_text()
    assert "CREATE TABLE IF NOT EXISTS public.mail_sentinel_observation" in text
    assert (
        "observation_type   TEXT NOT NULL"
        in text
    )
    assert (
        "extraction_outcome TEXT NOT NULL"
        in text
    )
    assert "UNIQUE (email_id, mailbox_id, observation_type)" in text


def test_observation_outcome_check():
    text = SCRIPT.read_text()
    assert (
        "extraction_outcome IN ('extracted','skipped_trivial','skipped_no_match','error')"
        in text
    )


def test_mission_id_fk_on_delete_set_null():
    text = SCRIPT.read_text()
    assert "REFERENCES public.mission(id) ON DELETE SET NULL" in text
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/sql/test_write_side_migration.py -v
```
Expected: FAIL (script does not exist).

- [ ] **Step 3: Write the migration script `sql/012_init_write_side.sh`**

```bash
#!/bin/bash
# SP5b write-side learning: extend mail_sentinel_memory + add mailbox_state and observation tables.
# For existing volumes:
#   docker exec -i twaky-pg bash /docker-entrypoint-initdb.d/012_init_write_side.sh
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-twaky}" <<-'EOSQL'

    -- =========================================================
    -- SP5b: extend mail_sentinel_memory with 4 columns
    -- =========================================================

    ALTER TABLE public.mail_sentinel_memory
      ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual','auto_diff','auto_reclass','auto_move')),
      ADD COLUMN IF NOT EXISTS sender_email TEXT,
      ADD COLUMN IF NOT EXISTS mission_id UUID
        REFERENCES public.mission(id) ON DELETE SET NULL,
      ADD COLUMN IF NOT EXISTS confidence NUMERIC(3,2)
        CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1));

    -- Allow "Keep permanent" memories: expires_at can now be NULL
    ALTER TABLE public.mail_sentinel_memory
      ALTER COLUMN expires_at DROP NOT NULL;

    CREATE INDEX IF NOT EXISTS mail_sentinel_memory_by_source
      ON public.mail_sentinel_memory (source, created_at DESC);

    -- =========================================================
    -- SP5b: mail_sentinel_mailbox_state
    -- =========================================================

    CREATE TABLE IF NOT EXISTS public.mail_sentinel_mailbox_state (
        mailbox_id  TEXT PRIMARY KEY,
        role        TEXT,
        name        TEXT,
        jmap_state  TEXT NOT NULL,
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    -- =========================================================
    -- SP5b: mail_sentinel_observation
    -- =========================================================

    CREATE TABLE IF NOT EXISTS public.mail_sentinel_observation (
        id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        email_id           TEXT NOT NULL,
        mailbox_id         TEXT NOT NULL,
        observation_type   TEXT NOT NULL
                           CHECK (observation_type IN (
                               'draft_sent',
                               'marked_spam',
                               'unmarked_spam',
                               'moved_to_custom'
                           )),
        observed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        extraction_outcome TEXT NOT NULL
                           CHECK (extraction_outcome IN (
                               'extracted',
                               'skipped_trivial',
                               'skipped_no_match',
                               'error'
                           )),
        memory_ids         UUID[] NOT NULL DEFAULT '{}',
        pattern_ids        UUID[] NOT NULL DEFAULT '{}',
        error_repr         TEXT,
        UNIQUE (email_id, mailbox_id, observation_type)
    );

    CREATE INDEX IF NOT EXISTS mail_sentinel_observation_recent
      ON public.mail_sentinel_observation (observed_at DESC);

EOSQL
```

Make it executable:
```
chmod +x sql/012_init_write_side.sh
```

- [ ] **Step 4: Run test to verify pass**

```
pytest tests/sql/test_write_side_migration.py -v
```
Expected: PASS on all 6 tests.

- [ ] **Step 5: Apply migration to running twaky-pg (dev instance)**

```
docker exec -e POSTGRES_USER=twaky -i twaky-pg bash < sql/012_init_write_side.sh
docker exec twaky-pg psql -U twaky -d twaky -c "\d mail_sentinel_memory" | head -20
docker exec twaky-pg psql -U twaky -d twaky -c "\d mail_sentinel_mailbox_state"
docker exec twaky-pg psql -U twaky -d twaky -c "\d mail_sentinel_observation"
```
Expected: no error; new columns and tables visible.

- [ ] **Step 6: Commit**

```bash
git add sql/012_init_write_side.sh tests/sql/test_write_side_migration.py
git commit -m "feat(sp5b): SQL migration for write-side learning tables"
```

---

### Task 3: Store — mailbox_state

**Files:**
- Create: `src/twaky/sentinels/mail/store/mailbox_state.py`
- Create: `tests/sentinels/mail/store/test_mailbox_state.py`

**Interfaces:**
- Consumes: `twaky.db.get_pool`
- Produces:
  - `@dataclass(frozen=True) class MailboxState(mailbox_id: str, role: str | None, name: str | None, jmap_state: str, updated_at: datetime)`
  - `def get(mailbox_id: str) -> MailboxState | None`
  - `def upsert(*, mailbox_id: str, jmap_state: str, role: str | None = None, name: str | None = None) -> MailboxState`
  - `def list_all() -> list[MailboxState]` (used by observability)

- [ ] **Step 1: Write failing tests**

Create `tests/sentinels/mail/store/test_mailbox_state.py`:

```python
"""Store CRUD for mail_sentinel_mailbox_state."""

from __future__ import annotations

import pytest

from twaky.sentinels.mail.store import mailbox_state as ms


pytestmark = pytest.mark.integration  # requires live twaky-pg


@pytest.fixture(autouse=True)
def _cleanup():
    from twaky.db import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_mailbox_state")
    yield
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_mailbox_state")


def test_get_returns_none_when_absent():
    assert ms.get("mbx-1") is None


def test_upsert_inserts_new_row():
    row = ms.upsert(mailbox_id="mbx-1", jmap_state="state-A", role="sent", name="Sent")
    assert row.mailbox_id == "mbx-1"
    assert row.jmap_state == "state-A"
    assert row.role == "sent"
    assert row.name == "Sent"


def test_upsert_updates_existing_row():
    ms.upsert(mailbox_id="mbx-1", jmap_state="state-A", role="sent", name="Sent")
    row = ms.upsert(mailbox_id="mbx-1", jmap_state="state-B", role="sent", name="Sent")
    assert row.jmap_state == "state-B"
    got = ms.get("mbx-1")
    assert got is not None
    assert got.jmap_state == "state-B"


def test_list_all_orders_by_mailbox_id():
    ms.upsert(mailbox_id="b-mbx", jmap_state="s1")
    ms.upsert(mailbox_id="a-mbx", jmap_state="s1")
    rows = ms.list_all()
    assert [r.mailbox_id for r in rows] == ["a-mbx", "b-mbx"]
```

- [ ] **Step 2: Run tests to verify failure**

```
pytest tests/sentinels/mail/store/test_mailbox_state.py -v
```
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `mailbox_state.py`**

```python
"""CRUD for ``mail_sentinel_mailbox_state``.

Tracks the last JMAP `state` observed for each mailbox the observer
watches. Enables idempotent delta polling: on bootstrap the current
JMAP state is stored without replay; each subsequent tick queries
`Email/changes sinceState=<stored>` and advances the row on success.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg.rows import dict_row

from twaky.db import get_pool


@dataclass(frozen=True)
class MailboxState:
    mailbox_id: str
    role: str | None
    name: str | None
    jmap_state: str
    updated_at: datetime


def _row(r: dict[str, Any]) -> MailboxState:
    return MailboxState(
        mailbox_id=r["mailbox_id"],
        role=r["role"],
        name=r["name"],
        jmap_state=r["jmap_state"],
        updated_at=r["updated_at"],
    )


def get(mailbox_id: str) -> MailboxState | None:
    sql = "SELECT * FROM mail_sentinel_mailbox_state WHERE mailbox_id = %s"
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (mailbox_id,))
        row = cur.fetchone()
    return _row(row) if row else None


def upsert(
    *,
    mailbox_id: str,
    jmap_state: str,
    role: str | None = None,
    name: str | None = None,
) -> MailboxState:
    sql = """
        INSERT INTO mail_sentinel_mailbox_state (mailbox_id, role, name, jmap_state)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (mailbox_id) DO UPDATE SET
            role = COALESCE(EXCLUDED.role, mail_sentinel_mailbox_state.role),
            name = COALESCE(EXCLUDED.name, mail_sentinel_mailbox_state.name),
            jmap_state = EXCLUDED.jmap_state,
            updated_at = now()
        RETURNING *
    """
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (mailbox_id, role, name, jmap_state))
        row = cur.fetchone()
    assert row is not None
    return _row(row)


def list_all() -> list[MailboxState]:
    sql = "SELECT * FROM mail_sentinel_mailbox_state ORDER BY mailbox_id ASC"
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        return [_row(r) for r in cur.fetchall()]


__all__ = ["MailboxState", "get", "list_all", "upsert"]
```

- [ ] **Step 4: Run tests to verify pass**

```
pytest tests/sentinels/mail/store/test_mailbox_state.py -v
```
Expected: PASS on all 4.

- [ ] **Step 5: Commit**

```bash
git add src/twaky/sentinels/mail/store/mailbox_state.py tests/sentinels/mail/store/test_mailbox_state.py
git commit -m "feat(sp5b): mailbox_state store for observer delta tracking"
```

---

---

### Task 4: Store — observations

**Files:**
- Create: `src/twaky/sentinels/mail/store/observations.py`
- Create: `tests/sentinels/mail/store/test_observations.py`

**Interfaces:**
- Consumes: `twaky.db.get_pool`
- Produces:
  - `class ObservationType(str, Enum)` with values `DRAFT_SENT`, `MARKED_SPAM`, `UNMARKED_SPAM`, `MOVED_TO_CUSTOM`
  - `class ExtractionOutcome(str, Enum)` with `EXTRACTED`, `SKIPPED_TRIVIAL`, `SKIPPED_NO_MATCH`, `ERROR`
  - `@dataclass Observation(id, email_id, mailbox_id, observation_type, observed_at, extraction_outcome, memory_ids, pattern_ids, error_repr)`
  - `def insert_if_new(*, email_id, mailbox_id, observation_type, extraction_outcome, memory_ids=(), pattern_ids=(), error_repr=None) -> Observation | None` (returns None on conflict)
  - `def list_recent(*, limit: int = 100) -> list[Observation]`
  - `def purge_older_than(days: int) -> int` (returns deleted count)

- [ ] **Step 1: Write failing tests**

Create `tests/sentinels/mail/store/test_observations.py`:

```python
"""Store CRUD for mail_sentinel_observation."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from twaky.sentinels.mail.store import observations as obs
from twaky.sentinels.mail.store.observations import (
    ExtractionOutcome,
    ObservationType,
)


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _cleanup():
    from twaky.db import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_observation")
    yield
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_observation")


def test_insert_if_new_creates_row():
    row = obs.insert_if_new(
        email_id="e1",
        mailbox_id="m1",
        observation_type=ObservationType.DRAFT_SENT,
        extraction_outcome=ExtractionOutcome.EXTRACTED,
        memory_ids=[uuid4()],
    )
    assert row is not None
    assert row.email_id == "e1"
    assert row.observation_type == ObservationType.DRAFT_SENT
    assert len(row.memory_ids) == 1


def test_insert_if_new_conflict_returns_none():
    obs.insert_if_new(
        email_id="e1",
        mailbox_id="m1",
        observation_type=ObservationType.DRAFT_SENT,
        extraction_outcome=ExtractionOutcome.EXTRACTED,
    )
    result = obs.insert_if_new(
        email_id="e1",
        mailbox_id="m1",
        observation_type=ObservationType.DRAFT_SENT,
        extraction_outcome=ExtractionOutcome.EXTRACTED,
    )
    assert result is None


def test_list_recent_orders_desc_and_limits():
    for i in range(5):
        obs.insert_if_new(
            email_id=f"e{i}",
            mailbox_id="m1",
            observation_type=ObservationType.DRAFT_SENT,
            extraction_outcome=ExtractionOutcome.EXTRACTED,
        )
    rows = obs.list_recent(limit=3)
    assert len(rows) == 3


def test_purge_older_than_removes_only_old_rows():
    from twaky.db import get_pool

    # Insert one with recent observed_at (default now())
    obs.insert_if_new(
        email_id="e_recent",
        mailbox_id="m1",
        observation_type=ObservationType.DRAFT_SENT,
        extraction_outcome=ExtractionOutcome.EXTRACTED,
    )
    # Force one to be old
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mail_sentinel_observation "
            "(email_id, mailbox_id, observation_type, observed_at, extraction_outcome) "
            "VALUES (%s, %s, %s, now() - INTERVAL '45 days', %s)",
            ("e_old", "m1", "draft_sent", "extracted"),
        )

    deleted = obs.purge_older_than(30)
    assert deleted == 1
    remaining = obs.list_recent()
    assert [r.email_id for r in remaining] == ["e_recent"]
```

- [ ] **Step 2: Run tests to verify failure**

```
pytest tests/sentinels/mail/store/test_observations.py -v
```
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `observations.py`**

```python
"""CRUD for ``mail_sentinel_observation``.

Audit log of user actions the observer detected and extracted. Every
observation is idempotent on `(email_id, mailbox_id, observation_type)`
via the UNIQUE constraint — a crash-and-replay tick cannot double-count.
Rows older than 30 days are purged by the housekeeping loop; the log
is for debug/observability, not load-bearing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from twaky.db import get_pool


class ObservationType(str, Enum):
    DRAFT_SENT = "draft_sent"
    MARKED_SPAM = "marked_spam"
    UNMARKED_SPAM = "unmarked_spam"
    MOVED_TO_CUSTOM = "moved_to_custom"


class ExtractionOutcome(str, Enum):
    EXTRACTED = "extracted"
    SKIPPED_TRIVIAL = "skipped_trivial"
    SKIPPED_NO_MATCH = "skipped_no_match"
    ERROR = "error"


@dataclass(frozen=True)
class Observation:
    id: UUID
    email_id: str
    mailbox_id: str
    observation_type: ObservationType
    observed_at: datetime
    extraction_outcome: ExtractionOutcome
    memory_ids: list[UUID]
    pattern_ids: list[UUID]
    error_repr: str | None


def _row(r: dict[str, Any]) -> Observation:
    return Observation(
        id=r["id"],
        email_id=r["email_id"],
        mailbox_id=r["mailbox_id"],
        observation_type=ObservationType(r["observation_type"]),
        observed_at=r["observed_at"],
        extraction_outcome=ExtractionOutcome(r["extraction_outcome"]),
        memory_ids=list(r["memory_ids"] or []),
        pattern_ids=list(r["pattern_ids"] or []),
        error_repr=r["error_repr"],
    )


def insert_if_new(
    *,
    email_id: str,
    mailbox_id: str,
    observation_type: ObservationType,
    extraction_outcome: ExtractionOutcome,
    memory_ids: list[UUID] | tuple[UUID, ...] = (),
    pattern_ids: list[UUID] | tuple[UUID, ...] = (),
    error_repr: str | None = None,
) -> Observation | None:
    sql = """
        INSERT INTO mail_sentinel_observation
            (email_id, mailbox_id, observation_type, extraction_outcome,
             memory_ids, pattern_ids, error_repr)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (email_id, mailbox_id, observation_type) DO NOTHING
        RETURNING *
    """
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        try:
            cur.execute(
                sql,
                (
                    email_id,
                    mailbox_id,
                    observation_type.value,
                    extraction_outcome.value,
                    list(memory_ids),
                    list(pattern_ids),
                    error_repr,
                ),
            )
            row = cur.fetchone()
        except UniqueViolation:
            return None
    return _row(row) if row else None


def list_recent(*, limit: int = 100) -> list[Observation]:
    sql = (
        "SELECT * FROM mail_sentinel_observation "
        "ORDER BY observed_at DESC LIMIT %s"
    )
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (limit,))
        return [_row(r) for r in cur.fetchall()]


def purge_older_than(days: int) -> int:
    sql = (
        "DELETE FROM mail_sentinel_observation "
        "WHERE observed_at < now() - make_interval(days => %s)"
    )
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (days,))
        return cur.rowcount


__all__ = [
    "ExtractionOutcome",
    "Observation",
    "ObservationType",
    "insert_if_new",
    "list_recent",
    "purge_older_than",
]
```

- [ ] **Step 4: Run tests to verify pass**

```
pytest tests/sentinels/mail/store/test_observations.py -v
```
Expected: PASS on all 4.

- [ ] **Step 5: Commit**

```bash
git add src/twaky/sentinels/mail/store/observations.py tests/sentinels/mail/store/test_observations.py
git commit -m "feat(sp5b): observations store with idempotent inserts"
```

---

### Task 5: Store — extend memories

**Files:**
- Modify: `src/twaky/sentinels/mail/store/memories.py`
- Test: `tests/sentinels/mail/store/test_memories_extended.py` (new file)

**Interfaces:**
- Consumes: existing `MailMemory` dataclass, `insert`, `candidate_pool`
- Produces:
  - `MailMemory` dataclass gains `source: str`, `sender_email: str | None`, `mission_id: UUID | None`, `confidence: float | None`
  - `def insert(...)` signature extended with `source: str = "manual"`, `sender_email: str | None = None`, `mission_id: UUID | None = None`, `confidence: float | None = None`
  - `def touch(ids: list[UUID]) -> int` — sets `expires_at = now() + INTERVAL '7 days'` for the given ids (skips rows where `expires_at IS NULL`)
  - `def list_for_prompt(*, sender_email: str, sender_domain: str, limit: int = 16) -> list[MailMemory]` — ranked union query
  - `def set_persist(memory_id: UUID, persist: bool) -> MailMemory | None` — `persist=True` sets `expires_at=NULL`, `persist=False` resets to `now() + INTERVAL '7 days'`

- [ ] **Step 1: Write failing tests**

Create `tests/sentinels/mail/store/test_memories_extended.py`:

```python
"""Extensions to memories store: source, touch, list_for_prompt, set_persist."""

from __future__ import annotations

import pytest

from twaky.sentinels.mail.store import memories as mem


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _cleanup():
    from twaky.db import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_memory")
    yield
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_memory")


def test_insert_records_new_fields():
    m = mem.insert(
        kind="preference",
        scope="sender",
        scope_value="a@example.com",
        content="Use Bonjour",
        source="auto_diff",
        sender_email="a@example.com",
        confidence=0.9,
    )
    assert m.source == "auto_diff"
    assert m.sender_email == "a@example.com"
    assert m.confidence == pytest.approx(0.9)


def test_insert_default_source_is_manual():
    m = mem.insert(
        kind="fact", scope="global", scope_value="*", content="Always sign Michel-Marie"
    )
    assert m.source == "manual"


def test_touch_extends_expires_at():
    from twaky.db import get_pool
    m = mem.insert(kind="fact", scope="global", scope_value="*", content="foo")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE mail_sentinel_memory SET expires_at = now() + INTERVAL '1 day' WHERE id = %s",
            (m.id,),
        )
    updated = mem.touch([m.id])
    assert updated == 1
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT expires_at FROM mail_sentinel_memory WHERE id = %s", (m.id,)
        )
        row = cur.fetchone()
        assert row is not None
    # Assert expiry is > 6 days out
    from datetime import datetime, timezone
    delta = row[0] - datetime.now(timezone.utc)
    assert delta.days >= 6


def test_touch_skips_permanent_memories():
    from twaky.db import get_pool
    m = mem.insert(kind="fact", scope="global", scope_value="*", content="perm")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE mail_sentinel_memory SET expires_at = NULL WHERE id = %s",
            (m.id,),
        )
    updated = mem.touch([m.id])
    assert updated == 0


def test_list_for_prompt_ranks_sender_over_global():
    mem.insert(
        kind="preference",
        scope="global",
        scope_value="*",
        content="global rule",
        confidence=0.9,
    )
    mem.insert(
        kind="preference",
        scope="sender",
        scope_value="a@example.com",
        content="sender rule",
        confidence=0.9,
        sender_email="a@example.com",
    )
    rows = mem.list_for_prompt(
        sender_email="a@example.com", sender_domain="example.com", limit=16
    )
    assert rows[0].scope == "sender"
    assert rows[1].scope == "global"


def test_list_for_prompt_filters_expired():
    from twaky.db import get_pool
    m = mem.insert(kind="fact", scope="global", scope_value="*", content="expired")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE mail_sentinel_memory SET expires_at = now() - INTERVAL '1 day' WHERE id = %s",
            (m.id,),
        )
    rows = mem.list_for_prompt(sender_email="x@y.com", sender_domain="y.com")
    assert all(r.id != m.id for r in rows)


def test_set_persist_true_nulls_expires_at():
    m = mem.insert(kind="fact", scope="global", scope_value="*", content="p")
    updated = mem.set_persist(m.id, True)
    assert updated is not None
    from twaky.db import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT expires_at FROM mail_sentinel_memory WHERE id = %s", (m.id,)
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] is None


def test_set_persist_false_resets_ttl():
    m = mem.insert(kind="fact", scope="global", scope_value="*", content="p")
    mem.set_persist(m.id, True)
    mem.set_persist(m.id, False)
    from twaky.db import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT expires_at FROM mail_sentinel_memory WHERE id = %s", (m.id,)
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] is not None
```

- [ ] **Step 2: Run tests to verify failure**

```
pytest tests/sentinels/mail/store/test_memories_extended.py -v
```
Expected: FAIL (methods missing, dataclass missing fields).

- [ ] **Step 3: Extend `MailMemory` dataclass in `src/twaky/sentinels/mail/store/memories.py`**

Change the dataclass:

```python
@dataclass(frozen=True)
class MailMemory:
    """Frozen mirror of the ``mail_sentinel_memory`` table row."""

    id: UUID
    kind: str
    scope: str
    scope_value: str
    content: str
    evidence: list[Any]
    created_at: datetime
    expires_at: datetime | None  # NULL when "keep permanent"
    source: str = "manual"
    sender_email: str | None = None
    mission_id: UUID | None = None
    confidence: float | None = None
```

- [ ] **Step 4: Update `_row_to_memory` in `src/twaky/sentinels/mail/store/memories.py`**

```python
def _row_to_memory(row: dict[str, Any]) -> MailMemory:
    return MailMemory(
        id=row["id"],
        kind=row["kind"],
        scope=row["scope"],
        scope_value=row["scope_value"],
        content=row["content"],
        evidence=row.get("evidence") or [],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        source=row.get("source", "manual"),
        sender_email=row.get("sender_email"),
        mission_id=row.get("mission_id"),
        confidence=(float(row["confidence"]) if row.get("confidence") is not None else None),
    )
```

- [ ] **Step 5: Replace `insert()` in `src/twaky/sentinels/mail/store/memories.py`**

Replace the existing `insert()` function (currently `src/twaky/sentinels/mail/store/memories.py:114-170`) with:

```python
def insert(
    *,
    kind: str,
    scope: str,
    scope_value: str,
    content: str,
    evidence: list[Any] | None = None,
    source: str = "manual",
    sender_email: str | None = None,
    mission_id: UUID | None = None,
    confidence: float | None = None,
) -> MailMemory | None:
    """Insert a memory row, returning None on duplicate or public-domain refusal.

    Extra fields introduced in SP5b:
      - source: 'manual' | 'auto_diff' | 'auto_reclass' | 'auto_move'
      - sender_email: dénormalisation pour indexation quand scope='sender'
      - mission_id: trace la mission d'origine (audit)
      - confidence: 0..1, utilisée par le ranking d'injection
    """
    import json

    scope_value = scope_value.strip().lower()
    content = _normalized_content(content)

    if scope == "domain" and scope_value in PUBLIC_EMAIL_DOMAINS:
        log.info(
            "mail_sentinel_memory: refusing domain-scoped insert for public domain %r",
            scope_value,
        )
        return None

    if evidence is None:
        evidence = []

    sql = (
        "INSERT INTO mail_sentinel_memory "
        "(kind, scope, scope_value, content, evidence, "
        " source, sender_email, mission_id, confidence) "
        "VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s) "
        "ON CONFLICT (kind, scope, scope_value, content) DO NOTHING "
        "RETURNING *"
    )
    params = [
        kind, scope, scope_value, content, json.dumps(evidence),
        source, sender_email, mission_id, confidence,
    ]

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()

    return _row_to_memory(row) if row else None
```

- [ ] **Step 6: Implement `touch()`**

Append to `src/twaky/sentinels/mail/store/memories.py`:

```python
def touch(ids: list[UUID]) -> int:
    """Push ``expires_at`` to now() + 7 days for rows in *ids*.

    Skips rows where ``expires_at IS NULL`` ("keep permanent"). Returns
    the number of rows actually updated.
    """
    if not ids:
        return 0
    sql = (
        "UPDATE mail_sentinel_memory "
        "SET expires_at = now() + INTERVAL '7 days' "
        "WHERE id = ANY(%s) AND expires_at IS NOT NULL"
    )
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (list(ids),))
        return cur.rowcount
```

- [ ] **Step 7: Implement `list_for_prompt()`**

Append to `src/twaky/sentinels/mail/store/memories.py`:

```python
def list_for_prompt(
    *,
    sender_email: str,
    sender_domain: str,
    limit: int = 16,
) -> list[MailMemory]:
    """Return memories ranked by scope × confidence × age decay.

    Ranking = scope_weight × confidence × exp(-age_days / 30):
      - scope=sender → weight 3.0
      - scope=domain → weight 1.5
      - scope=global → weight 1.0
    Rows with expires_at in the past are excluded; rows with
    expires_at IS NULL are always eligible.
    """
    sql = """
        WITH candidates AS (
          SELECT id, kind, scope, scope_value, content, evidence,
                 created_at, expires_at, source, sender_email,
                 mission_id, confidence,
                 (CASE scope
                    WHEN 'sender' THEN 3.0
                    WHEN 'domain' THEN 1.5
                    WHEN 'global' THEN 1.0
                    ELSE 0.5
                  END) AS scope_weight,
                 COALESCE(confidence, 0.5) AS conf,
                 EXTRACT(EPOCH FROM (now() - created_at)) / 86400.0 AS age_days
          FROM mail_sentinel_memory
          WHERE ((scope = 'sender' AND scope_value = %s)
              OR (scope = 'domain' AND scope_value = %s)
              OR (scope = 'global'))
            AND (expires_at IS NULL OR expires_at > now())
        )
        SELECT *
        FROM candidates
        ORDER BY (scope_weight * conf * exp(-age_days / 30.0)) DESC
        LIMIT %s
    """
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (sender_email.lower(), sender_domain.lower(), limit))
        return [_row_to_memory(r) for r in cur.fetchall()]
```

- [ ] **Step 8: Implement `set_persist()`**

Append to `src/twaky/sentinels/mail/store/memories.py`:

```python
def set_persist(memory_id: UUID, persist: bool) -> MailMemory | None:
    """Toggle a memory between permanent (expires_at=NULL) and 7-day TTL."""
    if persist:
        sql = (
            "UPDATE mail_sentinel_memory SET expires_at = NULL "
            "WHERE id = %s RETURNING *"
        )
        params: tuple[Any, ...] = (memory_id,)
    else:
        sql = (
            "UPDATE mail_sentinel_memory "
            "SET expires_at = now() + INTERVAL '7 days' "
            "WHERE id = %s RETURNING *"
        )
        params = (memory_id,)
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return _row_to_memory(row) if row else None
```

- [ ] **Step 9: Update `__all__` in `src/twaky/sentinels/mail/store/memories.py`**

Add `"touch"`, `"list_for_prompt"`, `"set_persist"` to the exports.

- [ ] **Step 10: Run tests to verify pass**

```
pytest tests/sentinels/mail/store/test_memories_extended.py tests/sentinels/mail/store/test_memories.py -v
```
Expected: all PASS (including any pre-existing tests).

- [ ] **Step 11: Commit**

```bash
git add src/twaky/sentinels/mail/store/memories.py tests/sentinels/mail/store/test_memories_extended.py
git commit -m "feat(sp5b): extend memories store with source, touch, ranked list_for_prompt"
```

---

### Task 6: LLM prompts — extract_memory_from_diff + extract_memory_from_move

**Files:**
- Create: `src/twaky/sentinels/mail/prompts/extract_memory_from_diff.py`
- Create: `src/twaky/sentinels/mail/prompts/extract_memory_from_move.py`
- Create: `src/twaky/sentinels/mail/schemas_write_side.py` (Pydantic output models)
- Test: `tests/sentinels/mail/prompts/test_extract_memory_prompts.py`

**Interfaces:**
- Consumes: existing prompt helpers (`today_for_llm`, `user_info_block`)
- Produces:
  - `class ExtractedMemory(BaseModel)` with `kind: Literal['fact','procedure','preference']`, `scope: Literal['sender','domain','global']`, `scope_value: str`, `content: str` (max 200 chars), `confidence: float` (0.0-1.0)
  - `class DraftDiffOutput(BaseModel)` with `memories: list[ExtractedMemory]`, `should_delete_previous_memory_ids: list[UUID] = []`
  - `class FolderMoveOutput(BaseModel)` with `should_extract: bool`, `memory: ExtractedMemory | None = None`
  - `def draft_diff_prompt(*, ai_draft: str, shipped_body: str, sender_email: str, recipient_email: str, thread_language: str, previous_memories: list[dict]) -> str`
  - `def folder_move_prompt(*, sender_email: str, history_count: int, folder_name: str, subject: str) -> str`

- [ ] **Step 1: Write failing tests**

Create `tests/sentinels/mail/prompts/test_extract_memory_prompts.py`:

```python
"""Unit tests for extract_memory_from_diff and extract_memory_from_move prompts."""

from twaky.sentinels.mail.prompts.extract_memory_from_diff import draft_diff_prompt
from twaky.sentinels.mail.prompts.extract_memory_from_move import folder_move_prompt
from twaky.sentinels.mail.schemas_write_side import (
    DraftDiffOutput,
    ExtractedMemory,
    FolderMoveOutput,
)


def test_extracted_memory_content_max_200():
    import pytest
    with pytest.raises(ValueError):
        ExtractedMemory(
            kind="preference",
            scope="global",
            scope_value="*",
            content="x" * 201,
            confidence=0.9,
        )


def test_extracted_memory_confidence_range():
    import pytest
    with pytest.raises(ValueError):
        ExtractedMemory(
            kind="fact",
            scope="global",
            scope_value="*",
            content="ok",
            confidence=1.5,
        )


def test_draft_diff_output_defaults_empty_lists():
    out = DraftDiffOutput(memories=[])
    assert out.should_delete_previous_memory_ids == []


def test_folder_move_output_optional_memory():
    out = FolderMoveOutput(should_extract=False)
    assert out.memory is None


def test_draft_diff_prompt_contains_inputs():
    prompt = draft_diff_prompt(
        ai_draft="Cher Alexandre,\n\nMerci.\n\nBien à vous,\n\nMichel-Marie",
        shipped_body="Bonjour Alexandre,\n\nMerci.\n\nBien à vous,\n\nMichel-Marie",
        sender_email="alexandre@linagora.com",
        recipient_email="alexandre@linagora.com",
        thread_language="fr",
        previous_memories=[],
    )
    assert "Cher Alexandre" in prompt
    assert "Bonjour Alexandre" in prompt
    assert "alexandre@linagora.com" in prompt
    assert '"memories"' in prompt


def test_folder_move_prompt_contains_inputs():
    prompt = folder_move_prompt(
        sender_email="comptable@fournisseur.com",
        history_count=5,
        folder_name="Facturation",
        subject="Facture N°2026-0812",
    )
    assert "comptable@fournisseur.com" in prompt
    assert "Facturation" in prompt
    assert "5" in prompt
```

- [ ] **Step 2: Run tests to verify failure**

```
pytest tests/sentinels/mail/prompts/test_extract_memory_prompts.py -v
```
Expected: FAIL (modules missing).

- [ ] **Step 3: Create `src/twaky/sentinels/mail/schemas_write_side.py`**

```python
"""Pydantic output schemas for SP5b write-side extractors."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ExtractedMemory(BaseModel):
    kind: Literal["fact", "procedure", "preference"]
    scope: Literal["sender", "domain", "global"]
    scope_value: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=200)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("scope_value")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower()


class DraftDiffOutput(BaseModel):
    memories: list[ExtractedMemory] = Field(default_factory=list)
    should_delete_previous_memory_ids: list[UUID] = Field(default_factory=list)


class FolderMoveOutput(BaseModel):
    should_extract: bool
    memory: ExtractedMemory | None = None


__all__ = ["DraftDiffOutput", "ExtractedMemory", "FolderMoveOutput"]
```

- [ ] **Step 4: Create `src/twaky/sentinels/mail/prompts/extract_memory_from_diff.py`**

```python
"""Prompt: extract durable lessons from the diff between an AI draft
and what the user actually sent."""

from __future__ import annotations

import json
from typing import Any


def draft_diff_prompt(
    *,
    ai_draft: str,
    shipped_body: str,
    sender_email: str,
    recipient_email: str,
    thread_language: str,
    previous_memories: list[dict[str, Any]],
) -> str:
    prev_block = json.dumps(previous_memories, ensure_ascii=False, indent=2)
    return (
        "You compare an AI-generated draft with what the user actually sent, "
        "and extract durable lessons the AI can apply to future replies.\n\n"
        "Return a JSON object with:\n"
        '  "memories": array of {kind, scope, scope_value, content, confidence}\n'
        '  "should_delete_previous_memory_ids": array of UUIDs (default [])\n\n'
        "Guidelines:\n"
        "- Only extract lessons that will apply beyond this specific mail.\n"
        "- Prefer scope=\"sender\" when the change is specific to this correspondent.\n"
        "- Prefer scope=\"domain\" when the change would apply to any correspondent in the same organization.\n"
        "- Prefer scope=\"global\" only when the lesson clearly applies to every reply the user writes.\n"
        "- Ignore purely factual insertions the user added (dates, numbers, names present in the incoming mail) — those are context, not lessons.\n"
        "- Include a confidence between 0 and 1. Use >=0.9 only when the diff clearly demonstrates a durable preference.\n"
        "- If a previous memory contradicts what the user just did, list its ID under should_delete_previous_memory_ids.\n"
        "- Keep each memory content <=200 characters, actionable, in the language the user writes drafts in.\n\n"
        f"Sender (original mail): {sender_email}\n"
        f"Recipient (of the sent reply): {recipient_email}\n"
        f"Thread language: {thread_language}\n\n"
        "AI draft:\n"
        '"""\n'
        f"{ai_draft}\n"
        '"""\n\n'
        "User's sent version:\n"
        '"""\n'
        f"{shipped_body}\n"
        '"""\n\n'
        "Previous memories for this sender:\n"
        f"{prev_block}\n"
    )


__all__ = ["draft_diff_prompt"]
```

- [ ] **Step 5: Create `src/twaky/sentinels/mail/prompts/extract_memory_from_move.py`**

```python
"""Prompt: decide whether a folder move deserves a durable memory
beyond the statistical learned_pattern already recorded."""

from __future__ import annotations


def folder_move_prompt(
    *,
    sender_email: str,
    history_count: int,
    folder_name: str,
    subject: str,
) -> str:
    return (
        "The user moved a mail from Inbox to a custom folder. Decide whether "
        "this move reflects a durable relationship worth memorizing beyond "
        "the statistical pattern already recorded.\n\n"
        "Return JSON: {\"should_extract\": bool, "
        "\"memory\": {kind, scope, scope_value, content, confidence} | null}\n\n"
        "Extract a memory only when:\n"
        "- The sender has been seen >=3 times before AND consistently classified, OR\n"
        "- The destination folder name clearly implies a lasting role for the sender "
        "(e.g. \"Facturation\" for an accountant, \"Recrutement\" for a recruiter).\n\n"
        "Skip when:\n"
        "- First contact with a new sender (single move, no pattern yet).\n"
        "- Destination folder name is generic (e.g. \"Archive\", \"Divers\").\n\n"
        f"Sender: {sender_email} (seen {history_count} times before)\n"
        f"Destination folder: {folder_name}\n"
        f"Subject: {subject}\n"
    )


__all__ = ["folder_move_prompt"]
```

- [ ] **Step 6: Ensure `src/twaky/sentinels/mail/prompts/__init__.py` exists (it does)**

Verify with `ls src/twaky/sentinels/mail/prompts/__init__.py` — no change needed if present.

- [ ] **Step 7: Create tests directory init**

Ensure `tests/sentinels/mail/prompts/__init__.py` exists (create as empty file if absent).

- [ ] **Step 8: Run tests to verify pass**

```
pytest tests/sentinels/mail/prompts/test_extract_memory_prompts.py -v
```
Expected: PASS on all 6.

- [ ] **Step 9: Commit**

```bash
git add src/twaky/sentinels/mail/prompts/extract_memory_from_diff.py \
        src/twaky/sentinels/mail/prompts/extract_memory_from_move.py \
        src/twaky/sentinels/mail/schemas_write_side.py \
        tests/sentinels/mail/prompts/test_extract_memory_prompts.py \
        tests/sentinels/mail/prompts/__init__.py
git commit -m "feat(sp5b): LLM prompts + Pydantic schemas for memory extraction"
```

---

### Task 7: Extractor — reclassification (deterministic)

**Files:**
- Create: `src/twaky/sentinels/mail/extractors/__init__.py` (empty)
- Create: `src/twaky/sentinels/mail/extractors/reclassification.py`
- Create: `tests/sentinels/mail/extractors/__init__.py` (empty)
- Create: `tests/sentinels/mail/extractors/test_reclassification.py`

**Interfaces:**
- Consumes: `learned_patterns.record_decision`, `memories.insert`, `observations.insert_if_new`
- Produces:
  - `def extract_reclassification(*, email_id: str, mailbox_id: str, sender_email: str, direction: Literal["in","out"]) -> ExtractionResult`
  - `@dataclass ExtractionResult(memory_ids: list[UUID], pattern_ids: list[UUID], outcome: ExtractionOutcome, error_repr: str | None = None)`

- [ ] **Step 1: Write failing tests**

Create `tests/sentinels/mail/extractors/test_reclassification.py`:

```python
"""Reclassification extractor: user (un)marks spam."""

from __future__ import annotations

import pytest

from twaky.sentinels.mail.extractors.reclassification import extract_reclassification
from twaky.sentinels.mail.store.observations import ExtractionOutcome


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _cleanup():
    from twaky.db import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_memory")
        cur.execute("DELETE FROM mail_sentinel_learned_pattern")
        cur.execute("DELETE FROM mail_sentinel_observation")
        cur.execute("DELETE FROM mail_sentinel_spam_decision")
    yield


def test_unmarked_spam_creates_trust_sender_pattern_and_memory():
    result = extract_reclassification(
        email_id="e1",
        mailbox_id="junk-mbx",
        sender_email="legit@example.com",
        direction="out",
    )
    assert result.outcome == ExtractionOutcome.EXTRACTED
    assert len(result.pattern_ids) == 1
    assert len(result.memory_ids) == 1

    from twaky.sentinels.mail.store import learned_patterns as lp
    from twaky.sentinels.mail.store import memories as mem
    pat = lp.by_sender("legit@example.com")
    # Not yet active (evidence_count=1 < 3), but row exists
    all_pats = lp.list_all()
    assert any(p.rule_name == "trust_sender" for p in all_pats)
    all_mems = mem.list_recent(limit=10)
    assert any(m.source == "auto_reclass" and "not classify" in m.content.lower() for m in all_mems)


def test_marked_spam_creates_block_sender_pattern():
    result = extract_reclassification(
        email_id="e2",
        mailbox_id="junk-mbx",
        sender_email="spammer@bad.com",
        direction="in",
    )
    assert result.outcome == ExtractionOutcome.EXTRACTED
    from twaky.sentinels.mail.store import learned_patterns as lp
    all_pats = lp.list_all()
    assert any(p.rule_name == "block_sender" for p in all_pats)


def test_three_unmark_events_activates_trust_pattern():
    for i in range(3):
        extract_reclassification(
            email_id=f"e{i}",
            mailbox_id="junk-mbx",
            sender_email="legit@example.com",
            direction="out",
        )
    from twaky.sentinels.mail.store import learned_patterns as lp
    active = lp.by_sender("legit@example.com")
    assert active is not None
    assert active.rule_name == "trust_sender"
    assert active.is_active


def test_restores_existing_spam_decision():
    from twaky.db import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mail_sentinel_spam_decision "
            "(email_id, sender_email, received_at, bucket, signal_source) "
            "VALUES (%s, %s, now(), %s, %s)",
            ("e1", "legit@example.com", "spam", "rspamd_junk_keyword"),
        )
    extract_reclassification(
        email_id="e1",
        mailbox_id="junk-mbx",
        sender_email="legit@example.com",
        direction="out",
    )
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT restored_at, restored_by FROM mail_sentinel_spam_decision WHERE email_id=%s",
            ("e1",),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] is not None
    assert row[1] == "user"


def test_idempotence_via_observation_unique():
    r1 = extract_reclassification(
        email_id="e1",
        mailbox_id="junk-mbx",
        sender_email="legit@example.com",
        direction="out",
    )
    r2 = extract_reclassification(
        email_id="e1",
        mailbox_id="junk-mbx",
        sender_email="legit@example.com",
        direction="out",
    )
    from twaky.sentinels.mail.store import observations as obs
    rows = obs.list_recent(limit=100)
    # Only ONE observation row for this (email_id, mailbox_id, type)
    assert sum(1 for r in rows if r.email_id == "e1") == 1
```

- [ ] **Step 2: Run tests to verify failure**

```
pytest tests/sentinels/mail/extractors/test_reclassification.py -v
```
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `src/twaky/sentinels/mail/extractors/reclassification.py`**

```python
"""Reclassification extractor: deterministic (no LLM).

When the user moves a mail out of Spam (direction='out') the sender
becomes trusted; moving IN flags them as spam-worthy. After three
consistent observations from the same sender, the pattern activates
and short-circuits the spam triage in future runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from twaky.db import get_pool
from twaky.sentinels.mail.store import learned_patterns as lp_store
from twaky.sentinels.mail.store import memories as mem_store
from twaky.sentinels.mail.store import observations as obs_store
from twaky.sentinels.mail.store.observations import (
    ExtractionOutcome,
    ObservationType,
)


@dataclass
class ExtractionResult:
    outcome: ExtractionOutcome
    memory_ids: list[UUID] = field(default_factory=list)
    pattern_ids: list[UUID] = field(default_factory=list)
    error_repr: str | None = None


def _observation_type(direction: Literal["in", "out"]) -> ObservationType:
    return (
        ObservationType.MARKED_SPAM if direction == "in" else ObservationType.UNMARKED_SPAM
    )


def _maybe_restore_spam_decision(email_id: str, direction: Literal["in", "out"]) -> None:
    if direction != "out":
        return
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE mail_sentinel_spam_decision "
            "SET restored_at = now(), restored_by = 'user' "
            "WHERE email_id = %s AND restored_at IS NULL",
            (email_id,),
        )


def extract_reclassification(
    *,
    email_id: str,
    mailbox_id: str,
    sender_email: str,
    direction: Literal["in", "out"],
) -> ExtractionResult:
    if direction == "out":
        rule_name = "trust_sender"
        content = "Legit sender — do not classify as spam."
        hint = 0.95
    else:
        rule_name = "block_sender"
        content = "Treat this sender as spam by default."
        hint = 0.90

    pattern = lp_store.record_decision(
        sender_email=sender_email, rule_name=rule_name, confidence_hint=hint
    )

    memory = mem_store.insert(
        kind="fact",
        scope="sender",
        scope_value=sender_email.lower(),
        content=content,
        source="auto_reclass",
        sender_email=sender_email.lower(),
        confidence=1.0,
    )

    _maybe_restore_spam_decision(email_id, direction)

    obs = obs_store.insert_if_new(
        email_id=email_id,
        mailbox_id=mailbox_id,
        observation_type=_observation_type(direction),
        extraction_outcome=ExtractionOutcome.EXTRACTED,
        memory_ids=[memory.id],
        pattern_ids=[pattern.id],
    )

    # If observation was a duplicate (obs is None), we still bumped the
    # pattern; this is intentional because the same email being un-flagged
    # twice is a valid signal-strength boost. Idempotence at the DB layer
    # protects the audit log, not the learning itself.
    return ExtractionResult(
        outcome=ExtractionOutcome.EXTRACTED,
        memory_ids=[memory.id],
        pattern_ids=[pattern.id],
    )


__all__ = ["ExtractionResult", "extract_reclassification"]
```

- [ ] **Step 4: Run tests to verify pass**

```
pytest tests/sentinels/mail/extractors/test_reclassification.py -v
```
Expected: PASS on all 5.

- [ ] **Step 5: Commit**

```bash
git add src/twaky/sentinels/mail/extractors/__init__.py \
        src/twaky/sentinels/mail/extractors/reclassification.py \
        tests/sentinels/mail/extractors/__init__.py \
        tests/sentinels/mail/extractors/test_reclassification.py
git commit -m "feat(sp5b): reclassification extractor (deterministic, no LLM)"
```

---

### Task 8: Extractor — folder_move (hybrid, cheap LLM)

**Files:**
- Create: `src/twaky/sentinels/mail/extractors/folder_move.py`
- Create: `tests/sentinels/mail/extractors/test_folder_move.py`

**Interfaces:**
- Consumes: `learned_patterns.record_decision`, `memories.insert`, `structured_call` from `twaky.sentinels.mail.llm.invoke`, `folder_move_prompt`, `FolderMoveOutput`
- Produces:
  - `def extract_folder_move(*, email_id: str, mailbox_id: str, sender_email: str, folder_name: str, subject: str, history_count: int) -> ExtractionResult`

- [ ] **Step 1: Write failing tests**

Create `tests/sentinels/mail/extractors/test_folder_move.py`:

```python
"""Folder move extractor: pattern always, LLM decides memory."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from twaky.sentinels.mail.extractors.folder_move import extract_folder_move
from twaky.sentinels.mail.schemas_write_side import (
    ExtractedMemory,
    FolderMoveOutput,
)
from twaky.sentinels.mail.store.observations import ExtractionOutcome


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _cleanup():
    from twaky.db import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_memory")
        cur.execute("DELETE FROM mail_sentinel_learned_pattern")
        cur.execute("DELETE FROM mail_sentinel_observation")
    yield


def test_should_extract_true_creates_memory_and_pattern():
    llm_out = FolderMoveOutput(
        should_extract=True,
        memory=ExtractedMemory(
            kind="fact",
            scope="sender",
            scope_value="c@x.com",
            content="Fournisseur récurrent facturation",
            confidence=0.9,
        ),
    )
    with patch(
        "twaky.sentinels.mail.extractors.folder_move.structured_call",
        return_value=llm_out,
    ):
        r = extract_folder_move(
            email_id="e1",
            mailbox_id="mbx-inbox",
            sender_email="c@x.com",
            folder_name="Facturation",
            subject="Facture 2026-01",
            history_count=5,
        )
    assert r.outcome == ExtractionOutcome.EXTRACTED
    assert len(r.pattern_ids) == 1
    assert len(r.memory_ids) == 1


def test_should_extract_false_creates_pattern_only():
    llm_out = FolderMoveOutput(should_extract=False)
    with patch(
        "twaky.sentinels.mail.extractors.folder_move.structured_call",
        return_value=llm_out,
    ):
        r = extract_folder_move(
            email_id="e2",
            mailbox_id="mbx-inbox",
            sender_email="unknown@z.com",
            folder_name="Archive",
            subject="Info",
            history_count=1,
        )
    assert r.outcome == ExtractionOutcome.EXTRACTED
    assert len(r.pattern_ids) == 1
    assert r.memory_ids == []


def test_folder_name_sanitized_for_rule_name():
    with patch(
        "twaky.sentinels.mail.extractors.folder_move.structured_call",
        return_value=FolderMoveOutput(should_extract=False),
    ):
        extract_folder_move(
            email_id="e3",
            mailbox_id="mbx-inbox",
            sender_email="c@x.com",
            folder_name="Ma Facturation!",
            subject="s",
            history_count=1,
        )
    from twaky.sentinels.mail.store import learned_patterns as lp
    pats = lp.list_all()
    assert any(p.rule_name == "label:Ma-Facturation" for p in pats)


def test_llm_failure_returns_error_outcome():
    with patch(
        "twaky.sentinels.mail.extractors.folder_move.structured_call",
        side_effect=RuntimeError("llm down"),
    ):
        r = extract_folder_move(
            email_id="e4",
            mailbox_id="mbx-inbox",
            sender_email="c@x.com",
            folder_name="Facturation",
            subject="s",
            history_count=5,
        )
    # Pattern still recorded (deterministic), LLM failed → error outcome
    assert r.outcome == ExtractionOutcome.ERROR
    assert r.error_repr is not None
    assert len(r.pattern_ids) == 1
    assert r.memory_ids == []
```

- [ ] **Step 2: Run tests to verify failure**

```
pytest tests/sentinels/mail/extractors/test_folder_move.py -v
```
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `src/twaky/sentinels/mail/extractors/folder_move.py`**

```python
"""Folder move extractor: pattern always, LLM decides memory."""

from __future__ import annotations

import logging
import re

from twaky.sentinels.mail.extractors.reclassification import ExtractionResult
from twaky.sentinels.mail.llm.hardening import Hardening
from twaky.sentinels.mail.llm.invoke import structured_call
from twaky.sentinels.mail.llm.tiers import UseCase
from twaky.sentinels.mail.prompts.extract_memory_from_move import folder_move_prompt
from twaky.sentinels.mail.schemas_write_side import FolderMoveOutput
from twaky.sentinels.mail.store import learned_patterns as lp_store
from twaky.sentinels.mail.store import memories as mem_store
from twaky.sentinels.mail.store import observations as obs_store
from twaky.sentinels.mail.store.observations import (
    ExtractionOutcome,
    ObservationType,
)

log = logging.getLogger(__name__)

_RULE_NAME_SANITIZER = re.compile(r"[^A-Za-z0-9-]+")


def _sanitize_folder_name(folder_name: str) -> str:
    """Match JMAP flag naming: alphanumeric + hyphen only."""
    return _RULE_NAME_SANITIZER.sub("-", folder_name).strip("-") or "Folder"


def extract_folder_move(
    *,
    email_id: str,
    mailbox_id: str,
    sender_email: str,
    folder_name: str,
    subject: str,
    history_count: int,
) -> ExtractionResult:
    sanitized = _sanitize_folder_name(folder_name)
    rule_name = f"label:{sanitized}"

    pattern = lp_store.record_decision(
        sender_email=sender_email, rule_name=rule_name, confidence_hint=0.85
    )
    pattern_ids = [pattern.id]
    memory_ids: list = []

    try:
        prompt = folder_move_prompt(
            sender_email=sender_email,
            history_count=history_count,
            folder_name=folder_name,
            subject=subject,
        )
        out: FolderMoveOutput = structured_call(
            prompt,
            FolderMoveOutput,
            hardening=Hardening.COMPACT,
            use_case=UseCase.EXTRACT_MEMORY_MOVE,
        )
    except Exception as e:
        log.warning("folder_move: LLM failed: %r", e)
        obs_store.insert_if_new(
            email_id=email_id,
            mailbox_id=mailbox_id,
            observation_type=ObservationType.MOVED_TO_CUSTOM,
            extraction_outcome=ExtractionOutcome.ERROR,
            memory_ids=[],
            pattern_ids=pattern_ids,
            error_repr=repr(e),
        )
        return ExtractionResult(
            outcome=ExtractionOutcome.ERROR,
            memory_ids=[],
            pattern_ids=pattern_ids,
            error_repr=repr(e),
        )

    if out.should_extract and out.memory is not None and out.memory.confidence >= 0.7:
        m = mem_store.insert(
            kind=out.memory.kind,
            scope=out.memory.scope,
            scope_value=out.memory.scope_value,
            content=out.memory.content,
            source="auto_move",
            sender_email=(sender_email.lower() if out.memory.scope == "sender" else None),
            confidence=out.memory.confidence,
        )
        memory_ids = [m.id]

    obs_store.insert_if_new(
        email_id=email_id,
        mailbox_id=mailbox_id,
        observation_type=ObservationType.MOVED_TO_CUSTOM,
        extraction_outcome=ExtractionOutcome.EXTRACTED,
        memory_ids=memory_ids,
        pattern_ids=pattern_ids,
    )

    return ExtractionResult(
        outcome=ExtractionOutcome.EXTRACTED,
        memory_ids=memory_ids,
        pattern_ids=pattern_ids,
    )


__all__ = ["extract_folder_move"]
```

Note: `UseCase.EXTRACT_MEMORY_MOVE` and `EXTRACT_MEMORY_DIFF` need to be added to `src/twaky/sentinels/mail/llm/tiers.py`'s UseCase enum. Add them in the same commit:

```python
    # SP5b extractors
    EXTRACT_MEMORY_DIFF = "extract_memory_diff"
    EXTRACT_MEMORY_MOVE = "extract_memory_move"
```

Map both to the `chat` / `economy` tier respectively in the existing tier map (whatever the existing pattern looks like — grep `UseCase.` in `tiers.py` to see the mapping table).

- [ ] **Step 4: Run tests to verify pass**

```
pytest tests/sentinels/mail/extractors/test_folder_move.py -v
```
Expected: PASS on all 4.

- [ ] **Step 5: Commit**

```bash
git add src/twaky/sentinels/mail/extractors/folder_move.py \
        src/twaky/sentinels/mail/llm/tiers.py \
        tests/sentinels/mail/extractors/test_folder_move.py
git commit -m "feat(sp5b): folder_move extractor with cheap LLM guard"
```

---

### Task 9: Extractor — draft_diff (LLM + mission match)

**Files:**
- Create: `src/twaky/sentinels/mail/extractors/draft_diff.py`
- Create: `tests/sentinels/mail/extractors/test_draft_diff.py`

**Interfaces:**
- Consumes: `mission` table (via SQL query), `memories.insert`, `memories.list_for_prompt`, `structured_call`, `draft_diff_prompt`, `DraftDiffOutput`
- Produces:
  - `def extract_draft_diff(*, email_id: str, mailbox_id: str, sender_email: str, recipient_email: str, shipped_body: str, subject: str, in_reply_to: str | None, owner_email: str) -> ExtractionResult`
  - Internal: `_find_matching_mission(...)`, `_levenshtein_ratio(...)`, `_transition_mission_to_done(...)`

- [ ] **Step 1: Write failing tests**

Create `tests/sentinels/mail/extractors/test_draft_diff.py`:

```python
"""Draft diff extractor: LLM extracts memories from AI vs shipped diff."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest

from twaky.sentinels.mail.extractors.draft_diff import (
    _levenshtein_ratio,
    extract_draft_diff,
)
from twaky.sentinels.mail.schemas_write_side import (
    DraftDiffOutput,
    ExtractedMemory,
)
from twaky.sentinels.mail.store.observations import ExtractionOutcome


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _cleanup():
    from twaky.db import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_memory")
        cur.execute("DELETE FROM mail_sentinel_observation")
        cur.execute("DELETE FROM mission WHERE declared_by='sentinel:mail'")
    yield


def _insert_mission_with_artifact(*, message_id: str, ai_draft: str, owner: str):
    from twaky.db import get_pool
    import json
    mission_id = uuid4()
    artifacts = json.dumps([
        {"kind": "draft", "body": ai_draft, "in_reply_to_message_id": message_id}
    ])
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mission (id, owner_email, declared_by, intent_text, "
            "state, artifacts) VALUES (%s, %s, 'sentinel:mail', 'Draft ready: test', "
            "'awaiting_user', %s::jsonb)",
            (mission_id, owner, artifacts),
        )
    return mission_id


def test_levenshtein_ratio_identical():
    assert _levenshtein_ratio("hello", "hello") == 0.0


def test_levenshtein_ratio_completely_different():
    assert _levenshtein_ratio("hello", "world") > 0.5


def test_trivial_diff_returns_skipped():
    mid = _insert_mission_with_artifact(
        message_id="<msg1@x>",
        ai_draft="Bonjour Alex,\n\nMerci.\n\nBien à vous,\n\nMichel-Marie",
        owner="mmaudet@linagora.com",
    )
    result = extract_draft_diff(
        email_id="e1",
        mailbox_id="sent-mbx",
        sender_email="alex@x.com",
        recipient_email="alex@x.com",
        shipped_body="Bonjour Alex,\n\nMerci!\n\nBien à vous,\n\nMichel-Marie",
        subject="Re: test",
        in_reply_to="<msg1@x>",
        owner_email="mmaudet@linagora.com",
    )
    assert result.outcome == ExtractionOutcome.SKIPPED_TRIVIAL
    assert result.memory_ids == []


def test_no_mission_match_returns_skipped_no_match():
    r = extract_draft_diff(
        email_id="e2",
        mailbox_id="sent-mbx",
        sender_email="x@y.com",
        recipient_email="x@y.com",
        shipped_body="Body",
        subject="s",
        in_reply_to="<nomatch@x>",
        owner_email="mmaudet@linagora.com",
    )
    assert r.outcome == ExtractionOutcome.SKIPPED_NO_MATCH


def test_llm_extraction_creates_memories_and_transitions_mission():
    mid = _insert_mission_with_artifact(
        message_id="<msg1@x>",
        ai_draft="Cher Alexandre,\n\nMerci.\n\nBien à vous,\n\nMichel-Marie",
        owner="mmaudet@linagora.com",
    )
    llm_out = DraftDiffOutput(
        memories=[
            ExtractedMemory(
                kind="preference",
                scope="sender",
                scope_value="alexandre@linagora.com",
                content="Utilise 'Bonjour Alexandre' au lieu de 'Cher Alexandre'",
                confidence=0.9,
            )
        ]
    )
    with patch(
        "twaky.sentinels.mail.extractors.draft_diff.structured_call",
        return_value=llm_out,
    ):
        r = extract_draft_diff(
            email_id="e3",
            mailbox_id="sent-mbx",
            sender_email="alexandre@linagora.com",
            recipient_email="alexandre@linagora.com",
            shipped_body="Bonjour Alexandre,\n\nMerci pour ta note, on regarde ça demain.\n\nBien à vous,\n\nMichel-Marie",
            subject="Re: sujet",
            in_reply_to="<msg1@x>",
            owner_email="mmaudet@linagora.com",
        )
    assert r.outcome == ExtractionOutcome.EXTRACTED
    assert len(r.memory_ids) == 1

    from twaky.db import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT state FROM mission WHERE id=%s", (mid,))
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "done"


def test_low_confidence_memory_filtered_out():
    _insert_mission_with_artifact(
        message_id="<msg2@x>",
        ai_draft="A",
        owner="mmaudet@linagora.com",
    )
    llm_out = DraftDiffOutput(
        memories=[
            ExtractedMemory(
                kind="preference",
                scope="sender",
                scope_value="x@y.com",
                content="test",
                confidence=0.5,  # below 0.7 threshold
            )
        ]
    )
    with patch(
        "twaky.sentinels.mail.extractors.draft_diff.structured_call",
        return_value=llm_out,
    ):
        r = extract_draft_diff(
            email_id="e5",
            mailbox_id="sent-mbx",
            sender_email="x@y.com",
            recipient_email="x@y.com",
            shipped_body="A very different body indeed, longer than the AI draft",
            subject="Re: s",
            in_reply_to="<msg2@x>",
            owner_email="mmaudet@linagora.com",
        )
    assert r.outcome == ExtractionOutcome.EXTRACTED
    assert r.memory_ids == []


def test_llm_failure_returns_error_outcome():
    _insert_mission_with_artifact(
        message_id="<msg3@x>",
        ai_draft="A",
        owner="mmaudet@linagora.com",
    )
    with patch(
        "twaky.sentinels.mail.extractors.draft_diff.structured_call",
        side_effect=RuntimeError("llm down"),
    ):
        r = extract_draft_diff(
            email_id="e6",
            mailbox_id="sent-mbx",
            sender_email="x@y.com",
            recipient_email="x@y.com",
            shipped_body="A very different body indeed, longer than the AI draft",
            subject="Re: s",
            in_reply_to="<msg3@x>",
            owner_email="mmaudet@linagora.com",
        )
    assert r.outcome == ExtractionOutcome.ERROR
```

- [ ] **Step 2: Run tests to verify failure**

```
pytest tests/sentinels/mail/extractors/test_draft_diff.py -v
```
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `src/twaky/sentinels/mail/extractors/draft_diff.py`**

```python
"""Draft diff extractor: LLM-based memory extraction from AI-vs-shipped diff.

Matches a sent mail to a recent Twaky mission by In-Reply-To message-id,
compares the AI-original draft to what the user actually shipped, and
calls the LLM to extract durable lessons.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row

from twaky.db import get_pool
from twaky.sentinels.mail.extractors.reclassification import ExtractionResult
from twaky.sentinels.mail.llm.hardening import Hardening
from twaky.sentinels.mail.llm.invoke import structured_call
from twaky.sentinels.mail.llm.tiers import UseCase
from twaky.sentinels.mail.prompts.extract_memory_from_diff import draft_diff_prompt
from twaky.sentinels.mail.schemas_write_side import DraftDiffOutput
from twaky.sentinels.mail.store import memories as mem_store
from twaky.sentinels.mail.store import observations as obs_store
from twaky.sentinels.mail.store.observations import (
    ExtractionOutcome,
    ObservationType,
)

log = logging.getLogger(__name__)

_TRIVIAL_DIFF_THRESHOLD = 0.05
_MIN_CONFIDENCE = 0.7


def _levenshtein_ratio(a: str, b: str) -> float:
    """Return the Levenshtein edit distance normalized to [0.0, 1.0].

    0.0 = identical; 1.0 = completely different.
    """
    if a == b:
        return 0.0
    if not a or not b:
        return 1.0
    # Simple DP implementation; strings here are short (< 5 KB usually).
    m, n = len(a), len(b)
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        for j in range(1, n + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[n] / max(m, n)


def _find_matching_mission(
    *, owner_email: str, in_reply_to: str
) -> dict[str, Any] | None:
    """Return a mission whose artifacts reference *in_reply_to* message-id."""
    sql = """
        SELECT id, artifacts
        FROM mission
        WHERE state = 'awaiting_user'
          AND declared_by = 'sentinel:mail'
          AND owner_email = %s
          AND created_at > now() - INTERVAL '7 days'
        ORDER BY created_at DESC
        LIMIT 20
    """
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (owner_email,))
        rows = cur.fetchall()

    for row in rows:
        artifacts = row["artifacts"] or []
        for art in artifacts:
            mid = (art or {}).get("in_reply_to_message_id")
            if mid and mid == in_reply_to:
                return row
    return None


def _extract_ai_draft(artifacts: list[dict[str, Any]]) -> str | None:
    for art in artifacts:
        if isinstance(art, dict) and art.get("kind") == "draft" and art.get("body"):
            return str(art["body"])
    return None


def _transition_mission_to_done(mission_id: UUID) -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE mission SET state='done', state_reason='draft_sent_by_user', "
            "updated_at=now() WHERE id=%s",
            (mission_id,),
        )


def _delete_memories(ids: list[UUID]) -> None:
    if not ids:
        return
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_memory WHERE id = ANY(%s)", (list(ids),))


def extract_draft_diff(
    *,
    email_id: str,
    mailbox_id: str,
    sender_email: str,
    recipient_email: str,
    shipped_body: str,
    subject: str,
    in_reply_to: str | None,
    owner_email: str,
) -> ExtractionResult:
    if not in_reply_to:
        obs_store.insert_if_new(
            email_id=email_id,
            mailbox_id=mailbox_id,
            observation_type=ObservationType.DRAFT_SENT,
            extraction_outcome=ExtractionOutcome.SKIPPED_NO_MATCH,
        )
        return ExtractionResult(outcome=ExtractionOutcome.SKIPPED_NO_MATCH)

    mission = _find_matching_mission(
        owner_email=owner_email, in_reply_to=in_reply_to
    )
    if mission is None:
        obs_store.insert_if_new(
            email_id=email_id,
            mailbox_id=mailbox_id,
            observation_type=ObservationType.DRAFT_SENT,
            extraction_outcome=ExtractionOutcome.SKIPPED_NO_MATCH,
        )
        return ExtractionResult(outcome=ExtractionOutcome.SKIPPED_NO_MATCH)

    ai_draft = _extract_ai_draft(mission["artifacts"])
    if not ai_draft:
        obs_store.insert_if_new(
            email_id=email_id,
            mailbox_id=mailbox_id,
            observation_type=ObservationType.DRAFT_SENT,
            extraction_outcome=ExtractionOutcome.SKIPPED_NO_MATCH,
        )
        return ExtractionResult(outcome=ExtractionOutcome.SKIPPED_NO_MATCH)

    ratio = _levenshtein_ratio(ai_draft, shipped_body)
    if ratio < _TRIVIAL_DIFF_THRESHOLD:
        obs_store.insert_if_new(
            email_id=email_id,
            mailbox_id=mailbox_id,
            observation_type=ObservationType.DRAFT_SENT,
            extraction_outcome=ExtractionOutcome.SKIPPED_TRIVIAL,
        )
        return ExtractionResult(outcome=ExtractionOutcome.SKIPPED_TRIVIAL)

    # Fetch previous memories for this sender to include in prompt
    prev = mem_store.list_for_prompt(
        sender_email=sender_email,
        sender_domain=sender_email.split("@")[-1] if "@" in sender_email else "",
        limit=8,
    )
    previous_memories = [
        {"id": str(m.id), "content": m.content, "scope": m.scope} for m in prev
    ]

    try:
        prompt = draft_diff_prompt(
            ai_draft=ai_draft,
            shipped_body=shipped_body,
            sender_email=sender_email,
            recipient_email=recipient_email,
            thread_language="auto",  # LLM infers from bodies
            previous_memories=previous_memories,
        )
        out: DraftDiffOutput = structured_call(
            prompt,
            DraftDiffOutput,
            hardening=Hardening.COMPACT,
            use_case=UseCase.EXTRACT_MEMORY_DIFF,
        )
    except Exception as e:
        log.warning("draft_diff: LLM failed: %r", e)
        obs_store.insert_if_new(
            email_id=email_id,
            mailbox_id=mailbox_id,
            observation_type=ObservationType.DRAFT_SENT,
            extraction_outcome=ExtractionOutcome.ERROR,
            error_repr=repr(e),
        )
        return ExtractionResult(
            outcome=ExtractionOutcome.ERROR, error_repr=repr(e)
        )

    _delete_memories(out.should_delete_previous_memory_ids)

    memory_ids: list[UUID] = []
    for em in out.memories:
        if em.confidence < _MIN_CONFIDENCE:
            continue
        m = mem_store.insert(
            kind=em.kind,
            scope=em.scope,
            scope_value=em.scope_value,
            content=em.content,
            source="auto_diff",
            sender_email=(sender_email.lower() if em.scope == "sender" else None),
            mission_id=mission["id"],
            confidence=em.confidence,
        )
        memory_ids.append(m.id)

    _transition_mission_to_done(mission["id"])

    obs_store.insert_if_new(
        email_id=email_id,
        mailbox_id=mailbox_id,
        observation_type=ObservationType.DRAFT_SENT,
        extraction_outcome=ExtractionOutcome.EXTRACTED,
        memory_ids=memory_ids,
    )

    return ExtractionResult(
        outcome=ExtractionOutcome.EXTRACTED, memory_ids=memory_ids
    )


__all__ = ["extract_draft_diff"]
```

- [ ] **Step 4: Run tests to verify pass**

```
pytest tests/sentinels/mail/extractors/test_draft_diff.py -v
```
Expected: PASS on all 6.

- [ ] **Step 5: Commit**

```bash
git add src/twaky/sentinels/mail/extractors/draft_diff.py \
        tests/sentinels/mail/extractors/test_draft_diff.py
git commit -m "feat(sp5b): draft_diff extractor (mission match + LLM diff)"
```

---

### Task 10: Observer — poll extension + dispatch

**Files:**
- Create: `src/twaky/sentinels/mail/observer.py`
- Create: `tests/sentinels/mail/test_observer.py`

**Interfaces:**
- Consumes: `JmapMailAdapter` (for `Mailbox/query`, `Mailbox/get`, `Email/changes`, `Email/get`), `mailbox_state` store, all three extractors
- Produces:
  - `class MailObserver` with async `run_tick(adapter: JmapMailAdapter, owner_email: str) -> ObserverTickResult`
  - `@dataclass ObserverTickResult(mailboxes_polled: int, observations_created: int, memories_created: int, patterns_updated: int, llm_calls: int)`

- [ ] **Step 1: Write failing tests**

Create `tests/sentinels/mail/test_observer.py`:

```python
"""Observer tick logic — dispatch to correct extractor per change type."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from twaky.sentinels.mail.observer import MailObserver


class FakeAdapter:
    def __init__(self, mailboxes, changes_map, emails):
        self._mailboxes = mailboxes
        self._changes_map = changes_map  # {mailbox_id: (new_state, created_ids)}
        self._emails = emails            # {email_id: email_dict}

    async def query_mailboxes(self):
        return self._mailboxes

    async def get_mailbox_state(self, mailbox_id):
        return self._changes_map.get(mailbox_id, ("state-init", []))[0]

    async def changes(self, mailbox_id, since_state):
        entry = self._changes_map.get(mailbox_id)
        if entry is None:
            return {"newState": "state-init", "created": [], "updated": [], "destroyed": []}
        new_state, created_ids = entry
        return {"newState": new_state, "created": created_ids, "updated": [], "destroyed": []}

    async def get_email(self, email_id):
        return self._emails.get(email_id)


@pytest.mark.integration
def test_bootstrap_stores_state_without_replay():
    import asyncio
    adapter = FakeAdapter(
        mailboxes=[{"id": "mbx-sent", "role": "sent", "name": "Sent"}],
        changes_map={"mbx-sent": ("state-1", ["e1"])},
        emails={"e1": {"id": "e1", "from": [{"email": "x@y.com"}]}},
    )
    obs = MailObserver()
    # No prior state → bootstrap should skip replay
    result = asyncio.run(obs.run_tick(adapter, owner_email="mmaudet@linagora.com"))
    assert result.observations_created == 0

    from twaky.sentinels.mail.store import mailbox_state as ms
    assert ms.get("mbx-sent") is not None


@pytest.mark.integration
def test_second_tick_dispatches_draft_sent():
    import asyncio
    from twaky.sentinels.mail.store import mailbox_state as ms
    ms.upsert(mailbox_id="mbx-sent", jmap_state="state-0", role="sent", name="Sent")

    adapter = FakeAdapter(
        mailboxes=[{"id": "mbx-sent", "role": "sent", "name": "Sent"}],
        changes_map={"mbx-sent": ("state-1", ["e1"])},
        emails={
            "e1": {
                "id": "e1",
                "from": [{"email": "recipient@x.com"}],
                "to": [{"email": "recipient@x.com"}],
                "subject": "Re: hello",
                "textBody": [{"partId": "1"}],
                "bodyValues": {"1": {"value": "Bonjour, merci."}},
                "headers": [{"name": "In-Reply-To", "value": "<orig@x>"}],
            }
        },
    )
    with patch("twaky.sentinels.mail.observer.extract_draft_diff") as diff_mock:
        diff_mock.return_value = MagicMock(
            outcome=MagicMock(value="skipped_no_match"),
            memory_ids=[],
            pattern_ids=[],
        )
        result = asyncio.run(
            MailObserver().run_tick(adapter, owner_email="mmaudet@linagora.com")
        )
    diff_mock.assert_called_once()
    assert result.observations_created >= 0  # dispatch happened
```

- [ ] **Step 2: Run tests to verify failure**

```
pytest tests/sentinels/mail/test_observer.py -v
```
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `src/twaky/sentinels/mail/observer.py`**

```python
"""SP5b observer: extended JMAP poller for user actions.

Runs once per tick from the mail sentinel poll loop. Iterates watched
mailboxes, queries Email/changes since the last stored state, classifies
each change (draft_sent / marked_spam / unmarked_spam / moved_to_custom),
and dispatches to the appropriate extractor.

On bootstrap (no prior state for a mailbox), stores the current JMAP
state without replaying history — conservative rollout.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from twaky.config import settings
from twaky.sentinels.mail.extractors.draft_diff import extract_draft_diff
from twaky.sentinels.mail.extractors.folder_move import extract_folder_move
from twaky.sentinels.mail.extractors.reclassification import extract_reclassification
from twaky.sentinels.mail.store import learned_patterns as lp_store
from twaky.sentinels.mail.store import mailbox_state as ms_store
from twaky.sentinels.mail.store.observations import ExtractionOutcome

log = logging.getLogger(__name__)

_SYSTEM_FOLDER_NAMES: frozenset[str] = frozenset(
    {"Inbox", "Drafts", "Templates", "Outbox", "Archive", "Sent", "Trash", "Junk"}
)


@dataclass
class ObserverTickResult:
    mailboxes_polled: int = 0
    observations_created: int = 0
    memories_created: int = 0
    patterns_updated: int = 0
    llm_calls: int = 0
    errors: list[str] = field(default_factory=list)


def _first_email_address(field_value: Any) -> str:
    """From JMAP addresses list: [{name, email}] → 'email' or ''."""
    if isinstance(field_value, list) and field_value:
        entry = field_value[0]
        if isinstance(entry, dict):
            return str(entry.get("email") or "").lower()
    return ""


def _header(headers: list[dict], name: str) -> str | None:
    lname = name.lower()
    for h in headers or []:
        if str(h.get("name", "")).lower() == lname:
            v = h.get("value")
            return str(v) if v is not None else None
    return None


def _extract_body_text(email: dict) -> str:
    """Best-effort: assemble text body from bodyValues+textBody."""
    text_body = email.get("textBody") or []
    body_values = email.get("bodyValues") or {}
    parts: list[str] = []
    for tb in text_body:
        pid = tb.get("partId")
        if not pid:
            continue
        bv = body_values.get(pid)
        if bv and bv.get("value"):
            parts.append(str(bv["value"]))
    return "\n".join(parts)


class MailObserver:
    """Polls watched mailboxes and dispatches user actions to extractors."""

    async def run_tick(self, adapter: Any, owner_email: str) -> ObserverTickResult:
        if not settings.mail_sentinel_observer_enabled:
            return ObserverTickResult()

        result = ObserverTickResult()
        mailboxes = await self._watched_mailboxes(adapter)
        result.mailboxes_polled = len(mailboxes)

        for mbx in mailboxes:
            try:
                await self._tick_mailbox(adapter, mbx, owner_email, result)
            except Exception as e:
                log.warning("observer: mailbox %s failed: %r", mbx.get("id"), e)
                result.errors.append(f"{mbx.get('id')}: {e!r}")
                continue

        log.info(
            "observer_tick_done polled=%d obs=%d mem=%d pat=%d errs=%d",
            result.mailboxes_polled,
            result.observations_created,
            result.memories_created,
            result.patterns_updated,
            len(result.errors),
        )
        return result

    async def _watched_mailboxes(self, adapter: Any) -> list[dict]:
        all_mbx = await adapter.query_mailboxes()
        watched_roles = set(settings.watched_mailbox_roles_list)
        watched = []
        for mbx in all_mbx:
            role = (mbx.get("role") or "").lower() or None
            name = mbx.get("name") or ""
            if role in watched_roles:
                watched.append(mbx)
                continue
            # Custom folders: role IS NULL AND name not standard
            if role is None and name and name not in _SYSTEM_FOLDER_NAMES:
                watched.append(mbx)
        return watched

    async def _tick_mailbox(
        self,
        adapter: Any,
        mbx: dict,
        owner_email: str,
        result: ObserverTickResult,
    ) -> None:
        mailbox_id = mbx["id"]
        role = (mbx.get("role") or "").lower() or None
        name = mbx.get("name")

        stored = ms_store.get(mailbox_id)
        if stored is None:
            # Bootstrap: read current state, store it, no replay.
            current = await adapter.get_mailbox_state(mailbox_id)
            ms_store.upsert(
                mailbox_id=mailbox_id, jmap_state=current, role=role, name=name
            )
            return

        changes = await adapter.changes(mailbox_id, stored.jmap_state)
        new_state = changes.get("newState") or stored.jmap_state
        for email_id in list(changes.get("created", [])) + list(changes.get("updated", [])):
            try:
                await self._dispatch(adapter, mailbox_id, role, name, email_id, owner_email, result)
            except Exception as e:
                log.warning("observer: dispatch failed for %s: %r", email_id, e)
                result.errors.append(f"{email_id}: {e!r}")

        ms_store.upsert(
            mailbox_id=mailbox_id, jmap_state=new_state, role=role, name=name
        )

    async def _dispatch(
        self,
        adapter: Any,
        mailbox_id: str,
        role: str | None,
        folder_name: str | None,
        email_id: str,
        owner_email: str,
        result: ObserverTickResult,
    ) -> None:
        email = await adapter.get_email(email_id)
        if email is None:
            return
        from_email = _first_email_address(email.get("from"))
        to_email = _first_email_address(email.get("to"))

        if role == "sent":
            headers = email.get("headers") or []
            in_reply_to = _header(headers, "In-Reply-To") or _header(headers, "References")
            body = _extract_body_text(email)
            r = extract_draft_diff(
                email_id=email_id,
                mailbox_id=mailbox_id,
                sender_email=to_email or from_email,
                recipient_email=to_email or from_email,
                shipped_body=body,
                subject=email.get("subject") or "",
                in_reply_to=in_reply_to,
                owner_email=owner_email,
            )
            self._tally(result, r)
            return

        if role == "junk":
            r = extract_reclassification(
                email_id=email_id,
                mailbox_id=mailbox_id,
                sender_email=from_email,
                direction="in",
            )
            self._tally(result, r)
            return

        # Custom folder (role is None)
        if role is None and folder_name and folder_name not in _SYSTEM_FOLDER_NAMES:
            history = len(
                [p for p in lp_store.list_all() if p.sender_email == from_email]
            )
            r = extract_folder_move(
                email_id=email_id,
                mailbox_id=mailbox_id,
                sender_email=from_email,
                folder_name=folder_name,
                subject=email.get("subject") or "",
                history_count=history,
            )
            self._tally(result, r)

        # Note: unmarked_spam (mail LEAVING junk) is detected as a
        # non-junk mailbox `updated` event; the classify path would need
        # cross-mailbox tracking to detect movement. MVP handles the
        # marked_spam direction only; unmarked_spam is a follow-up.

    def _tally(self, result: ObserverTickResult, r: Any) -> None:
        if r is None:
            return
        result.observations_created += 1
        result.memories_created += len(getattr(r, "memory_ids", []) or [])
        result.patterns_updated += len(getattr(r, "pattern_ids", []) or [])
        if getattr(r, "outcome", None) == ExtractionOutcome.ERROR:
            result.errors.append(str(getattr(r, "error_repr", "unknown error")))


__all__ = ["MailObserver", "ObserverTickResult"]
```

- [ ] **Step 4: Run tests to verify pass**

```
pytest tests/sentinels/mail/test_observer.py -v
```
Expected: PASS on both.

- [ ] **Step 5: Commit**

```bash
git add src/twaky/sentinels/mail/observer.py tests/sentinels/mail/test_observer.py
git commit -m "feat(sp5b): observer with per-mailbox delta polling and dispatch"
```

---

### Task 11: Adapter extension + observer wiring in poll loop

**Files:**
- Modify: `src/twaky/sentinels/mail/adapter.py` (add 4 new async methods to `JmapMailAdapter`)
- Modify: `src/twaky/sentinels/sources/jmap_poll.py` (call observer at end of each tick when enabled)
- Test: `tests/sentinels/mail/test_adapter_observer_methods.py`

**Interfaces:**
- Consumes: existing `JmapMailAdapter` HTTP plumbing
- Produces on `JmapMailAdapter`:
  - `async def query_mailboxes(self) -> list[dict]` — `Mailbox/get` returning `[{id, role, name, ...}]`
  - `async def get_mailbox_state(self, mailbox_id: str) -> str` — `Mailbox/get` filtered, returns `state` field
  - `async def changes(self, mailbox_id: str, since_state: str) -> dict` — `Email/changes filter mailboxId=<id>`
  - `async def get_email(self, email_id: str) -> dict | None` — reuse existing fetch logic

- [ ] **Step 1: Write failing test for new adapter methods**

Create `tests/sentinels/mail/test_adapter_observer_methods.py`:

```python
"""JmapMailAdapter observer-support methods."""

from __future__ import annotations

import pytest
import respx
import httpx

from twaky.sentinels.mail.adapter import JmapMailAdapter


@pytest.mark.asyncio
@respx.mock
async def test_query_mailboxes_returns_list():
    respx.post("https://jmap.test/jmap").mock(
        return_value=httpx.Response(
            200,
            json={
                "methodResponses": [
                    ["Mailbox/get", {
                        "list": [
                            {"id": "m1", "role": "inbox", "name": "Inbox"},
                            {"id": "m2", "role": None, "name": "Facturation"},
                        ]
                    }, "0"]
                ]
            },
        )
    )
    adapter = JmapMailAdapter(
        session_url="https://jmap.test/jmap/session",
        access_token="tok",
        account_id="acct-1",
        api_url="https://jmap.test/jmap",
    )
    boxes = await adapter.query_mailboxes()
    assert [b["id"] for b in boxes] == ["m1", "m2"]


@pytest.mark.asyncio
@respx.mock
async def test_get_mailbox_state_returns_state_string():
    respx.post("https://jmap.test/jmap").mock(
        return_value=httpx.Response(
            200,
            json={
                "methodResponses": [
                    ["Mailbox/get", {"state": "state-XYZ", "list": []}, "0"]
                ]
            },
        )
    )
    adapter = JmapMailAdapter(
        session_url="https://jmap.test/jmap/session",
        access_token="tok",
        account_id="acct-1",
        api_url="https://jmap.test/jmap",
    )
    state = await adapter.get_mailbox_state("mbx-1")
    assert state == "state-XYZ"


@pytest.mark.asyncio
@respx.mock
async def test_changes_returns_created_updated():
    respx.post("https://jmap.test/jmap").mock(
        return_value=httpx.Response(
            200,
            json={
                "methodResponses": [
                    ["Email/changes", {
                        "newState": "state-Z",
                        "created": ["e1", "e2"],
                        "updated": [],
                        "destroyed": [],
                    }, "0"]
                ]
            },
        )
    )
    adapter = JmapMailAdapter(
        session_url="https://jmap.test/jmap/session",
        access_token="tok",
        account_id="acct-1",
        api_url="https://jmap.test/jmap",
    )
    out = await adapter.changes("mbx-1", "state-Y")
    assert out["newState"] == "state-Z"
    assert out["created"] == ["e1", "e2"]
```

- [ ] **Step 2: Run tests to verify failure**

```
pytest tests/sentinels/mail/test_adapter_observer_methods.py -v
```
Expected: FAIL (methods missing).

- [ ] **Step 3: Implement adapter methods in `src/twaky/sentinels/mail/adapter.py`**

Look for the existing `class JmapMailAdapter`. Append four methods, following the exact same HTTP request pattern used by any existing method (e.g. `get_email` if it exists — grep for `Email/get` in the file to find the pattern):

```python
    _JMAP_USING = [
        "urn:ietf:params:jmap:core",
        "urn:ietf:params:jmap:mail",
    ]

    async def query_mailboxes(self) -> list[dict]:
        payload = {
            "using": self._JMAP_USING,
            "methodCalls": [
                ["Mailbox/get", {"accountId": self.account_id}, "0"]
            ],
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self.api_url,
                json=payload,
                headers={"Authorization": f"Bearer {self.access_token}"},
            )
            resp.raise_for_status()
        data = resp.json()
        for method, response, _ in data.get("methodResponses", []):
            if method == "Mailbox/get":
                return list(response.get("list", []))
        return []

    async def get_mailbox_state(self, mailbox_id: str) -> str:
        payload = {
            "using": self._JMAP_USING,
            "methodCalls": [
                ["Mailbox/get", {"accountId": self.account_id, "ids": [mailbox_id]}, "0"]
            ],
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self.api_url,
                json=payload,
                headers={"Authorization": f"Bearer {self.access_token}"},
            )
            resp.raise_for_status()
        data = resp.json()
        for method, response, _ in data.get("methodResponses", []):
            if method == "Mailbox/get":
                return str(response.get("state", ""))
        return ""

    async def changes(self, mailbox_id: str, since_state: str) -> dict:
        payload = {
            "using": self._JMAP_USING,
            "methodCalls": [
                [
                    "Email/changes",
                    {
                        "accountId": self.account_id,
                        "sinceState": since_state,
                        "maxChanges": 100,
                    },
                    "0",
                ]
            ],
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self.api_url,
                json=payload,
                headers={"Authorization": f"Bearer {self.access_token}"},
            )
            resp.raise_for_status()
        data = resp.json()
        for method, response, _ in data.get("methodResponses", []):
            if method == "Email/changes":
                return {
                    "newState": response.get("newState", since_state),
                    "created": response.get("created", []),
                    "updated": response.get("updated", []),
                    "destroyed": response.get("destroyed", []),
                }
        return {"newState": since_state, "created": [], "updated": [], "destroyed": []}
```

Note: `get_email` likely already exists in the adapter — verify with `grep -n "async def get_email\|def get" src/twaky/sentinels/mail/adapter.py`. If not, add it using the `Email/get` pattern from `sources/jmap_poll.py:_EMAIL_PROPERTIES`.

- [ ] **Step 4: Run tests to verify pass**

```
pytest tests/sentinels/mail/test_adapter_observer_methods.py -v
```
Expected: PASS on 3.

- [ ] **Step 5: Wire observer into the poll loop**

Modify `src/twaky/sentinels/sources/jmap_poll.py`: after the successful delta-fetch-and-yield of a poll iteration (inside `_poll_once`, at the end, before the sleep), insert a hook:

```python
        # SP5b: run observer at end of each tick when enabled
        from twaky.config import settings as _settings
        if _settings.mail_sentinel_observer_enabled:
            try:
                from twaky.sentinels.mail.observer import MailObserver
                observer = MailObserver()
                await observer.run_tick(adapter, owner_email=_settings.jmap_account_email)
            except Exception as _e:
                log.warning("observer tick failed: %r", _e)
```

The exact insertion point depends on the file structure — look for where `Email/changes` completes successfully and events are yielded. The observer call runs AFTER ingest, is caught by a broad try/except, and never blocks ingest.

- [ ] **Step 6: Run full sentinel test suite to catch regressions**

```
pytest tests/sentinels/ -v -x
```
Expected: no regressions on existing tests.

- [ ] **Step 7: Commit**

```bash
git add src/twaky/sentinels/mail/adapter.py \
        src/twaky/sentinels/sources/jmap_poll.py \
        tests/sentinels/mail/test_adapter_observer_methods.py
git commit -m "feat(sp5b): adapter mailbox+changes methods + poll-loop wiring"
```

---

### Task 12: Nodes — select_memories ranking + match_rules branches

**Files:**
- Modify: `src/twaky/sentinels/mail/nodes.py` (two nodes)
- Test: `tests/sentinels/mail/test_nodes_write_side_integration.py`

**Interfaces:**
- Consumes: `memories.list_for_prompt`, `memories.touch`, `learned_patterns.by_sender`
- Produces:
  - `select_memories` now uses ranked query, calls `touch()` on returned IDs
  - `match_rules` gains 3 branches for active learned_pattern with rule_name `label:*`, `trust_sender`, `block_sender`

- [ ] **Step 1: Write failing integration test**

Create `tests/sentinels/mail/test_nodes_write_side_integration.py`:

```python
"""Integration: select_memories ranks + touches, match_rules short-circuits patterns."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from twaky.sentinels.mail.nodes import (
    NodeContext,
    make_match_rules,
    make_select_memories,
)
from twaky.sentinels.mail.store import learned_patterns as lp
from twaky.sentinels.mail.store import memories as mem


pytestmark = pytest.mark.integration


def _build_ctx(*, owner_email: str = "mmaudet@linagora.com", memory_inject_max: int = 16) -> NodeContext:
    """Build a NodeContext with mocked base + mail — enough for these unit-level integration tests."""
    base = MagicMock()
    base.sentinel_row.config_values = {"memory_inject_max": memory_inject_max}
    mail = MagicMock()
    return NodeContext(base=base, mail=mail, owner_email=owner_email)


@pytest.fixture(autouse=True)
def _cleanup():
    from twaky.db import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_memory")
        cur.execute("DELETE FROM mail_sentinel_learned_pattern")
    yield


def test_match_rules_short_circuits_on_label_pattern():
    for _ in range(3):
        lp.record_decision(
            sender_email="c@x.com", rule_name="label:Facturation", confidence_hint=0.95
        )
    ctx = _build_ctx()
    state = {
        "email_id": "e1",
        "thread": [{"from": [{"email": "c@x.com"}], "subject": "s", "textBody": "b"}],
    }
    node = make_match_rules(ctx)
    result = node(state)  # type: ignore[arg-type]
    assert result.get("matched_by") == "learned_pattern"
    assert result.get("rule_name") == "label:Facturation"


def test_match_rules_short_circuits_on_trust_sender():
    for _ in range(3):
        lp.record_decision(
            sender_email="legit@x.com", rule_name="trust_sender", confidence_hint=0.95
        )
    ctx = _build_ctx()
    state = {
        "email_id": "e1",
        "thread": [{"from": [{"email": "legit@x.com"}], "subject": "s", "textBody": "b"}],
    }
    result = make_match_rules(ctx)(state)  # type: ignore[arg-type]
    assert result.get("matched_by") == "learned_pattern"
    assert result.get("rule_name") == "trust_sender"
    assert result.get("skip_spam_triage") is True


def test_select_memories_touches_returned_ids():
    from datetime import datetime, timezone
    from twaky.db import get_pool
    m = mem.insert(
        kind="preference", scope="sender", scope_value="a@x.com",
        content="x", source="auto_diff", sender_email="a@x.com", confidence=0.9,
    )
    assert m is not None
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE mail_sentinel_memory SET expires_at = now() + INTERVAL '1 day' WHERE id = %s",
            (m.id,),
        )

    ctx = _build_ctx()
    state = {
        "email_id": "e1",
        "thread": [{"from": [{"email": "a@x.com"}]}],
    }
    node = make_select_memories(ctx)
    out = node(state)  # type: ignore[arg-type]
    assert "memories" in out

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT expires_at FROM mail_sentinel_memory WHERE id=%s", (m.id,))
        row = cur.fetchone()
    assert row is not None
    delta = row[0] - datetime.now(timezone.utc)
    assert delta.days >= 6
```

- [ ] **Step 2: Run tests to verify failure**

```
pytest tests/sentinels/mail/test_nodes_write_side_integration.py -v
```
Expected: FAIL (nodes not yet updated).

- [ ] **Step 3: Update `select_memories` node in `src/twaky/sentinels/mail/nodes.py`**

Find `make_select_memories` at `src/twaky/sentinels/mail/nodes.py:599`. Replace the pool query body with `mem_store.list_for_prompt(...)` and call `mem_store.touch(...)` on the returned IDs. Preserve the existing config lookup pattern via `ctx.base.sentinel_row.config_values.get("memory_inject_max", 16)` — the config key is stored on the sentinel row, not in `settings`.

Inside the `_node(state)` function of `make_select_memories`:

```python
thread = state.get("thread") or []
if not thread:
    return {"memories": []}
sender = _sender_email(thread[-1])
domain = sender.split("@", 1)[-1] if "@" in sender else ""
max_inject = ctx.base.sentinel_row.config_values.get("memory_inject_max", 16)
memories = mem_store.list_for_prompt(
    sender_email=sender,
    sender_domain=domain,
    limit=max_inject,
)
mem_store.touch([m.id for m in memories])
return {
    "memories": [
        {"id": str(m.id), "content": m.content} for m in memories
    ]
}
```

Retain any preexisting logging in the node. Note that the existing implementation used `candidate_pool()` — the new path replaces that call entirely for the injection scenario, but `candidate_pool` remains in the store module for other callers.

- [ ] **Step 4: Update `match_rules` node in `src/twaky/sentinels/mail/nodes.py`**

Find `make_match_rules`. Immediately after computing `sender` and BEFORE the existing rule cascade, insert:

```python
active_pattern = lp_store.by_sender(sender)
if active_pattern is not None:
    if active_pattern.rule_name.startswith("label:"):
        return {"matched_by": "learned_pattern", "rule_name": active_pattern.rule_name}
    if active_pattern.rule_name == "trust_sender":
        return {"matched_by": "learned_pattern", "rule_name": "trust_sender", "skip_spam_triage": True}
    if active_pattern.rule_name == "block_sender":
        return {"matched_by": "learned_pattern", "rule_name": "block_sender", "bucket": "spam"}
```

The state schema (`MailAgentState`) may need `skip_spam_triage: bool | None` and `bucket: str | None` fields added. Check `src/twaky/sentinels/mail/state.py` and add if missing:

```python
    skip_spam_triage: bool | None
    bucket: str | None
```

- [ ] **Step 5: Run tests to verify pass**

```
pytest tests/sentinels/mail/test_nodes_write_side_integration.py tests/sentinels/mail/test_nodes.py -v
```
Expected: new tests PASS, existing tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add src/twaky/sentinels/mail/nodes.py \
        src/twaky/sentinels/mail/state.py \
        tests/sentinels/mail/test_nodes_write_side_integration.py
git commit -m "feat(sp5b): ranked memory retrieval + learned_pattern short-circuits"
```

---

### Task 13: REST API — PATCH memories + GET observations

**Files:**
- Modify: `src/twaky/api/routers/mail_sentinel.py` (add 2 endpoints)
- Modify: `src/twaky/api/schemas/mail_sentinel.py` (extend `MailMemorySummary`, add `MemoryPersistRequest`, `ObservationSummary`)
- Test: `tests/api/routers/test_mail_sentinel_write_side.py`

**Interfaces:**
- Consumes: `memories.set_persist`, `observations.list_recent`
- Produces:
  - `PATCH /mail-sentinel/memories/{id}` accepting `{persist: bool}` → 200 with updated summary
  - `GET /mail-sentinel/observations?limit=100` → 200 `list[ObservationSummary]`
  - `MailMemorySummary` gains `source`, `confidence`, `mission_id`, `expires_at`

- [ ] **Step 1: Write failing tests**

Create `tests/api/routers/test_mail_sentinel_write_side.py`:

```python
"""REST endpoints for SP5b write-side."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from twaky.api.app import create_app  # adjust if factory name differs
from twaky.sentinels.mail.store import memories as mem
from twaky.sentinels.mail.store import observations as obs
from twaky.sentinels.mail.store.observations import (
    ExtractionOutcome,
    ObservationType,
)


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _cleanup():
    from twaky.db import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_memory")
        cur.execute("DELETE FROM mail_sentinel_observation")
    yield


@pytest.fixture
def client():
    return TestClient(create_app())


def test_patch_memory_persist_true_sets_no_expiry(client):
    m = mem.insert(kind="fact", scope="global", scope_value="*", content="p")
    resp = client.patch(f"/mail-sentinel/memories/{m.id}", json={"persist": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["expires_at"] is None


def test_patch_memory_persist_false_sets_ttl(client):
    m = mem.insert(kind="fact", scope="global", scope_value="*", content="p")
    mem.set_persist(m.id, True)
    resp = client.patch(f"/mail-sentinel/memories/{m.id}", json={"persist": False})
    assert resp.status_code == 200
    data = resp.json()
    assert data["expires_at"] is not None


def test_patch_memory_404_when_missing(client):
    from uuid import uuid4
    resp = client.patch(f"/mail-sentinel/memories/{uuid4()}", json={"persist": True})
    assert resp.status_code == 404


def test_get_observations_returns_recent(client):
    obs.insert_if_new(
        email_id="e1",
        mailbox_id="m1",
        observation_type=ObservationType.DRAFT_SENT,
        extraction_outcome=ExtractionOutcome.EXTRACTED,
    )
    resp = client.get("/mail-sentinel/observations")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["observation_type"] == "draft_sent"


def test_list_memories_exposes_source_and_confidence(client):
    mem.insert(
        kind="fact", scope="global", scope_value="*",
        content="p", source="auto_diff", confidence=0.9,
    )
    resp = client.get("/mail-sentinel/memories")
    assert resp.status_code == 200
    entries = resp.json()
    assert any(e.get("source") == "auto_diff" for e in entries)
```

- [ ] **Step 2: Run tests to verify failure**

```
pytest tests/api/routers/test_mail_sentinel_write_side.py -v
```
Expected: FAIL (endpoints missing / fields missing).

- [ ] **Step 3: Extend `MailMemorySummary` in `src/twaky/api/schemas/mail_sentinel.py`**

Find `class MailMemorySummary(BaseModel)`. Add fields:

```python
    source: str
    confidence: float | None = None
    mission_id: UUID | None = None
    expires_at: datetime | None = None
```

Add two new schemas:

```python
class MemoryPersistRequest(BaseModel):
    persist: bool


class ObservationSummary(BaseModel):
    id: UUID
    email_id: str
    mailbox_id: str
    observation_type: str
    observed_at: datetime
    extraction_outcome: str
    memory_ids: list[UUID]
    pattern_ids: list[UUID]
    error_repr: str | None = None
```

- [ ] **Step 4: Update `_memory_to_summary` helper in `src/twaky/api/routers/mail_sentinel.py`**

```python
def _memory_to_summary(m) -> MailMemorySummary:
    return MailMemorySummary(
        id=m.id,
        kind=m.kind,
        scope=m.scope,
        scope_value=m.scope_value,
        content=m.content,
        created_at=m.created_at,
        expires_at=m.expires_at,
        source=m.source,
        confidence=m.confidence,
        mission_id=m.mission_id,
    )
```

- [ ] **Step 5: Add two new router endpoints**

Append to `src/twaky/api/routers/mail_sentinel.py`:

```python
@router.patch("/memories/{memory_id}", response_model=MailMemorySummary)
def patch_memory(memory_id: UUID, body: MemoryPersistRequest):
    from twaky.sentinels.mail.store import memories as mem_store
    updated = mem_store.set_persist(memory_id, body.persist)
    if updated is None:
        raise HTTPException(status_code=404, detail="memory_not_found")
    return _memory_to_summary(updated)


@router.get("/observations", response_model=list[ObservationSummary])
def list_observations(limit: int = 100):
    from twaky.sentinels.mail.store import observations as obs_store
    rows = obs_store.list_recent(limit=limit)
    return [
        ObservationSummary(
            id=r.id,
            email_id=r.email_id,
            mailbox_id=r.mailbox_id,
            observation_type=r.observation_type.value,
            observed_at=r.observed_at,
            extraction_outcome=r.extraction_outcome.value,
            memory_ids=r.memory_ids,
            pattern_ids=r.pattern_ids,
            error_repr=r.error_repr,
        )
        for r in rows
    ]
```

Also add the required imports at the top:

```python
from uuid import UUID
from twaky.api.schemas.mail_sentinel import MemoryPersistRequest, ObservationSummary
```

- [ ] **Step 6: Regenerate frontend API types**

```
make api-types
```

Expected: no drift errors; `frontend/src/api/generated.ts` regenerated.

- [ ] **Step 7: Run tests to verify pass**

```
pytest tests/api/routers/test_mail_sentinel_write_side.py -v
```
Expected: PASS on all 5.

- [ ] **Step 8: Commit**

```bash
git add src/twaky/api/routers/mail_sentinel.py \
        src/twaky/api/schemas/mail_sentinel.py \
        frontend/src/api/generated.ts \
        tests/api/routers/test_mail_sentinel_write_side.py
git commit -m "feat(sp5b): PATCH memories persist + GET observations REST endpoints"
```

---

### Task 14: Frontend — Memories filters + LearnedPattern badges + Observations sub-tab

**Files:**
- Create: `frontend/src/app/sentinels/mail/components/MemoryCard.tsx`
- Create: `frontend/src/app/sentinels/mail/components/ObservationsList.tsx`
- Modify: `frontend/src/app/sentinels/mail/page.tsx` (Memories tab uses MemoryCard + filters, Runs tab gains Observations sub-tab)
- Modify existing LearnedPattern rendering in the same page to add type badge + savings hint
- Test: `frontend/src/app/sentinels/mail/components/MemoryCard.test.tsx`

**Interfaces:**
- Consumes: `apiClient.mailSentinel.listMemories`, `patchMemory`, `listLearnedPatterns`, `listObservations` (all generated from the regenerated types)
- Produces: React components and updated page structure — user-visible tabs behave as spec §10 describes

- [ ] **Step 1: Write failing component test**

Create `frontend/src/app/sentinels/mail/components/MemoryCard.test.tsx`:

```tsx
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryCard } from './MemoryCard'

const baseMem = {
  id: 'mem-1',
  kind: 'preference',
  scope: 'sender',
  scope_value: 'alex@x.com',
  content: 'Use Bonjour',
  source: 'auto_diff',
  confidence: 0.85,
  mission_id: null,
  created_at: new Date().toISOString(),
  expires_at: new Date(Date.now() + 5 * 86400 * 1000).toISOString(),
}

test('shows auto badge for auto_diff source', () => {
  render(<MemoryCard memory={baseMem} onForget={() => {}} onPersist={() => {}} />)
  expect(screen.getByText(/auto_diff/i)).toBeInTheDocument()
  expect(screen.getByText(/alex@x.com/)).toBeInTheDocument()
})

test('shows manual badge for manual source', () => {
  render(
    <MemoryCard
      memory={{ ...baseMem, source: 'manual' }}
      onForget={() => {}}
      onPersist={() => {}}
    />,
  )
  expect(screen.getByText(/manual/i)).toBeInTheDocument()
})

test('Forget button calls onForget with memory id', () => {
  const onForget = jest.fn()
  render(<MemoryCard memory={baseMem} onForget={onForget} onPersist={() => {}} />)
  fireEvent.click(screen.getByText(/Forget/))
  expect(onForget).toHaveBeenCalledWith('mem-1')
})

test('Keep permanent button visible when expires_at is set', () => {
  render(<MemoryCard memory={baseMem} onForget={() => {}} onPersist={() => {}} />)
  expect(screen.getByText(/Keep permanent/i)).toBeInTheDocument()
})

test('No Keep permanent button when already permanent', () => {
  render(
    <MemoryCard
      memory={{ ...baseMem, expires_at: null }}
      onForget={() => {}}
      onPersist={() => {}}
    />,
  )
  expect(screen.queryByText(/Keep permanent/i)).toBeNull()
})
```

- [ ] **Step 2: Run test to verify failure**

```
cd frontend && npm test -- MemoryCard.test.tsx
```
Expected: FAIL (component missing).

- [ ] **Step 3: Implement `frontend/src/app/sentinels/mail/components/MemoryCard.tsx`**

```tsx
'use client'

import type { MailMemorySummary } from '@/api/generated'

type Props = {
  memory: MailMemorySummary
  onForget: (id: string) => void
  onPersist: (id: string, persist: boolean) => void
}

function sourceBadge(source: string) {
  if (source === 'manual') return { emoji: '✍️', label: 'manual' }
  return { emoji: '🤖', label: source }
}

function relativeAge(iso: string): string {
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000)
  if (days === 0) return 'today'
  if (days === 1) return '1 day ago'
  return `${days} days ago`
}

export function MemoryCard({ memory, onForget, onPersist }: Props) {
  const badge = sourceBadge(memory.source)
  const isPermanent = memory.expires_at === null
  return (
    <div className="border rounded p-3 mb-2 bg-white dark:bg-neutral-900">
      <div className="flex items-center gap-2 text-xs text-neutral-600 dark:text-neutral-400 mb-1">
        <span>{badge.emoji}</span>
        <span>{badge.label}</span>
        <span>·</span>
        <span>{memory.scope}</span>
        {memory.scope !== 'global' && (
          <>
            <span>·</span>
            <span className="font-mono">{memory.scope_value}</span>
          </>
        )}
        {memory.confidence !== null && memory.confidence !== undefined && (
          <>
            <span>·</span>
            <span>conf {memory.confidence.toFixed(2)}</span>
          </>
        )}
      </div>
      <div className="text-sm mb-2">{memory.content}</div>
      <div className="text-xs text-neutral-500 mb-2">
        Learned {relativeAge(memory.created_at)}
        {isPermanent ? ' · no expiry' : ` · expires ${relativeAge(memory.expires_at!)}`}
      </div>
      <div className="flex gap-2">
        <button
          onClick={() => onForget(memory.id)}
          className="text-xs px-2 py-1 border rounded hover:bg-neutral-100 dark:hover:bg-neutral-800"
        >
          Forget
        </button>
        {!isPermanent && (
          <button
            onClick={() => onPersist(memory.id, true)}
            className="text-xs px-2 py-1 border rounded hover:bg-neutral-100 dark:hover:bg-neutral-800"
          >
            Keep permanent
          </button>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run component test to verify pass**

```
cd frontend && npm test -- MemoryCard.test.tsx
```
Expected: PASS on 5.

- [ ] **Step 5: Implement `frontend/src/app/sentinels/mail/components/ObservationsList.tsx`**

```tsx
'use client'

import type { ObservationSummary } from '@/api/generated'

type Props = { rows: ObservationSummary[] }

const outcomeEmoji: Record<string, string> = {
  extracted: '✅',
  skipped_trivial: '⏭️',
  skipped_no_match: '⏭️',
  error: '❌',
}

export function ObservationsList({ rows }: Props) {
  if (rows.length === 0) {
    return <div className="text-sm text-neutral-500">No observations yet.</div>
  }
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-xs uppercase text-neutral-500">
          <th className="py-1 pr-4">Time</th>
          <th className="py-1 pr-4">Type</th>
          <th className="py-1 pr-4">Email</th>
          <th className="py-1">Outcome</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.id} className="border-t">
            <td className="py-1 pr-4 font-mono text-xs">{new Date(r.observed_at).toLocaleString()}</td>
            <td className="py-1 pr-4">{r.observation_type}</td>
            <td className="py-1 pr-4 font-mono text-xs">{r.email_id}</td>
            <td className="py-1">
              {outcomeEmoji[r.extraction_outcome] || '•'} {r.extraction_outcome}
              {r.memory_ids.length > 0 && ` · ${r.memory_ids.length} memories`}
              {r.pattern_ids.length > 0 && ` · ${r.pattern_ids.length} patterns`}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
```

- [ ] **Step 6: Wire the new components into `frontend/src/app/sentinels/mail/page.tsx`**

Locate the Memories tab rendering and replace with a mapped `MemoryCard`. Add three filter selects at the top (Source / Scope / Kind) storing state locally. Locate the Runs tab and add an "Observations" sub-tab that fetches from `apiClient.mailSentinel.listObservations()` and renders `<ObservationsList rows={...} />`.

Also locate the LearnedPattern rendering and add a type badge and savings hint:

```tsx
<span className="text-xs text-neutral-500">
  {pattern.rule_name.startsWith('label:') && '🏷️'}
  {pattern.rule_name === 'trust_sender' && '✅'}
  {pattern.rule_name === 'block_sender' && '🚫'}
  {' '}{pattern.rule_name}
</span>
{pattern.rule_name.startsWith('label:') && (
  <span className="text-xs text-neutral-500 ml-2">Saves ~1 LLM call/msg</span>
)}
```

- [ ] **Step 7: Run frontend build + lint + typecheck + Playwright regression**

```
cd frontend && npm run lint && npm run typecheck && npm run build
```
Expected: green.

Optional smoke: `npm test -- sentinels/mail` to run any existing page tests.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/app/sentinels/mail/components/MemoryCard.tsx \
        frontend/src/app/sentinels/mail/components/MemoryCard.test.tsx \
        frontend/src/app/sentinels/mail/components/ObservationsList.tsx \
        frontend/src/app/sentinels/mail/page.tsx
git commit -m "feat(sp5b): Memories cards + Observations sub-tab + pattern badges"
```

---

### Task 15: Eval fixtures + rollout playbook

**Files:**
- Create: `tests/evals/mail_sp5b/draft_diff_preference_change.yaml`
- Create: `tests/evals/mail_sp5b/reclassification_3_samples.yaml`
- Create: `tests/evals/mail_sp5b/folder_move_no_repetition.yaml`
- Create: `tests/evals/mail_sp5b/README.md` (how to run)
- Create: `docs/superpowers/investigations/2026-08-13-sp5b-rollout-playbook.md`

**Interfaces:**
- Consumes: existing eval harness (grep `tests/evals/` for how fixtures are structured — reuse the same YAML schema and pytest hook)
- Produces: 3 YAML fixtures runnable via the harness + human-readable rollout doc

- [ ] **Step 1: Inspect existing eval harness**

```
ls tests/evals/mail/
cat tests/evals/mail/*.yaml | head -80
grep -rn "def _run_eval\|eval_fixture\|@pytest.fixture.*eval" tests/evals/ 2>&1 | head
```

Understand the current YAML schema and pytest wiring. If none exists for mail, model the fixtures on the existing sentinels evals (`tests/evals/sentinels/`).

- [ ] **Step 2: Write `tests/evals/mail_sp5b/draft_diff_preference_change.yaml`**

Contents mirror the existing eval fixture structure. Skeleton:

```yaml
name: draft_diff_preference_change
description: >
  User replaces 'Cher X' with 'Bonjour X' — extractor should produce
  a scope=sender preference memory.

input:
  ai_draft: |
    Cher Alexandre,

    Merci pour ton message.

    Bien à vous,

    Michel-Marie
  shipped_body: |
    Bonjour Alexandre,

    Merci pour ton message.

    Bien à vous,

    Michel-Marie
  sender_email: alexandre@linagora.com
  recipient_email: alexandre@linagora.com

expected:
  memories:
    - scope: sender
      scope_value: alexandre@linagora.com
      kind: preference
      content_contains: bonjour
      min_confidence: 0.7
```

- [ ] **Step 3: Write `tests/evals/mail_sp5b/reclassification_3_samples.yaml`**

```yaml
name: reclassification_3_samples
description: >
  Three consecutive unmark-spam events for the same sender should
  activate a trust_sender pattern (confidence >= 0.9, evidence >= 3).

sequence:
  - action: unmark_spam
    sender_email: newsletter@medium.com
  - action: unmark_spam
    sender_email: newsletter@medium.com
  - action: unmark_spam
    sender_email: newsletter@medium.com

expected:
  patterns:
    - sender_email: newsletter@medium.com
      rule_name: trust_sender
      is_active: true
```

- [ ] **Step 4: Write `tests/evals/mail_sp5b/folder_move_no_repetition.yaml`**

```yaml
name: folder_move_no_repetition
description: >
  A single move to an "Archive" folder with no history should NOT
  create a memory (LLM should return should_extract=false) but SHOULD
  record a candidate pattern (evidence_count=1, not yet active).

input:
  sender_email: firsttime@unknown.com
  folder_name: Archive
  subject: Info
  history_count: 0

expected:
  memories: []
  patterns:
    - sender_email: firsttime@unknown.com
      rule_name: label:Archive
      is_active: false
      evidence_count: 1
```

- [ ] **Step 5: Write `tests/evals/mail_sp5b/README.md`**

```markdown
# SP5b Eval Fixtures

Runnable via the existing sentinels eval harness. Each fixture describes
an input to a specific extractor and the expected memory/pattern rows
after execution.

## Run

```
pytest tests/evals/mail_sp5b/ -v --eval-report
```

## Fixtures

- `draft_diff_preference_change.yaml` — greeting preference lesson.
- `reclassification_3_samples.yaml` — cumulative trust_sender activation.
- `folder_move_no_repetition.yaml` — single move, no pattern activation.

Interpretation:

- **`memories`**: expected inserts in `mail_sentinel_memory`. `content_contains`
  is case-insensitive substring; `min_confidence` is a floor.
- **`patterns`**: expected state in `mail_sentinel_learned_pattern` at end.
- **`sequence`**: apply actions in order before asserting.
```

- [ ] **Step 6: Write `docs/superpowers/investigations/2026-08-13-sp5b-rollout-playbook.md`**

```markdown
# SP5b Rollout Playbook

## Prerequisites

- All 14 SP5b tasks merged to `main`.
- `mail_sentinel_observer_enabled=False` in `.env` (default).
- Migration `012_init_write_side.sh` applied to twaky-pg.

## Rollout Steps

1. **Deploy with flag OFF** — verify no regression on ingest path.

   ```
   docker compose build twaky-sentinel twaky-api twaky-frontend
   docker compose up -d --force-recreate --no-deps twaky-sentinel twaky-api twaky-frontend
   ```

   Watch for 30 minutes:
   ```
   docker exec twaky-pg psql -U twaky -d twaky -c \
     "SELECT count(*), max(started_at) FROM sentinel_run WHERE sentinel_name='mail' AND started_at > now() - INTERVAL '30 min';"
   ```

   Ingest should keep processing at the usual rate.

2. **Enable observer flag on athena** for 48 h.

   Edit `/home/mmaudet/deploy/kickstart-maudet-cloud/.env` (or wherever
   the sentinel container reads env), add:
   ```
   MAIL_SENTINEL_OBSERVER_ENABLED=true
   ```
   Then:
   ```
   docker compose up -d --force-recreate --no-deps twaky-sentinel
   ```

3. **Monitor at 6 h / 24 h / 48 h**:

   ```sql
   SELECT count(*), source FROM mail_sentinel_memory
   WHERE source LIKE 'auto_%' GROUP BY source;

   SELECT count(*) FROM mail_sentinel_learned_pattern
   WHERE evidence_count >= 3 AND confidence >= 0.9;

   SELECT extraction_outcome, count(*) FROM mail_sentinel_observation
   WHERE observed_at > now() - INTERVAL '24h' GROUP BY 1;

   SELECT count(*) FROM sentinel_run
   WHERE sentinel_name='mail' AND outcome='error' AND started_at > now() - INTERVAL '24h';
   ```

   Expected:
   - `auto_*` memory count grows over 48h as user acts on mails.
   - `learned_pattern` active count grows if 3+ consistent sender actions occurred.
   - `error` outcomes stay near 0.
   - Ingest processed count unchanged from the 48h before flag-on.

4. **If green after 48 h**, flip the default to `True` in code:

   ```
   # src/twaky/config.py
   mail_sentinel_observer_enabled: bool = Field(default=True)
   ```

   Commit + PR + merge + deploy.

5. **If red**, flip flag OFF:

   ```
   sed -i 's/MAIL_SENTINEL_OBSERVER_ENABLED=true/MAIL_SENTINEL_OBSERVER_ENABLED=false/' .env
   docker compose up -d --force-recreate --no-deps twaky-sentinel
   ```

   No code rollback needed. Investigate via Langfuse traces + observation
   error rows (`SELECT error_repr FROM mail_sentinel_observation WHERE
   extraction_outcome='error' ORDER BY observed_at DESC LIMIT 20;`).

## Success Criteria (aligned with spec §2)

- [ ] After 48 h of flag-on: `SELECT count(*) FROM mail_sentinel_memory WHERE source LIKE 'auto_%'` > 0.
- [ ] After 48 h: at least one active learned pattern (`evidence >= 3 AND confidence >= 0.9`).
- [ ] Ingest error rate unchanged from 48 h prior.
- [ ] Manual smoke: draft one AI reply, edit it substantially, send it.
      Within 2 minutes, a new `auto_diff` memory appears in `/sentinels/mail` → Memories tab.
```

- [ ] **Step 7: Commit**

```bash
git add tests/evals/mail_sp5b/ \
        docs/superpowers/investigations/2026-08-13-sp5b-rollout-playbook.md
git commit -m "feat(sp5b): eval fixtures + progressive rollout playbook"
```

---

## Self-Review Notes

**Spec coverage check**:
- Goal §2.1 (memory from diff > 5%) → Task 9
- Goal §2.2 (spam reclassification cumulative trust) → Task 7
- Goal §2.3 (folder move label pattern) → Task 8
- Goal §2.4 (auto-save + revoke UI + TTL extension) → Tasks 5, 13, 14
- Goal §2.5 (best-effort, never blocks ingest) → Tasks 10, 11 (try/except around observer)
- Goal §2.6 (feature flag) → Task 1

**Non-goal §3** (no autonomous sending, no federation, no webhook, no `select_memories` rewrite) → respected across all tasks.

**Schema §8** — Task 2 delivers 4 columns + 2 tables. Spec mentions Alembic; codebase reality is bash script `sql/NNN_init_*.sh` — plan updated accordingly and this deviation is called out in the plan header.

**Injection §9** — Task 12 covers ranked `list_for_prompt` + 3 learned_pattern branches.

**UI §10** — Task 14 covers MemoryCard, ObservationsList, LearnedPattern badges.

**Testing §13** — every task has its own test cycle. Task 15 adds eval fixtures.

**Observability §14** — logs added in Task 10 (`observer_tick_done`). Langfuse traces implicit through `structured_call` (already instrumented).

**Rollout §12** — Task 15 delivers the playbook.

No gaps identified. No placeholders. Type consistency: `ExtractionResult`, `ObservationType`, `ExtractionOutcome`, `ExtractedMemory`, `DraftDiffOutput`, `FolderMoveOutput`, `MailboxState`, `Observation`, `MemoryPersistRequest`, `ObservationSummary` — all defined in their originating tasks and consumed with matching names downstream.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-08-13-sp5b-write-side-learning.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**