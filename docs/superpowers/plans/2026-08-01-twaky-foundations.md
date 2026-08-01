# Twaky Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the Foundations invariants (Mission model + owner scoping + mail ingest + LangGraph seam + documented P2P envelope) that every subsequent twaky sub-project builds on.

**Architecture:** Add a `mission` table + a state-machine engine (`src/twaky/missions/`) on top of the existing Postgres. Introduce `TWAKY_OWNER_EMAIL` + a filter dispatch in the ingest worker so `event_log` stays owner-scoped. Bind 4 new `mail:message:*` exchanges with metadata-only Email mappers. Use `langgraph-checkpoint-postgres` as the fine-grained execution store, keyed by `thread_id = mission.id`. Ship a Pydantic model for the future P2P envelope (no wire code).

**Tech Stack:** Python 3.12, uv, psycopg[binary,pool] (raw — matches existing `src/twaky/db.py` pattern), pydantic v2 + pydantic-settings, langgraph 1.x + langgraph-checkpoint-postgres (new dep), aio-pika, langfuse 3.x, pytest + ruff + mypy.

## Global Constraints

- All new code Python 3.12, matches existing conventions in `src/twaky/`.
- Persistence via **raw psycopg3** (matching `src/twaky/db.py`) — do NOT introduce SQLAlchemy for one table. Pydantic models sit on top for validation/serialization.
- All new modules pass `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src/`.
- New unit tests must run in <1s each with no infra (mock the pool).
- New integration tests use `TWAKY_TEST_DSN` env var and self-skip when unset, matching `tests/test_projector_idempotence.py`.
- Every task ends with an atomic commit: message under 72 chars for subject, imperative mood, one logical change.
- No new deps beyond `langgraph-checkpoint-postgres`. If a task needs another, stop and ask.
- `TWAKY_OWNER_EMAIL` is required in `Settings`. Missing = process refuses to start (fail-fast at boot).
- New SQL init scripts follow the existing `sql/00N_init_*.sh` naming.

---

## File Structure

**New files (create):**

| Path | Responsibility |
|---|---|
| `src/twaky/missions/__init__.py` | Package marker; re-exports public API (`Mission`, `engine`, `Envelope`). |
| `src/twaky/missions/models.py` | Pydantic `Mission` model + `PlanStep` + enum `MissionState`. |
| `src/twaky/missions/repository.py` | psycopg CRUD on `mission` table (insert, get, update_state, list_live, `SELECT FOR UPDATE`). |
| `src/twaky/missions/guards.py` | Pure state-machine `check_transition()` + `InvalidTransition` exception. |
| `src/twaky/missions/engine.py` | The 7 public transition functions. Wraps repository + guards + Langfuse. |
| `src/twaky/missions/checkpointer.py` | Thin factory for `langgraph.checkpoint.postgres.PostgresSaver` + `setup()` at boot. |
| `src/twaky/missions/recovery.py` | `resume_missions_after_restart()` — scans live missions, reconciles with checkpointer. |
| `src/twaky/missions/envelope.py` | Pydantic `Envelope` model + `Intent` enum for the future P2P protocol. |
| `src/twaky/mappers/mail_message_received.py` | Cypher for a new Email node. |
| `src/twaky/mappers/mail_message_expunged.py` | Marks Email deleted. |
| `src/twaky/mappers/mail_message_flags_updated.py` | Sets Email.read from `\Seen` flag. |
| `src/twaky/mappers/mail_message_moved.py` | Updates Email.mailbox_path. |
| `src/twaky/owner_filter.py` | `matches_owner(exchange, payload, owner_email)` dispatch. |
| `sql/004_init_mission.sh` | Creates `mission` table + indexes. Runs at first-boot volume init. |
| `sql/005_init_checkpointer.sh` | Runs `PostgresSaver.setup()` via `python -c`. |
| `tests/missions/__init__.py` | Empty. |
| `tests/missions/test_guards.py` | State-machine unit tests. |
| `tests/missions/test_models.py` | Pydantic Mission model tests. |
| `tests/missions/test_repository.py` | psycopg CRUD integration (self-skips w/o DB). |
| `tests/missions/test_engine.py` | Engine transitions integration (self-skips w/o DB). |
| `tests/missions/test_checkpointer.py` | Put/get/delete via PostgresSaver (self-skips w/o DB). |
| `tests/missions/test_recovery.py` | Restart recovery integration (self-skips w/o DB). |
| `tests/missions/test_envelope.py` | Envelope Pydantic validation. |
| `tests/ingest/__init__.py` | Empty. |
| `tests/ingest/test_owner_filter.py` | Unit tests on `matches_owner()` dispatch. |
| `tests/mappers/test_mail_mappers.py` | Cypher shape assertions for the 4 mail mappers. |
| `tests/integration/__init__.py` | Empty. |
| `tests/integration/test_mail_roundtrip.py` | Publish synth mail event → event_log → graph Email node. |
| `scripts/scenarios-foundations.sh` | E2E: owner filter + mail + full mission lifecycle + crash recovery. |

**Modified files:**

| Path | What changes |
|---|---|
| `src/twaky/config.py` | Add `twaky_owner_email: str` (required). Add `mail:message:*` to `agent_exchanges` default. |
| `src/twaky/ingest.py` | Wire `matches_owner()` filter before `_insert_event`. |
| `src/twaky/mappers/__init__.py` | Register 4 mail mappers. |
| `src/twaky/db.py` | Add `get_langgraph_dsn()` helper (same DSN, exposed separately for the checkpointer). |
| `pyproject.toml` | Add `langgraph-checkpoint-postgres>=2.0`. |
| `.env.example` | Add `TWAKY_OWNER_EMAIL=`. |
| `Makefile` | Add `scenarios-foundations` target. |

---

## Task 1: Config — `TWAKY_OWNER_EMAIL` required env var

**Files:**
- Modify: `src/twaky/config.py`
- Modify: `.env.example`
- Create: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `settings.twaky_owner_email: str` (required, fail-fast if unset).

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:
```python
"""Config validation tests — owner email must be required."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from twaky.config import Settings


def test_owner_email_required_missing_raises(monkeypatch):
    monkeypatch.delenv("TWAKY_OWNER_EMAIL", raising=False)
    with pytest.raises(ValidationError) as ei:
        Settings(_env_file=None)  # type: ignore[call-arg]
    assert "twaky_owner_email" in str(ei.value).lower()


def test_owner_email_present_ok(monkeypatch):
    monkeypatch.setenv("TWAKY_OWNER_EMAIL", "alice@example.com")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.twaky_owner_email == "alice@example.com"


def test_agent_exchanges_default_includes_mail(monkeypatch):
    monkeypatch.setenv("TWAKY_OWNER_EMAIL", "alice@example.com")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert "mail:message:received" in s.exchanges
    assert "mail:message:expunged" in s.exchanges
    assert "mail:message:flags:updated" in s.exchanges
    assert "mail:message:moved" in s.exchanges
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `AttributeError` or import errors (field doesn't exist).

- [ ] **Step 3: Add the field to `src/twaky/config.py`**

Edit `src/twaky/config.py`. Add after existing `Field` imports:
```python
    # --- Owner scoping (required — fail-fast if missing) ---
    twaky_owner_email: str = Field(
        ...,  # required — no default
        description="Email of the sole owner this instance serves.",
    )
```

Update the default of `agent_exchanges` to include the four mail exchanges (comma-separated, existing string format):
```python
    agent_exchanges: str = Field(
        default=(
            "calendar:event:created,calendar:event:updated,calendar:event:request,"
            "calendar:event:deleted,calendar:event:cancel,calendar:event:reply,"
            "sabre:contact:created,sabre:contact:updated,sabre:contact:update,"
            "sabre:contact:deleted,"
            "mail:message:received,mail:message:expunged,"
            "mail:message:flags:updated,mail:message:moved"
        ),
        description="Comma-separated fanout exchanges to bind to.",
    )
```

- [ ] **Step 4: Update `.env.example`**

Add at the top under the Postgres block:
```
# --- Owner scoping (REQUIRED — instance refuses to start without it) ---
TWAKY_OWNER_EMAIL=you@twake-dev.maudet.cloud
```

- [ ] **Step 5: Update `.env`** (this file is gitignored; done locally, not committed)

```
TWAKY_OWNER_EMAIL=michel.maudet@linagora.com
```

- [ ] **Step 6: Run tests + lint**

```bash
uv run pytest tests/test_config.py -v
uv run ruff check src/twaky/config.py tests/test_config.py
uv run mypy src/twaky/config.py
```
Expected: PASS everywhere.

- [ ] **Step 7: Commit**

```bash
git add src/twaky/config.py .env.example tests/test_config.py
git commit -m "feat(config): TWAKY_OWNER_EMAIL required + mail exchanges in default"
```

---

## Task 2: Owner filter dispatch

**Files:**
- Create: `src/twaky/owner_filter.py`
- Create: `tests/ingest/__init__.py` (empty)
- Create: `tests/ingest/test_owner_filter.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `matches_owner(exchange: str, payload: dict, owner_email: str) -> bool`.

- [ ] **Step 1: Write the failing test**

Create `tests/ingest/__init__.py` (empty).

Create `tests/ingest/test_owner_filter.py`:
```python
"""Owner-filter dispatch table — one unit test per family."""

from __future__ import annotations

import pytest

from twaky.owner_filter import matches_owner

OWNER = "alice@example.com"


class TestCalendar:
    def test_owner_is_organizer(self):
        p = {"uid": "e1", "organizer": {"email": OWNER}, "attendees": []}
        assert matches_owner("calendar:event:created", p, OWNER)

    def test_owner_is_attendee(self):
        p = {"uid": "e1", "organizer": {"email": "x@y"}, "attendees": [{"email": OWNER}]}
        assert matches_owner("calendar:event:updated", p, OWNER)

    def test_owner_neither(self):
        p = {"uid": "e1", "organizer": {"email": "x@y"}, "attendees": [{"email": "z@y"}]}
        assert not matches_owner("calendar:event:created", p, OWNER)

    def test_owner_with_missing_fields(self):
        assert not matches_owner("calendar:event:created", {}, OWNER)


class TestSabreContact:
    def test_owner_matches_email(self):
        p = {"email": OWNER, "fn": "Alice"}
        assert matches_owner("sabre:contact:created", p, OWNER)

    def test_owner_no_match(self):
        p = {"email": "someone@else", "fn": "Someone"}
        assert not matches_owner("sabre:contact:updated", p, OWNER)


class TestMail:
    def test_owner_is_mailbox_user(self):
        p = {"user": OWNER, "message_id": "m1"}
        assert matches_owner("mail:message:received", p, OWNER)

    def test_owner_no_match(self):
        p = {"user": "other@example.com", "message_id": "m1"}
        assert not matches_owner("mail:message:expunged", p, OWNER)


class TestUnknown:
    def test_unknown_exchange_drops(self):
        # Safe default: unknown → False (drop). Never pollute the graph.
        assert not matches_owner("something:else", {"anything": True}, OWNER)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/test_owner_filter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'twaky.owner_filter'`.

- [ ] **Step 3: Create the module**

Create `src/twaky/owner_filter.py`:
```python
"""Dispatch that decides whether an event on a given exchange concerns
the owner of this twaky instance.

Applied at ingest time, BEFORE inserting into event_log — events that
don't concern the owner are ack'd and dropped silently (no DLQ, no
log noise, no storage cost).
"""

from __future__ import annotations

from collections.abc import Callable

_Matcher = Callable[[dict, str], bool]


def _match_calendar_event(payload: dict, owner: str) -> bool:
    org = (payload.get("organizer") or {}).get("email")
    if org == owner:
        return True
    for att in payload.get("attendees") or []:
        if isinstance(att, dict) and att.get("email") == owner:
            return True
    return False


def _match_sabre_contact(payload: dict, owner: str) -> bool:
    # Assumption to validate with a real payload: the contact's own
    # address book is the owner's, so `payload.email == owner`.
    return payload.get("email") == owner


def _match_mail(payload: dict, owner: str) -> bool:
    return payload.get("user") == owner


_RULES: dict[str, _Matcher] = {
    "calendar:event:": _match_calendar_event,
    "sabre:contact:": _match_sabre_contact,
    "mail:message:": _match_mail,
}


def matches_owner(exchange: str, payload: dict, owner_email: str) -> bool:
    """Return True iff the event concerns the owner. Unknown families → False."""
    for prefix, rule in _RULES.items():
        if exchange.startswith(prefix):
            return rule(payload, owner_email)
    return False


__all__ = ["matches_owner"]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/ingest/test_owner_filter.py -v
uv run ruff check src/twaky/owner_filter.py tests/ingest/
uv run mypy src/twaky/owner_filter.py
```
Expected: 10 passed, ruff clean, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/twaky/owner_filter.py tests/ingest/
git commit -m "feat(ingest): matches_owner dispatch for calendar/sabre/mail"
```

---

## Task 3: Wire owner filter into ingest

**Files:**
- Modify: `src/twaky/ingest.py`

**Interfaces:**
- Consumes: `owner_filter.matches_owner` (Task 2), `settings.twaky_owner_email` (Task 1).
- Produces: unchanged public API. Behavior change: events not matching owner are ack+dropped.

- [ ] **Step 1: Extend `tests/ingest/test_owner_filter.py` with a wiring test**

Append to `tests/ingest/test_owner_filter.py`:
```python
class TestIngestWiring:
    """Verify _consume drops non-owner events before insert."""

    @pytest.mark.asyncio
    async def test_non_owner_event_is_acked_and_dropped(self, monkeypatch):
        # Import here so the module-under-test picks up patched settings.
        from twaky import ingest

        # Fake message: calendar event NOT concerning the owner.
        acked = []
        inserted = []

        class FakeMessage:
            exchange = "calendar:event:created"
            routing_key = ""
            body = b'{"uid":"e1","organizer":{"email":"stranger@x"},"attendees":[]}'
            message_id = "verify-e1"

            async def ack(self):
                acked.append(True)

            async def reject(self, requeue):
                pass

        def _fake_insert(*a, **kw):
            inserted.append(True)
            return True

        monkeypatch.setattr(ingest, "_insert_event", _fake_insert)
        monkeypatch.setattr(ingest.settings, "twaky_owner_email", OWNER)

        # Consume ONE message.
        class FakeIter:
            def __init__(self, items): self.items = list(items)
            def __aiter__(self): return self
            async def __anext__(self):
                if not self.items: raise StopAsyncIteration
                return self.items.pop(0)
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None

        class FakeQueue:
            def iterator(self): return FakeIter([FakeMessage()])

        await ingest._consume(FakeQueue())

        assert acked == [True]
        assert inserted == []  # dropped, not inserted
```

Also add at the top of the file:
```python
import pytest
```
(imports may already exist; keep the module top clean).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/test_owner_filter.py::TestIngestWiring -v`
Expected: FAIL — the ingest currently inserts unconditionally.

- [ ] **Step 3: Modify `src/twaky/ingest.py`**

Edit the top imports to add:
```python
from twaky.owner_filter import matches_owner
```

Inside `_consume`, at the top of the `async for message in it:` body, right after the `try:` line, insert:
```python
                exch = message.exchange or ""
                if not matches_owner(exch, _decode_payload(message.body), settings.twaky_owner_email):
                    await message.ack()
                    log.debug("dropped: not for owner", exchange=exch)
                    continue
```

Then keep the existing logic below (which recomputes `exch` — that's fine, just don't duplicate the try/except structure). Concretely, replace the existing loop body with:
```python
        async for message in it:
            try:
                exch = message.exchange or ""
                payload = _decode_payload(message.body)
                if not matches_owner(exch, payload, settings.twaky_owner_email):
                    await message.ack()
                    log.debug("dropped: not for owner", exchange=exch)
                    continue
                rk = message.routing_key or ""
                mid = _message_key(exch, message.body, message.message_id)
                inserted = _insert_event(exch, rk, mid, payload)
                await message.ack()
                if inserted:
                    log.info("ingested", exchange=exch, mid=mid[:32])
                else:
                    log.debug("dedup", exchange=exch, mid=mid[:32])
            except psycopg.Error as e:
                log.error("db error, rejecting to DLQ", err=str(e))
                await message.reject(requeue=False)
            except Exception as e:
                log.exception("unexpected error, rejecting to DLQ", err=str(e))
                await message.reject(requeue=False)
```

- [ ] **Step 4: Run test + full suite**

```bash
uv run pytest tests/ingest/ tests/test_config.py -v
uv run ruff check src/twaky/ingest.py
uv run mypy src/twaky/ingest.py
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/twaky/ingest.py tests/ingest/test_owner_filter.py
git commit -m "feat(ingest): drop events not for TWAKY_OWNER_EMAIL before event_log"
```

---

## Task 4: Mail mappers + registry

**Files:**
- Create: `src/twaky/mappers/mail_message_received.py`
- Create: `src/twaky/mappers/mail_message_expunged.py`
- Create: `src/twaky/mappers/mail_message_flags_updated.py`
- Create: `src/twaky/mappers/mail_message_moved.py`
- Modify: `src/twaky/mappers/__init__.py`
- Create: `tests/mappers/test_mail_mappers.py`

**Interfaces:**
- Consumes: `twaky.mappers._cypher.cql_literal`.
- Produces: 4 `map_event(payload: dict) -> list[str]` functions; registry mapping 4 exchanges to them.

- [ ] **Step 1: Write the failing tests**

Create `tests/mappers/test_mail_mappers.py`:
```python
"""Cypher shape assertions for the 4 mail mappers."""

from __future__ import annotations

from twaky.mappers import get_mapper


class TestMailReceived:
    def _m(self):
        m = get_mapper("mail:message:received")
        assert m is not None
        return m

    def test_no_message_id_returns_empty(self):
        assert self._m()({"user": "a@x"}) == []

    def test_full_payload(self):
        stmts = self._m()(
            {
                "message_id": "m1",
                "user": "a@x",
                "mailbox_path": {"namespace": "#private", "user": "a@x", "name": "INBOX"},
                "timestamp": "2026-08-01T12:00:00Z",
            }
        )
        assert len(stmts) == 1
        s = stmts[0]
        assert 'MERGE (e:Email {message_id: "m1"})' in s
        assert 'e.user = "a@x"' in s
        assert 'e.deleted = false' in s
        assert "INBOX" in s
        assert '"2026-08-01T12:00:00Z"' in s


class TestMailExpunged:
    def _m(self):
        return get_mapper("mail:message:expunged")

    def test_marks_deleted(self):
        stmts = self._m()({"message_id": "m1", "user": "a@x"})
        assert len(stmts) == 1
        assert "SET e.deleted = true" in stmts[0]


class TestMailFlagsUpdated:
    def _m(self):
        return get_mapper("mail:message:flags:updated")

    def test_seen_true(self):
        stmts = self._m()({
            "message_id": "m1", "user": "a@x", "flags": ["\\Seen", "\\Answered"],
        })
        assert 'SET e.read = true' in stmts[0]

    def test_seen_false(self):
        stmts = self._m()({"message_id": "m1", "user": "a@x", "flags": ["\\Answered"]})
        assert 'SET e.read = false' in stmts[0]

    def test_missing_flags_treated_as_unread(self):
        stmts = self._m()({"message_id": "m1", "user": "a@x"})
        assert 'SET e.read = false' in stmts[0]


class TestMailMoved:
    def _m(self):
        return get_mapper("mail:message:moved")

    def test_updates_mailbox_path(self):
        stmts = self._m()({
            "message_id": "m1", "user": "a@x",
            "mailbox_path": {"namespace": "#private", "user": "a@x", "name": "Archive"},
        })
        assert 'SET e.mailbox_path' in stmts[0]
        assert "Archive" in stmts[0]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/mappers/test_mail_mappers.py -v
```
Expected: FAIL — `get_mapper` returns None for these exchanges.

- [ ] **Step 3: Create the 4 mapper modules**

Create `src/twaky/mappers/mail_message_received.py`:
```python
"""Map a `mail:message:received` event to an Email node.

Metadata only — no body fetch (JMAP fetch is deferred to sub-project 2).
"""

from __future__ import annotations

from twaky.mappers._cypher import cql_literal


def _flatten_path(mbp: object) -> str | None:
    if isinstance(mbp, dict):
        parts = [
            mbp.get("namespace") or "",
            mbp.get("user") or "",
            mbp.get("name") or "",
        ]
        return "/".join(str(p) for p in parts if p)
    if isinstance(mbp, str):
        return mbp
    return None


def map_event(payload: dict) -> list[str]:
    mid = payload.get("message_id")
    if not mid:
        return []
    settable = {
        "user": payload.get("user"),
        "mailbox_path": _flatten_path(payload.get("mailbox_path")),
        "received_at": payload.get("timestamp"),
        "deleted": False,
    }
    set_frag = ", ".join(
        f"e.{k} = {cql_literal(v)}" for k, v in settable.items() if v is not None
    )
    stmt = f"MERGE (e:Email {{message_id: {cql_literal(mid)}}})"
    if set_frag:
        stmt += f" SET {set_frag}"
    return [stmt]
```

Create `src/twaky/mappers/mail_message_expunged.py`:
```python
"""Map a `mail:message:expunged` event to a tombstone on the Email node."""

from __future__ import annotations

from twaky.mappers._cypher import cql_literal


def map_event(payload: dict) -> list[str]:
    mid = payload.get("message_id")
    if not mid:
        return []
    return [f"MERGE (e:Email {{message_id: {cql_literal(mid)}}}) SET e.deleted = true"]
```

Create `src/twaky/mappers/mail_message_flags_updated.py`:
```python
"""Map a `mail:message:flags:updated` event to Email.read (from `\\Seen`)."""

from __future__ import annotations

from twaky.mappers._cypher import cql_literal


def map_event(payload: dict) -> list[str]:
    mid = payload.get("message_id")
    if not mid:
        return []
    flags = payload.get("flags") or []
    read = "\\Seen" in flags if isinstance(flags, list) else False
    return [
        (
            f"MERGE (e:Email {{message_id: {cql_literal(mid)}}}) "
            f"SET e.read = {'true' if read else 'false'}"
        )
    ]
```

Create `src/twaky/mappers/mail_message_moved.py`:
```python
"""Map a `mail:message:moved` event to an update of Email.mailbox_path."""

from __future__ import annotations

from twaky.mappers._cypher import cql_literal
from twaky.mappers.mail_message_received import _flatten_path


def map_event(payload: dict) -> list[str]:
    mid = payload.get("message_id")
    if not mid:
        return []
    new_path = _flatten_path(payload.get("mailbox_path"))
    if new_path is None:
        return []
    return [
        (
            f"MERGE (e:Email {{message_id: {cql_literal(mid)}}}) "
            f"SET e.mailbox_path = {cql_literal(new_path)}"
        )
    ]
```

- [ ] **Step 4: Register the mappers**

Edit `src/twaky/mappers/__init__.py`. Add to imports:
```python
from twaky.mappers import (
    calendar_event_created,
    calendar_event_deleted,
    calendar_event_reply,
    mail_message_expunged,
    mail_message_flags_updated,
    mail_message_moved,
    mail_message_received,
    sabre_contact_created,
    sabre_contact_deleted,
)
```

Add to `_REGISTRY`:
```python
    "mail:message:received": mail_message_received.map_event,
    "mail:message:expunged": mail_message_expunged.map_event,
    "mail:message:flags:updated": mail_message_flags_updated.map_event,
    "mail:message:moved": mail_message_moved.map_event,
```

- [ ] **Step 5: Run test + lint**

```bash
uv run pytest tests/mappers/test_mail_mappers.py -v
uv run ruff check src/twaky/mappers/
uv run mypy src/twaky/mappers/
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/twaky/mappers/mail_message_*.py src/twaky/mappers/__init__.py tests/mappers/test_mail_mappers.py
git commit -m "feat(mappers): 4 mail:message:* mappers, metadata-only Email node"
```

---

## Task 5: Mission SQL schema (init script)

**Files:**
- Create: `sql/004_init_mission.sh`
- Create: `tests/missions/__init__.py` (empty)
- Create: `tests/missions/test_schema.py`

**Interfaces:**
- Consumes: existing `twaky` database.
- Produces: `mission` table + indexes.

- [ ] **Step 1: Write the failing test**

Create `tests/missions/__init__.py` (empty).

Create `tests/missions/test_schema.py`:
```python
"""Confirm the `mission` table + expected indexes exist on the running DB."""

from __future__ import annotations

import os

import psycopg
import pytest

from twaky.config import settings


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(), reason="twaky-pg not reachable (host must be inside twake-network)"
)


def test_mission_table_exists():
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='mission' ORDER BY ordinal_position"
        )
        cols = {r[0] for r in cur.fetchall()}
    expected = {
        "id", "owner_email", "declared_by", "declared_at", "intent_text",
        "plan", "state", "state_reason", "due_at", "artifacts",
        "langfuse_session_id", "created_at", "updated_at",
    }
    assert expected.issubset(cols), f"missing columns: {expected - cols}"


def test_indexes_exist():
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename='mission'"
        )
        idx = {r[0] for r in cur.fetchall()}
    assert "mission_live_idx" in idx
    assert "mission_owner_state_idx" in idx
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/missions/test_schema.py -v
```
Expected: FAIL — `mission` table doesn't exist (or SKIPPED if DB unreachable).

- [ ] **Step 3: Create the init script**

Create `sql/004_init_mission.sh`:
```bash
#!/bin/bash
# Provision the `mission` table (state coarse-grained) inside the twaky DB.
# Runs once on first-boot volume init. For existing volumes:
#   docker exec -i twaky-pg bash /docker-entrypoint-initdb.d/004_init_mission.sh
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-twaky}" <<-'EOSQL'
    CREATE TABLE IF NOT EXISTS public.mission (
        id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_email         TEXT NOT NULL,
        declared_by         TEXT NOT NULL,
        declared_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        intent_text         TEXT NOT NULL,
        plan                JSONB,
        state               TEXT NOT NULL DEFAULT 'declared'
                            CHECK (state IN ('declared','planning','running',
                                             'awaiting_user','done','failed','cancelled')),
        state_reason        TEXT,
        due_at              TIMESTAMPTZ,
        artifacts           JSONB NOT NULL DEFAULT '[]'::jsonb,
        langfuse_session_id TEXT,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS mission_live_idx
        ON public.mission (state)
        WHERE state IN ('declared','planning','running','awaiting_user');
    CREATE INDEX IF NOT EXISTS mission_owner_state_idx
        ON public.mission (owner_email, state);
    -- pgcrypto for gen_random_uuid() — usually preinstalled but be safe
    CREATE EXTENSION IF NOT EXISTS pgcrypto;
EOSQL
```

- [ ] **Step 4: Apply the script to the running DB**

```bash
chmod +x sql/004_init_mission.sh
docker exec -i twaky-pg bash /docker-entrypoint-initdb.d/004_init_mission.sh
```

Verify manually:
```bash
docker exec twaky-pg psql -U twaky -d twaky -c "\d mission"
```
Expected: table listed with all 13 columns + 2 indexes.

- [ ] **Step 5: Run the test suite**

```bash
uv run pytest tests/missions/test_schema.py -v
```
Expected: PASS (or SKIPPED if the runner isn't on twake-network).

- [ ] **Step 6: Commit**

```bash
git add sql/004_init_mission.sh tests/missions/
git commit -m "feat(sql): 004_init_mission.sh — mission table + indexes"
```

---

## Task 6: Mission Pydantic model

**Files:**
- Create: `src/twaky/missions/__init__.py`
- Create: `src/twaky/missions/models.py`
- Create: `tests/missions/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `MissionState` (StrEnum), `PlanStep` (Pydantic), `Mission` (Pydantic).

- [ ] **Step 1: Write the failing test**

Create `tests/missions/test_models.py`:
```python
"""Pydantic Mission model + PlanStep + state enum."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from twaky.missions.models import Mission, MissionState, PlanStep


class TestMissionState:
    def test_all_states(self):
        assert set(MissionState) == {
            MissionState.DECLARED, MissionState.PLANNING, MissionState.RUNNING,
            MissionState.AWAITING_USER, MissionState.DONE, MissionState.FAILED,
            MissionState.CANCELLED,
        }

    def test_terminal_helper(self):
        assert MissionState.DONE.is_terminal
        assert MissionState.FAILED.is_terminal
        assert MissionState.CANCELLED.is_terminal
        assert not MissionState.RUNNING.is_terminal


class TestPlanStep:
    def test_default_status_pending(self):
        s = PlanStep(agent="chronos", tool="list_events", args={})
        assert s.status == "pending"

    def test_bad_status_rejected(self):
        with pytest.raises(ValidationError):
            PlanStep(agent="x", tool="y", args={}, status="lol")  # type: ignore[arg-type]


class TestMission:
    def test_minimal_construction(self):
        m = Mission(
            id=uuid4(),
            owner_email="a@x",
            declared_by="a@x",
            declared_at=datetime.now(UTC),
            intent_text="do stuff",
            state=MissionState.DECLARED,
            artifacts=[],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        assert m.state == MissionState.DECLARED
        assert m.plan is None

    def test_plan_typed(self):
        m = Mission(
            id=uuid4(), owner_email="a@x", declared_by="a@x",
            declared_at=datetime.now(UTC), intent_text="do stuff",
            state=MissionState.RUNNING,
            plan=[PlanStep(agent="chronos", tool="list_events", args={"date": "2026-08-01"})],
            artifacts=[],
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        )
        assert m.plan and m.plan[0].agent == "chronos"

    def test_roundtrip_via_json(self):
        m1 = Mission(
            id=UUID("00000000-0000-0000-0000-000000000001"),
            owner_email="a@x", declared_by="a@x",
            declared_at=datetime(2026, 8, 1, tzinfo=UTC),
            intent_text="do stuff", state=MissionState.PLANNING,
            plan=[PlanStep(agent="atlas", tool="plan", args={})],
            artifacts=[], created_at=datetime(2026, 8, 1, tzinfo=UTC),
            updated_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        m2 = Mission.model_validate_json(m1.model_dump_json())
        assert m1 == m2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/missions/test_models.py -v
```
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Create the models**

Create `src/twaky/missions/__init__.py`:
```python
"""Mission domain: state, transitions, persistence, execution seam."""

from twaky.missions.models import Mission, MissionState, PlanStep

__all__ = ["Mission", "MissionState", "PlanStep"]
```

Create `src/twaky/missions/models.py`:
```python
"""Pydantic models for the Mission domain.

Persistence uses raw psycopg (see repository.py). These models are the
single source of truth for serialization (API, Langfuse, tests).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MissionState(StrEnum):
    DECLARED = "declared"
    PLANNING = "planning"
    RUNNING = "running"
    AWAITING_USER = "awaiting_user"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {MissionState.DONE, MissionState.FAILED, MissionState.CANCELLED}


class PlanStep(BaseModel):
    model_config = ConfigDict(frozen=False, extra="forbid")

    agent: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "in_progress", "done", "skipped"] = "pending"


class Mission(BaseModel):
    model_config = ConfigDict(frozen=False, extra="forbid")

    id: UUID
    owner_email: str
    declared_by: str
    declared_at: datetime
    intent_text: str
    plan: list[PlanStep] | None = None
    state: MissionState = MissionState.DECLARED
    state_reason: str | None = None
    due_at: datetime | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    langfuse_session_id: str | None = None
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 4: Run + lint**

```bash
uv run pytest tests/missions/test_models.py -v
uv run ruff check src/twaky/missions/
uv run mypy src/twaky/missions/
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/twaky/missions/__init__.py src/twaky/missions/models.py tests/missions/test_models.py
git commit -m "feat(missions): Pydantic Mission + PlanStep + MissionState enum"
```

---

## Task 7: State-machine guards (pure)

**Files:**
- Create: `src/twaky/missions/guards.py`
- Create: `tests/missions/test_guards.py`

**Interfaces:**
- Consumes: `MissionState` (Task 6).
- Produces: `check_transition(from_state: MissionState, to_state: MissionState) -> None` (raises `InvalidTransition` on illegal move); `InvalidTransition` exception class.

- [ ] **Step 1: Write the failing test**

Create `tests/missions/test_guards.py`:
```python
"""Pure state-machine tests — no DB, no LangGraph, just the transition table."""

from __future__ import annotations

import pytest

from twaky.missions.guards import InvalidTransition, check_transition
from twaky.missions.models import MissionState as S


class TestLegalTransitions:
    def test_declared_to_planning(self):
        check_transition(S.DECLARED, S.PLANNING)

    def test_planning_to_running(self):
        check_transition(S.PLANNING, S.RUNNING)

    def test_running_to_awaiting_user(self):
        check_transition(S.RUNNING, S.AWAITING_USER)

    def test_awaiting_user_to_running(self):
        check_transition(S.AWAITING_USER, S.RUNNING)

    def test_running_to_done(self):
        check_transition(S.RUNNING, S.DONE)

    def test_running_to_failed(self):
        check_transition(S.RUNNING, S.FAILED)

    def test_all_non_terminal_can_cancel(self):
        for s in (S.DECLARED, S.PLANNING, S.RUNNING, S.AWAITING_USER):
            check_transition(s, S.CANCELLED)


class TestIllegalTransitions:
    def test_declared_to_running_forbidden(self):
        with pytest.raises(InvalidTransition):
            check_transition(S.DECLARED, S.RUNNING)

    def test_running_to_planning_forbidden(self):
        with pytest.raises(InvalidTransition):
            check_transition(S.RUNNING, S.PLANNING)

    def test_terminal_states_have_no_exit(self):
        for start in (S.DONE, S.FAILED, S.CANCELLED):
            for end in S:
                if start == end:
                    continue
                with pytest.raises(InvalidTransition):
                    check_transition(start, end)

    def test_error_message_includes_states(self):
        with pytest.raises(InvalidTransition) as ei:
            check_transition(S.DONE, S.RUNNING)
        assert "done" in str(ei.value)
        assert "running" in str(ei.value)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/missions/test_guards.py -v
```
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Create the guards**

Create `src/twaky/missions/guards.py`:
```python
"""Pure state-machine for Mission transitions.

No DB, no I/O — just a static table + a check. Kept separate from
engine.py so it can be reused by anything that needs to reason about
transitions statically (validation, UI hints, docs).
"""

from __future__ import annotations

from twaky.missions.models import MissionState as S

_ALLOWED: dict[S, frozenset[S]] = {
    S.DECLARED: frozenset({S.PLANNING, S.CANCELLED}),
    S.PLANNING: frozenset({S.RUNNING, S.CANCELLED}),
    S.RUNNING: frozenset({S.AWAITING_USER, S.DONE, S.FAILED, S.CANCELLED}),
    S.AWAITING_USER: frozenset({S.RUNNING, S.CANCELLED, S.FAILED}),
    S.DONE: frozenset(),
    S.FAILED: frozenset(),
    S.CANCELLED: frozenset(),
}


class InvalidTransition(Exception):
    """Raised when a caller tries an illegal Mission state transition."""


def check_transition(from_state: S, to_state: S) -> None:
    allowed = _ALLOWED.get(from_state, frozenset())
    if to_state not in allowed:
        raise InvalidTransition(
            f"illegal Mission transition: {from_state.value} → {to_state.value} "
            f"(allowed from {from_state.value}: "
            f"{sorted(s.value for s in allowed) or '∅'})"
        )


__all__ = ["InvalidTransition", "check_transition"]
```

- [ ] **Step 4: Run + lint**

```bash
uv run pytest tests/missions/test_guards.py -v
uv run ruff check src/twaky/missions/guards.py tests/missions/test_guards.py
uv run mypy src/twaky/missions/guards.py
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/twaky/missions/guards.py tests/missions/test_guards.py
git commit -m "feat(missions): pure state-machine guards + InvalidTransition"
```

---

## Task 8: Mission repository (psycopg CRUD)

**Files:**
- Create: `src/twaky/missions/repository.py`
- Create: `tests/missions/test_repository.py`

**Interfaces:**
- Consumes: `twaky.db.get_pool` (existing), `Mission`, `MissionState`, `PlanStep` (Task 6).
- Produces:
  - `insert(mission: Mission) -> None`
  - `get(mission_id: UUID) -> Mission | None`
  - `update_state(mission_id: UUID, new_state: MissionState, reason: str | None = None, plan: list[PlanStep] | None = None, artifacts: list[dict] | None = None) -> None` (single row update, bumps `updated_at`)
  - `list_live(owner_email: str) -> list[Mission]` (rows in {declared, planning, running, awaiting_user})
  - `select_for_update(cur, mission_id: UUID) -> Mission` (helper used inside a caller's transaction; raises `MissionNotFound` if row absent)
  - `MissionNotFound` exception

- [ ] **Step 1: Write the failing test**

Create `tests/missions/test_repository.py`:
```python
"""Repository integration tests (self-skips if twaky-pg unreachable)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest

from twaky.config import settings
from twaky.missions import repository as repo
from twaky.missions.models import Mission, MissionState, PlanStep


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="twaky-pg not reachable")


def _make(owner: str = "alice@example.com", intent: str = "test mission") -> Mission:
    now = datetime.now(UTC)
    return Mission(
        id=uuid4(), owner_email=owner, declared_by=owner,
        declared_at=now, intent_text=intent, state=MissionState.DECLARED,
        artifacts=[], created_at=now, updated_at=now,
    )


def test_insert_and_get_roundtrip():
    m = _make()
    repo.insert(m)
    got = repo.get(m.id)
    assert got is not None
    assert got.id == m.id
    assert got.intent_text == m.intent_text
    assert got.state == MissionState.DECLARED
    _cleanup(m.id)


def test_update_state_bumps_updated_at():
    m = _make()
    repo.insert(m)
    got1 = repo.get(m.id)
    repo.update_state(m.id, MissionState.PLANNING, reason="atlas_took_over")
    got2 = repo.get(m.id)
    assert got2.state == MissionState.PLANNING
    assert got2.state_reason == "atlas_took_over"
    assert got2.updated_at > got1.updated_at
    _cleanup(m.id)


def test_update_state_with_plan():
    m = _make()
    repo.insert(m)
    plan = [PlanStep(agent="chronos", tool="list_events", args={"date": "2026-08-01"})]
    repo.update_state(m.id, MissionState.RUNNING, plan=plan)
    got = repo.get(m.id)
    assert got.plan == plan
    _cleanup(m.id)


def test_list_live_filters_by_state_and_owner():
    a = _make(owner="alice@x", intent="a")
    b = _make(owner="alice@x", intent="b")
    c = _make(owner="bob@x", intent="c")
    for m in (a, b, c):
        repo.insert(m)
    repo.update_state(b.id, MissionState.DONE, reason="ok")

    live_alice = repo.list_live("alice@x")
    ids = {m.id for m in live_alice}
    assert a.id in ids
    assert b.id not in ids  # terminal
    assert c.id not in ids  # different owner

    for m in (a, b, c):
        _cleanup(m.id)


def test_get_missing_returns_none():
    assert repo.get(uuid4()) is None


def _cleanup(mid):
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mission WHERE id = %s", (mid,))
        conn.commit()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/missions/test_repository.py -v
```
Expected: FAIL — `MissionNotFound` module doesn't exist / imports fail (or SKIPPED without DB).

- [ ] **Step 3: Create the repository**

Create `src/twaky/missions/repository.py`:
```python
"""psycopg3 CRUD for the `mission` table.

Raw SQL (no ORM) to stay consistent with src/twaky/db.py. Callers that
need atomicity across state transitions use select_for_update inside
their own connection/transaction.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row

from twaky.db import get_pool
from twaky.missions.models import Mission, MissionState, PlanStep


class MissionNotFound(Exception):
    pass


def _row_to_mission(row: dict[str, Any]) -> Mission:
    plan = row.get("plan")
    if plan is not None:
        plan = [PlanStep(**s) for s in plan]
    return Mission(
        id=row["id"],
        owner_email=row["owner_email"],
        declared_by=row["declared_by"],
        declared_at=row["declared_at"],
        intent_text=row["intent_text"],
        plan=plan,
        state=MissionState(row["state"]),
        state_reason=row["state_reason"],
        due_at=row["due_at"],
        artifacts=row["artifacts"] or [],
        langfuse_session_id=row["langfuse_session_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def insert(m: Mission) -> None:
    """Insert a fresh mission. Fails if id already exists."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO mission (
                id, owner_email, declared_by, declared_at, intent_text,
                plan, state, state_reason, due_at, artifacts,
                langfuse_session_id, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, %s, %s, %s)
            """,
            (
                m.id, m.owner_email, m.declared_by, m.declared_at, m.intent_text,
                json.dumps([s.model_dump() for s in m.plan]) if m.plan else None,
                m.state.value, m.state_reason, m.due_at,
                json.dumps(m.artifacts),
                m.langfuse_session_id, m.created_at, m.updated_at,
            ),
        )
        conn.commit()


def get(mission_id: UUID) -> Mission | None:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM mission WHERE id = %s", (mission_id,))
        row = cur.fetchone()
    return _row_to_mission(row) if row else None


def update_state(
    mission_id: UUID,
    new_state: MissionState,
    reason: str | None = None,
    plan: list[PlanStep] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
) -> None:
    """Update state + optional plan/artifacts, bump updated_at. One row, one txn."""
    sets = ["state = %s", "state_reason = %s", "updated_at = %s"]
    params: list[Any] = [new_state.value, reason, datetime.now(tz=_utc())]
    if plan is not None:
        sets.append("plan = %s::jsonb")
        params.append(json.dumps([s.model_dump() for s in plan]))
    if artifacts is not None:
        sets.append("artifacts = %s::jsonb")
        params.append(json.dumps(artifacts))
    params.append(mission_id)
    sql = f"UPDATE mission SET {', '.join(sets)} WHERE id = %s"
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        if cur.rowcount == 0:
            raise MissionNotFound(f"mission {mission_id} not found")
        conn.commit()


def list_live(owner_email: str) -> list[Mission]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM mission WHERE owner_email = %s AND state IN "
            "('declared','planning','running','awaiting_user') "
            "ORDER BY declared_at DESC",
            (owner_email,),
        )
        rows = cur.fetchall()
    return [_row_to_mission(r) for r in rows]


def select_for_update(cur, mission_id: UUID) -> Mission:
    """SELECT ... FOR UPDATE inside a caller's transaction. Locks the row."""
    cur.row_factory = dict_row
    cur.execute("SELECT * FROM mission WHERE id = %s FOR UPDATE", (mission_id,))
    row = cur.fetchone()
    if row is None:
        raise MissionNotFound(f"mission {mission_id} not found")
    return _row_to_mission(row)


def _utc():
    from datetime import UTC
    return UTC


__all__ = [
    "MissionNotFound", "get", "insert", "list_live", "select_for_update", "update_state",
]
```

- [ ] **Step 4: Run + lint**

```bash
uv run pytest tests/missions/test_repository.py -v
uv run ruff check src/twaky/missions/repository.py tests/missions/test_repository.py
uv run mypy src/twaky/missions/repository.py
```
Expected: PASS (or SKIPPED if runner not on twake-network).

- [ ] **Step 5: Commit**

```bash
git add src/twaky/missions/repository.py tests/missions/test_repository.py
git commit -m "feat(missions): psycopg repository (insert/get/update_state/list_live)"
```

---

## Task 9: LangGraph checkpointer wiring

**Files:**
- Modify: `pyproject.toml` (add `langgraph-checkpoint-postgres>=2.0`)
- Modify: `src/twaky/db.py` (add `get_langgraph_dsn()`)
- Create: `src/twaky/missions/checkpointer.py`
- Create: `sql/005_init_checkpointer.sh`
- Create: `tests/missions/test_checkpointer.py`

**Interfaces:**
- Consumes: `settings.pg_dsn` (existing).
- Produces:
  - `get_checkpointer() -> PostgresSaver` (module-level singleton)
  - `setup_checkpointer_tables() -> None` (idempotent; call once at boot)

- [ ] **Step 1: Add the dep**

```bash
uv add 'langgraph-checkpoint-postgres>=2.0'
```

Verify it doesn't pull anything forbidden:
```bash
uv tree --depth 1 | grep -iE 'langgraph-api|langgraph-cli|neo4j' || echo 'clean'
```
Expected: `clean`.

- [ ] **Step 2: Write the failing test**

Create `tests/missions/test_checkpointer.py`:
```python
"""LangGraph PostgresSaver — put/get/delete roundtrip on the twaky DB."""

from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest

from twaky.config import settings


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="twaky-pg not reachable")


def test_setup_creates_checkpoint_tables():
    from twaky.missions.checkpointer import setup_checkpointer_tables
    setup_checkpointer_tables()
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name LIKE 'checkpoint%'"
        )
        tables = {r[0] for r in cur.fetchall()}
    # PostgresSaver 2.x creates at minimum: checkpoints, checkpoint_writes, checkpoint_blobs
    assert "checkpoints" in tables
    assert "checkpoint_writes" in tables


def test_put_get_roundtrip():
    from twaky.missions.checkpointer import get_checkpointer
    saver = get_checkpointer()
    thread_id = str(uuid4())
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    checkpoint = {"v": 4, "id": thread_id, "channel_values": {"n": 42}, "channel_versions": {"n": 1}, "versions_seen": {}}
    metadata = {"source": "test", "step": 0, "writes": {}, "parents": {}}
    saved_cfg = saver.put(config, checkpoint, metadata, {})
    got = saver.get_tuple(saved_cfg)
    assert got is not None
    assert got.checkpoint["channel_values"] == {"n": 42}
```

- [ ] **Step 3: Run test to verify it fails**

```bash
uv run pytest tests/missions/test_checkpointer.py -v
```
Expected: FAIL — `twaky.missions.checkpointer` doesn't exist.

- [ ] **Step 4: Add DSN helper to `src/twaky/db.py`**

Append to `src/twaky/db.py`:
```python
def get_langgraph_dsn() -> str:
    """DSN used by the langgraph PostgresSaver. Same DB as the twaky graph."""
    return settings.pg_dsn
```

- [ ] **Step 5: Create the checkpointer factory**

Create `src/twaky/missions/checkpointer.py`:
```python
"""Thin factory + setup for the langgraph PostgresSaver.

The saver holds the fine-grained per-mission execution state, keyed on
thread_id = str(mission.id). It shares the twaky Postgres instance and
lives in its own tables (checkpoints, checkpoint_writes, checkpoint_blobs)
created by setup_checkpointer_tables() at boot.
"""

from __future__ import annotations

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg_pool import ConnectionPool

from twaky.db import get_langgraph_dsn

_pool: ConnectionPool | None = None
_saver: PostgresSaver | None = None


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=get_langgraph_dsn(),
            min_size=1, max_size=4,
            kwargs={"autocommit": True, "prepare_threshold": 0},
            open=True,
        )
    return _pool


def get_checkpointer() -> PostgresSaver:
    """Return the process-wide PostgresSaver instance."""
    global _saver
    if _saver is None:
        _saver = PostgresSaver(_get_pool())  # type: ignore[arg-type]
    return _saver


def setup_checkpointer_tables() -> None:
    """Create the checkpoint_* tables if missing. Idempotent. Call once at boot."""
    get_checkpointer().setup()


__all__ = ["get_checkpointer", "setup_checkpointer_tables"]
```

- [ ] **Step 6: Create the init script** (for fresh-volume operators)

Create `sql/005_init_checkpointer.sh`:
```bash
#!/bin/bash
# Placeholder — the checkpointer tables are created at runtime by
# setup_checkpointer_tables() (called at Atlas boot). This script exists
# so the sql/ layout stays sequential.
set -euo pipefail
echo "langgraph checkpointer tables created lazily by setup_checkpointer_tables()"
```

Make it executable:
```bash
chmod +x sql/005_init_checkpointer.sh
```

- [ ] **Step 7: Run test + lint**

Run setup manually first (integration test needs the tables):
```bash
uv run python -c "from twaky.missions.checkpointer import setup_checkpointer_tables; setup_checkpointer_tables()"
```

Then the tests:
```bash
uv run pytest tests/missions/test_checkpointer.py -v
uv run ruff check src/twaky/missions/checkpointer.py src/twaky/db.py tests/missions/test_checkpointer.py
uv run mypy src/twaky/missions/checkpointer.py src/twaky/db.py
```
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock src/twaky/missions/checkpointer.py src/twaky/db.py \
        sql/005_init_checkpointer.sh tests/missions/test_checkpointer.py
git commit -m "feat(missions): langgraph PostgresSaver factory + setup helper"
```

---

## Task 10: Mission engine transitions

**Files:**
- Create: `src/twaky/missions/engine.py`
- Create: `tests/missions/test_engine.py`

**Interfaces:**
- Consumes: `repository` (Task 8), `guards.check_transition` + `InvalidTransition` (Task 7), `Mission` + `MissionState` + `PlanStep` (Task 6).
- Produces (public function signatures — later tasks depend on these names):
  - `declare(intent_text: str, owner_email: str, declared_by: str, due_at: datetime | None = None) -> Mission`
  - `start_planning(mission_id: UUID) -> None`
  - `commit_plan(mission_id: UUID, plan: list[PlanStep]) -> None`
  - `request_user_input(mission_id: UUID, reason: str, artifact: dict) -> None`
  - `resume(mission_id: UUID, user_response: dict) -> None`
  - `finish(mission_id: UUID, outcome: Literal["done", "failed"], artifacts: list[dict], reason: str = "") -> None`
  - `cancel(mission_id: UUID, reason: str) -> None`

Each function: SELECT FOR UPDATE + check_transition + update_state (in a single transaction).

- [ ] **Step 1: Write the failing test**

Create `tests/missions/test_engine.py`:
```python
"""Engine transition integration tests — one legal + one illegal path each."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest

from twaky.config import settings
from twaky.missions import engine, repository
from twaky.missions.guards import InvalidTransition
from twaky.missions.models import Mission, MissionState, PlanStep


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="twaky-pg not reachable")


def _cleanup(mid):
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mission WHERE id = %s", (mid,))
        conn.commit()


def test_declare_creates_row():
    m = engine.declare(intent_text="do X", owner_email="a@x", declared_by="a@x")
    got = repository.get(m.id)
    assert got is not None
    assert got.state == MissionState.DECLARED
    _cleanup(m.id)


def test_full_happy_path():
    m = engine.declare(intent_text="do X", owner_email="a@x", declared_by="a@x")
    engine.start_planning(m.id)
    assert repository.get(m.id).state == MissionState.PLANNING

    plan = [PlanStep(agent="chronos", tool="list_events", args={})]
    engine.commit_plan(m.id, plan)
    got = repository.get(m.id)
    assert got.state == MissionState.RUNNING
    assert got.plan == plan

    engine.request_user_input(m.id, reason="approve draft", artifact={"draft": "hi"})
    assert repository.get(m.id).state == MissionState.AWAITING_USER

    engine.resume(m.id, user_response={"ok": True})
    assert repository.get(m.id).state == MissionState.RUNNING

    engine.finish(m.id, outcome="done", artifacts=[{"final": "ok"}])
    final = repository.get(m.id)
    assert final.state == MissionState.DONE
    assert final.artifacts == [{"draft": "hi"}, {"final": "ok"}]
    _cleanup(m.id)


def test_illegal_transition_rejected():
    m = engine.declare(intent_text="X", owner_email="a@x", declared_by="a@x")
    with pytest.raises(InvalidTransition):
        engine.commit_plan(m.id, [])  # DECLARED → RUNNING skipping PLANNING
    assert repository.get(m.id).state == MissionState.DECLARED  # unchanged
    _cleanup(m.id)


def test_cancel_from_any_non_terminal():
    m = engine.declare(intent_text="X", owner_email="a@x", declared_by="a@x")
    engine.start_planning(m.id)
    engine.cancel(m.id, reason="user_aborted")
    got = repository.get(m.id)
    assert got.state == MissionState.CANCELLED
    assert got.state_reason == "user_aborted"
    _cleanup(m.id)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/missions/test_engine.py -v
```
Expected: FAIL — engine doesn't exist.

- [ ] **Step 3: Create the engine**

Create `src/twaky/missions/engine.py`:
```python
"""Mission state-transition engine.

Every mutation of the `mission` table goes through this module. Callers
outside this file MUST NOT write to the row directly. The engine:

1. Opens a transaction and locks the row with SELECT ... FOR UPDATE.
2. Validates the transition via guards.check_transition.
3. Applies the update (state, state_reason, plan, artifacts, updated_at).
4. Commits.

Langfuse trace emission is added in a later task (test-driven; keep this
task focused on the state machine + persistence).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from twaky.db import get_pool
from twaky.missions import repository
from twaky.missions.guards import check_transition
from twaky.missions.models import Mission, MissionState, PlanStep


def declare(
    intent_text: str,
    owner_email: str,
    declared_by: str,
    due_at: datetime | None = None,
) -> Mission:
    """Create a fresh Mission in state=declared and persist it."""
    now = datetime.now(UTC)
    m = Mission(
        id=uuid4(),
        owner_email=owner_email,
        declared_by=declared_by,
        declared_at=now,
        intent_text=intent_text,
        state=MissionState.DECLARED,
        due_at=due_at,
        artifacts=[],
        created_at=now,
        updated_at=now,
    )
    repository.insert(m)
    return m


def _transition(
    mission_id: UUID,
    to_state: MissionState,
    reason: str | None = None,
    plan: list[PlanStep] | None = None,
    append_artifact: dict[str, Any] | None = None,
    replace_artifacts: list[dict[str, Any]] | None = None,
) -> None:
    """Common transition path — lock, check, update, commit."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        current = repository.select_for_update(cur, mission_id)
        check_transition(current.state, to_state)
        artifacts = replace_artifacts
        if append_artifact is not None:
            artifacts = list(current.artifacts) + [append_artifact]
        # Direct SQL here (not repository.update_state) to stay in the same txn.
        sets = ["state = %s", "state_reason = %s", "updated_at = %s"]
        params: list[Any] = [to_state.value, reason, datetime.now(UTC)]
        if plan is not None:
            sets.append("plan = %s::jsonb")
            import json as _json
            params.append(_json.dumps([s.model_dump() for s in plan]))
        if artifacts is not None:
            sets.append("artifacts = %s::jsonb")
            import json as _json
            params.append(_json.dumps(artifacts))
        params.append(mission_id)
        cur.execute(f"UPDATE mission SET {', '.join(sets)} WHERE id = %s", params)
        conn.commit()


def start_planning(mission_id: UUID) -> None:
    _transition(mission_id, MissionState.PLANNING)


def commit_plan(mission_id: UUID, plan: list[PlanStep]) -> None:
    _transition(mission_id, MissionState.RUNNING, plan=plan)


def request_user_input(mission_id: UUID, reason: str, artifact: dict[str, Any]) -> None:
    _transition(
        mission_id, MissionState.AWAITING_USER, reason=reason, append_artifact=artifact,
    )


def resume(mission_id: UUID, user_response: dict[str, Any]) -> None:
    _transition(
        mission_id, MissionState.RUNNING,
        reason="user_response_received",
        append_artifact={"kind": "user_response", "at": datetime.now(UTC).isoformat(),
                         "payload": user_response},
    )


def finish(
    mission_id: UUID,
    outcome: Literal["done", "failed"],
    artifacts: list[dict[str, Any]],
    reason: str = "",
) -> None:
    target = MissionState.DONE if outcome == "done" else MissionState.FAILED
    # Append the final artifacts to the existing list (don't clobber).
    with get_pool().connection() as conn, conn.cursor() as cur:
        current = repository.select_for_update(cur, mission_id)
        check_transition(current.state, target)
        merged = list(current.artifacts) + list(artifacts)
        import json as _json
        cur.execute(
            "UPDATE mission SET state = %s, state_reason = %s, artifacts = %s::jsonb, "
            "updated_at = %s WHERE id = %s",
            (target.value, reason or None, _json.dumps(merged),
             datetime.now(UTC), mission_id),
        )
        conn.commit()


def cancel(mission_id: UUID, reason: str) -> None:
    _transition(mission_id, MissionState.CANCELLED, reason=reason)


__all__ = [
    "cancel", "commit_plan", "declare", "finish", "request_user_input",
    "resume", "start_planning",
]
```

- [ ] **Step 4: Run test + lint**

```bash
uv run pytest tests/missions/test_engine.py -v
uv run ruff check src/twaky/missions/engine.py tests/missions/test_engine.py
uv run mypy src/twaky/missions/engine.py
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/twaky/missions/engine.py tests/missions/test_engine.py
git commit -m "feat(missions): state-machine engine (declare/plan/run/pause/finish/cancel)"
```

---

## Task 11: Langfuse instrumentation on transitions

**Files:**
- Modify: `src/twaky/missions/engine.py`
- Modify: `tests/missions/test_engine.py` (add trace-emission assertion)

**Interfaces:**
- Consumes: `twaky.observability.get_client()` (existing).
- Produces: no new function; adds a `_trace(name, mission_id, extra)` internal helper. Behavior change: every transition emits a Langfuse trace named `mission.<transition>` attached to `mission.langfuse_session_id`.

- [ ] **Step 1: Extend the test**

Append to `tests/missions/test_engine.py`:
```python
class TestLangfuseInstrumentation:
    def test_declare_emits_trace(self, monkeypatch):
        """When langfuse creds are set, engine.declare should call the client."""
        # Skip if not configured — we don't want CI to require creds.
        if not (settings.langfuse_public_key and settings.langfuse_secret_key):
            pytest.skip("langfuse not configured in this environment")

        seen: list[str] = []

        # Wrap the real Langfuse client to record start_as_current_span names.
        import twaky.observability as obs

        real_client = obs.get_client()
        if real_client is None:
            pytest.skip("langfuse client unavailable")

        orig_span = real_client.start_as_current_span

        def _spy(name, **kw):
            seen.append(name)
            return orig_span(name=name, **kw)

        monkeypatch.setattr(real_client, "start_as_current_span", _spy)

        m = engine.declare(intent_text="X", owner_email="a@x", declared_by="a@x")
        engine.start_planning(m.id)
        engine.cancel(m.id, reason="test_over")
        assert any(n.startswith("mission.declare") for n in seen)
        assert any(n.startswith("mission.start_planning") for n in seen)
        assert any(n.startswith("mission.cancel") for n in seen)
        _cleanup(m.id)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/missions/test_engine.py::TestLangfuseInstrumentation -v
```
Expected: FAIL (or SKIPPED if langfuse not configured) — no trace names captured.

- [ ] **Step 3: Add instrumentation to `src/twaky/missions/engine.py`**

Add at the top:
```python
from twaky import observability
```

Add a helper right below the imports:
```python
def _trace(name: str, mission_id: UUID, extra: dict[str, Any] | None = None):
    """Emit a `mission.<name>` trace attached to the mission's session_id.

    No-op if Langfuse is not configured — observability.get_client() returns
    None and this helper silently returns a nullcontext.
    """
    import contextlib
    lf = observability.get_client()
    if lf is None:
        return contextlib.nullcontext()
    m = repository.get(mission_id)
    session_id = (m.langfuse_session_id if m else None) or f"mission-{mission_id}"
    span = lf.start_as_current_span(name=f"mission.{name}")
    # Best-effort: set trace-level session_id (matches what agent.ask does).
    try:
        span.update_trace(session_id=session_id, user_id=(m.owner_email if m else ""))
    except Exception:  # noqa: BLE001
        pass
    if extra:
        try:
            span.update(input=extra)
        except Exception:  # noqa: BLE001
            pass
    return span
```

Wrap each public transition with the trace. For example, replace `declare`:
```python
def declare(
    intent_text: str,
    owner_email: str,
    declared_by: str,
    due_at: datetime | None = None,
) -> Mission:
    now = datetime.now(UTC)
    m = Mission(
        id=uuid4(),
        owner_email=owner_email,
        declared_by=declared_by,
        declared_at=now,
        intent_text=intent_text,
        state=MissionState.DECLARED,
        due_at=due_at,
        artifacts=[],
        langfuse_session_id=f"mission-{uuid4()}",  # stable session id from birth
        created_at=now, updated_at=now,
    )
    repository.insert(m)
    with _trace("declare", m.id, extra={"intent_text": intent_text}):
        pass
    return m
```

Wrap the six other transitions similarly — each opens a `_trace()` context around the SQL write. Example for `start_planning`:
```python
def start_planning(mission_id: UUID) -> None:
    with _trace("start_planning", mission_id):
        _transition(mission_id, MissionState.PLANNING)
```

Do the same pattern for `commit_plan`, `request_user_input`, `resume`, `finish`, `cancel`.

Also try to flush the client after each transition so short-lived CLI processes don't lose traces:
```python
def _flush():
    lf = observability.get_client()
    if lf is None: return
    try: lf.flush()
    except Exception: pass  # noqa: BLE001
```
Call `_flush()` at the end of each transition function (after `_transition(...)`).

- [ ] **Step 4: Run test + lint**

```bash
uv run pytest tests/missions/test_engine.py -v
uv run ruff check src/twaky/missions/engine.py
uv run mypy src/twaky/missions/engine.py
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/twaky/missions/engine.py tests/missions/test_engine.py
git commit -m "feat(missions): Langfuse trace per transition (session_id per mission)"
```

---

## Task 12: Restart recovery

**Files:**
- Create: `src/twaky/missions/recovery.py`
- Create: `tests/missions/test_recovery.py`

**Interfaces:**
- Consumes: `repository.list_live` (Task 8), `get_checkpointer` (Task 9), `engine.finish` (Task 10).
- Produces: `resume_missions_after_restart(owner_email: str) -> list[tuple[UUID, str]]` — returns list of `(mission_id, action)` where action ∈ `{"resumed", "failed_checkpoint_lost"}`.

- [ ] **Step 1: Write the failing test**

Create `tests/missions/test_recovery.py`:
```python
"""Restart-recovery reconciles live missions with LangGraph checkpoints."""

from __future__ import annotations

import os

import psycopg
import pytest

from twaky.config import settings
from twaky.missions import engine, recovery, repository
from twaky.missions.models import MissionState, PlanStep


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="twaky-pg not reachable")


def _cleanup(mid):
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mission WHERE id = %s", (mid,))
        conn.commit()


def test_mission_without_checkpoint_is_failed_at_recovery():
    """Simulate a crash right after commit_plan, before LangGraph wrote a checkpoint."""
    m = engine.declare(intent_text="ghost", owner_email="a@x", declared_by="a@x")
    engine.start_planning(m.id)
    engine.commit_plan(m.id, [PlanStep(agent="chronos", tool="list_events", args={})])
    # State is RUNNING but no LangGraph checkpoint was written for this thread_id.

    results = recovery.resume_missions_after_restart(owner_email="a@x")
    ids = {mid: action for (mid, action) in results}
    assert ids.get(m.id) == "failed_checkpoint_lost"

    final = repository.get(m.id)
    assert final.state == MissionState.FAILED
    assert "checkpoint_lost" in (final.state_reason or "")
    _cleanup(m.id)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/missions/test_recovery.py -v
```
Expected: FAIL — `recovery` module missing.

- [ ] **Step 3: Create the recovery module**

Create `src/twaky/missions/recovery.py`:
```python
"""Restart resilience for the Mission engine.

At Atlas boot, scans missions in a non-terminal, non-declared state and
reconciles them with the LangGraph checkpointer:

- If a checkpoint exists → the caller (Atlas) is expected to resume it.
- If no checkpoint exists → the mission is transitioned to `failed` with
  reason `checkpoint_lost_after_restart`. The user can re-declare.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

import structlog

from twaky.missions import engine, repository
from twaky.missions.checkpointer import get_checkpointer
from twaky.missions.models import MissionState

log = structlog.get_logger("twaky.missions.recovery")

Action = Literal["resumed", "failed_checkpoint_lost"]


def _has_checkpoint(mission_id: UUID) -> bool:
    saver = get_checkpointer()
    cfg = {"configurable": {"thread_id": str(mission_id), "checkpoint_ns": ""}}
    return saver.get_tuple(cfg) is not None


def resume_missions_after_restart(owner_email: str) -> list[tuple[UUID, Action]]:
    """Reconcile live missions with checkpointer. Returns per-mission action."""
    live = repository.list_live(owner_email)
    # `declared` state doesn't have a checkpoint yet — skip it.
    to_check = [m for m in live if m.state != MissionState.DECLARED]

    out: list[tuple[UUID, Action]] = []
    for m in to_check:
        if _has_checkpoint(m.id):
            log.info("resume_ready", mission_id=str(m.id), state=m.state.value)
            out.append((m.id, "resumed"))
        else:
            log.warning("checkpoint_lost", mission_id=str(m.id))
            engine.finish(
                m.id, outcome="failed", artifacts=[],
                reason="checkpoint_lost_after_restart",
            )
            out.append((m.id, "failed_checkpoint_lost"))
    return out


__all__ = ["Action", "resume_missions_after_restart"]
```

- [ ] **Step 4: Run test + lint**

```bash
uv run pytest tests/missions/test_recovery.py -v
uv run ruff check src/twaky/missions/recovery.py tests/missions/test_recovery.py
uv run mypy src/twaky/missions/recovery.py
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/twaky/missions/recovery.py tests/missions/test_recovery.py
git commit -m "feat(missions): recovery — auto-fail missions with lost checkpoints at boot"
```

---

## Task 13: P2P envelope (documented + Pydantic model)

**Files:**
- Create: `src/twaky/missions/envelope.py`
- Create: `tests/missions/test_envelope.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Intent` (StrEnum), `Envelope` (Pydantic BaseModel). No wire code.

- [ ] **Step 1: Write the failing test**

Create `tests/missions/test_envelope.py`:
```python
"""Pydantic validation for the future P2P envelope."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from twaky.missions.envelope import Envelope, Intent


class TestIntent:
    def test_all_initial_intents_present(self):
        assert Intent.ASK_AVAILABILITY in Intent
        assert Intent.PROPOSE_MEETING in Intent
        assert Intent.DELEGATE_TASK in Intent
        assert Intent.SHARE_INFO in Intent
        assert Intent.ACK in Intent


class TestEnvelope:
    def _base(self, **kw):
        now = datetime.now(UTC)
        return {
            "envelope_version": "1",
            "message_id": f"urn:uuid:{uuid4()}",
            "correlation_id": f"urn:uuid:{uuid4()}",
            "from_email": "alice@x",
            "to_email": "bob@x",
            "sent_at": now,
            "expires_at": now + timedelta(minutes=5),
            "intent": Intent.ACK,
            "payload": {"ok": True},
            **kw,
        }

    def test_minimal_ok(self):
        e = Envelope(**self._base())
        assert e.intent == Intent.ACK

    def test_expires_after_sent_at(self):
        now = datetime.now(UTC)
        with pytest.raises(ValidationError, match="expires_at"):
            Envelope(**self._base(sent_at=now, expires_at=now - timedelta(seconds=1)))

    def test_serialize_roundtrip(self):
        e1 = Envelope(**self._base())
        e2 = Envelope.model_validate_json(e1.model_dump_json())
        assert e1 == e2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/missions/test_envelope.py -v
```
Expected: FAIL — envelope module missing.

- [ ] **Step 3: Create the envelope module**

Create `src/twaky/missions/envelope.py`:
```python
"""P2P envelope for future federation (documented, not deployed yet).

Fixing the contract here lets sub-project 2+ code against a stable shape.
Signature scheme is deliberately deferred — see sub-project 4.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class Intent(StrEnum):
    ASK_AVAILABILITY = "ask_availability"
    PROPOSE_MEETING = "propose_meeting"
    DELEGATE_TASK = "delegate_task"
    SHARE_INFO = "share_info"
    ACK = "ack"


class Envelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    envelope_version: str = "1"
    message_id: str            # urn:uuid:<uuid4>
    correlation_id: str        # urn:uuid:<uuid4>
    from_email: str            # sender twaky owner
    to_email: str              # recipient twaky owner (used as routing key)
    sent_at: datetime
    expires_at: datetime
    intent: Intent
    payload: dict[str, Any]

    @model_validator(mode="after")
    def _check_time_ordering(self) -> Envelope:
        if self.expires_at <= self.sent_at:
            raise ValueError("expires_at must be strictly after sent_at")
        return self


__all__ = ["Envelope", "Intent"]
```

- [ ] **Step 4: Run test + lint**

```bash
uv run pytest tests/missions/test_envelope.py -v
uv run ruff check src/twaky/missions/envelope.py tests/missions/test_envelope.py
uv run mypy src/twaky/missions/envelope.py
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/twaky/missions/envelope.py tests/missions/test_envelope.py
git commit -m "feat(missions): P2P envelope Pydantic model (spec-only, no wire)"
```

---

## Task 14: Mail roundtrip integration test

**Files:**
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/test_mail_roundtrip.py`

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces: an integration test proving a mail event round-trips from RabbitMQ publish to an Email graph node.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/__init__.py` (empty).

Create `tests/integration/test_mail_roundtrip.py`:
```python
"""Publish a synthetic mail:message:received → verify Email node in graph.

Requires the live twaky stack (twaky-pg + rabbitmq + twaky-ingest + twaky-projector).
Test is skipped if any component is unreachable. Run inside twake-network via
`docker compose run --rm --no-deps twaky-agent pytest tests/integration/...`.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from uuid import uuid4

import aio_pika
import psycopg
import pytest

from twaky.config import settings


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="twaky-pg not reachable")


async def _publish_mail_received(mid: str, owner: str):
    conn = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with conn:
        ch = await conn.channel()
        exch = await ch.get_exchange("mail:message:received", ensure=True)
        body = {
            "message_id": mid,
            "user": owner,
            "mailbox_path": {"namespace": "#private", "user": owner, "name": "INBOX"},
            "timestamp": "2026-08-01T12:00:00Z",
        }
        await exch.publish(
            aio_pika.Message(body=json.dumps(body).encode(),
                             content_type="application/json",
                             message_id=f"test-{mid}"),
            routing_key="",
        )


def _read_email_node(mid: str):
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("LOAD 'age';")
        cur.execute('SET search_path = ag_catalog, "$user", public;')
        cur.execute(
            f"SELECT * FROM cypher('twake', $CQR$ MATCH (e:Email {{message_id: '{mid}'}}) "
            f"RETURN e.user AS user, e.deleted AS deleted, e.mailbox_path AS mp $CQR$) "
            f"AS (u ag_catalog.agtype, d ag_catalog.agtype, mp ag_catalog.agtype);"
        )
        rows = cur.fetchall()
    return rows


def test_mail_received_lands_in_graph():
    mid = f"pytest-mail-{uuid4().hex[:8]}"
    owner = settings.twaky_owner_email
    asyncio.run(_publish_mail_received(mid, owner))

    # Wait up to 15s for ingest + projector to catch up.
    for _ in range(15):
        rows = _read_email_node(mid)
        if rows:
            break
        time.sleep(1)
    assert rows, f"Email node {mid!r} did not appear in graph"
    user_val = str(rows[0][0]).strip('"')
    deleted_val = str(rows[0][1]).lower()
    assert user_val == owner
    assert deleted_val == "false"

    # Cleanup graph.
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("LOAD 'age';")
        cur.execute('SET search_path = ag_catalog, "$user", public;')
        cur.execute(
            f"SELECT * FROM cypher('twake', $CQR$ MATCH (e:Email {{message_id: '{mid}'}}) "
            f"DETACH DELETE e $CQR$) AS (v ag_catalog.agtype);"
        )
        cur.execute("DELETE FROM event_log WHERE message_id = %s", (f"test-{mid}",))
        conn.commit()
```

- [ ] **Step 2: Run test to verify it fails or skips**

```bash
uv run pytest tests/integration/test_mail_roundtrip.py -v
```
Expected: FAIL (mail exchange declaration should exist — it's fanout, mail-events-bridge creates it if the bridge is running) OR SKIPPED if runner isn't on twake-network.

If the exchange doesn't exist, that's expected on a fresh stack — declare it with a dummy publisher first via:
```bash
docker exec rabbitmq rabbitmqadmin declare exchange name=mail:message:received type=fanout durable=true
```
(only needed on a stack without the mail-events-bridge running).

- [ ] **Step 3: Rebuild + restart ingest/projector so they pick up the new mail bindings**

```bash
docker compose -f /home/mmaudet/deploy/kickstart-maudet-cloud/docker-compose.yaml build twaky-ingest twaky-projector
docker compose -f /home/mmaudet/deploy/kickstart-maudet-cloud/docker-compose.yaml up -d --force-recreate twaky-ingest twaky-projector
```

Verify bindings:
```bash
docker exec rabbitmq rabbitmqctl list_bindings -p / source_name destination_name 2>&1 | grep 'mail:'
```
Expected: 4 rows, all pointing at `agent.graph.ingest`.

- [ ] **Step 4: Re-run the integration test inside a container**

```bash
docker compose -f /home/mmaudet/deploy/kickstart-maudet-cloud/docker-compose.yaml run --rm --no-deps twaky-agent uv run pytest tests/integration/test_mail_roundtrip.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/
git commit -m "test(integration): mail:message:received round-trip → Email graph node"
```

---

## Task 15: E2E scenario script (Foundations)

**Files:**
- Create: `scripts/scenarios-foundations.sh`
- Modify: `Makefile` (add `scenarios-foundations` target)

**Interfaces:**
- Consumes: everything from Tasks 1–14.
- Produces: a bash script proving the full Foundations flow end-to-end on the live stack.

- [ ] **Step 1: Write the script**

Create `scripts/scenarios-foundations.sh`:
```bash
#!/usr/bin/env bash
# End-to-end verification of Twaky Foundations (sub-project 1).
#
# Requires the live stack (docker compose from deploy root) + TWAKY_OWNER_EMAIL
# set in twaky/.env.
#
# Verifies:
#   T1 · owner filter drops events not for the owner
#   T2 · mail:message:received lands in the graph as an Email node
#   T3 · mission lifecycle (declared → planning → running → awaiting_user → done)
#   T4 · crash-mid-flight recovery (checkpoint_lost → failed)

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
step() { echo -e "\n${BLUE}══ $* ══${NC}"; }
ok()   { echo -e "${GREEN}✔${NC} $*"; }
fail() { echo -e "${RED}✘${NC} $*"; exit 1; }

TWAKY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY="/home/mmaudet/deploy/kickstart-maudet-cloud/docker-compose.yaml"
RUN="docker compose -f ${DEPLOY} run --rm --no-deps twaky-agent"

# shellcheck disable=SC1091
source "${TWAKY_DIR}/.env"
OWNER="${TWAKY_OWNER_EMAIL}"

step "T1 · owner filter — publish 2 mails (owner + stranger), only owner survives"
MID_OWNER="scenario-$(date +%s)-owner"
MID_STRANGER="scenario-$(date +%s)-stranger"
$RUN python -c "
import asyncio, json, aio_pika
from twaky.config import settings

async def pub(mid, user):
    conn = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with conn:
        ch = await conn.channel()
        ex = await ch.get_exchange('mail:message:received', ensure=True)
        await ex.publish(aio_pika.Message(
            body=json.dumps({'message_id': mid, 'user': user,
                             'mailbox_path': {'namespace': '#private',
                                              'user': user, 'name': 'INBOX'},
                             'timestamp': '2026-08-01T12:00:00Z'}).encode(),
            message_id='scenario-' + mid,
        ), routing_key='')

asyncio.run(pub('${MID_OWNER}', '${OWNER}'))
asyncio.run(pub('${MID_STRANGER}', 'stranger@example.com'))
"
sleep 3
COUNT_OWNER=$(docker exec twaky-pg psql -tAU twaky -d twaky -c \
    "SELECT count(*) FROM event_log WHERE payload->>'message_id'='${MID_OWNER}';")
COUNT_STRANGER=$(docker exec twaky-pg psql -tAU twaky -d twaky -c \
    "SELECT count(*) FROM event_log WHERE payload->>'message_id'='${MID_STRANGER}';")
[[ "$COUNT_OWNER"    == "1" ]] || fail "owner mail count = $COUNT_OWNER, expected 1"
[[ "$COUNT_STRANGER" == "0" ]] || fail "stranger mail count = $COUNT_STRANGER, expected 0"
ok "T1 · owner=1, stranger=0 in event_log"

step "T2 · Email node in graph for the owner's mail"
GRAPH_COUNT=$(docker exec -i twaky-pg psql -tAU twaky -d twaky <<SQL | tail -1 | tr -d '"'
LOAD 'age';
SET search_path = ag_catalog, "\$user", public;
SELECT * FROM cypher('twake', \$CQR\$ MATCH (e:Email {message_id: "${MID_OWNER}"}) RETURN count(e) AS n \$CQR\$) AS (n agtype);
SQL
)
[[ "$GRAPH_COUNT" == "1" ]] || fail "graph count for Email{message_id=${MID_OWNER}} = $GRAPH_COUNT"
ok "T2 · Email node present"

step "T3 · mission lifecycle happy path"
MISSION_ID=$($RUN python -c "
from twaky.missions import engine
from twaky.missions.models import PlanStep
m = engine.declare(intent_text='scenario check', owner_email='${OWNER}', declared_by='${OWNER}')
engine.start_planning(m.id)
engine.commit_plan(m.id, [PlanStep(agent='chronos', tool='list_events', args={})])
engine.request_user_input(m.id, reason='approve', artifact={'draft': 'hi'})
engine.resume(m.id, user_response={'ok': True})
engine.finish(m.id, outcome='done', artifacts=[{'final': 'ok'}])
print(m.id)
" | tail -1)
STATE=$(docker exec twaky-pg psql -tAU twaky -d twaky -c \
    "SELECT state FROM mission WHERE id = '${MISSION_ID}';")
[[ "$STATE" == "done" ]] || fail "mission ${MISSION_ID} state = $STATE, expected done"
ok "T3 · mission ${MISSION_ID} traversed all 6 states"

step "T4 · crash recovery — mission stuck in running with no checkpoint"
STUCK_ID=$($RUN python -c "
from twaky.missions import engine
from twaky.missions.models import PlanStep
m = engine.declare(intent_text='stuck', owner_email='${OWNER}', declared_by='${OWNER}')
engine.start_planning(m.id)
engine.commit_plan(m.id, [PlanStep(agent='chronos', tool='list_events', args={})])
print(m.id)
" | tail -1)
RECOVERY_ACTION=$($RUN python -c "
from twaky.missions.recovery import resume_missions_after_restart
for mid, action in resume_missions_after_restart(owner_email='${OWNER}'):
    if str(mid) == '${STUCK_ID}':
        print(action)
        break
" | tail -1)
[[ "$RECOVERY_ACTION" == "failed_checkpoint_lost" ]] || fail "recovery for ${STUCK_ID} = $RECOVERY_ACTION"
STATE=$(docker exec twaky-pg psql -tAU twaky -d twaky -c "SELECT state FROM mission WHERE id = '${STUCK_ID}';")
[[ "$STATE" == "failed" ]] || fail "stuck mission state after recovery = $STATE"
ok "T4 · stuck mission ${STUCK_ID} auto-failed"

# cleanup
docker exec twaky-pg psql -U twaky -d twaky -c \
    "DELETE FROM mission WHERE id IN ('${MISSION_ID}', '${STUCK_ID}');" >/dev/null
docker exec twaky-pg psql -U twaky -d twaky -c \
    "DELETE FROM event_log WHERE message_id IN ('scenario-${MID_OWNER}');" >/dev/null
docker exec -i twaky-pg psql -tAU twaky -d twaky >/dev/null <<SQL
LOAD 'age';
SET search_path = ag_catalog, "\$user", public;
SELECT * FROM cypher('twake', \$CQR\$ MATCH (e:Email {message_id: "${MID_OWNER}"}) DETACH DELETE e \$CQR\$) AS (v agtype);
SQL

echo -e "\n${GREEN}══════ ALL FOUNDATIONS CHECKS PASSED ══════${NC}"
```

Make it executable:
```bash
chmod +x scripts/scenarios-foundations.sh
```

- [ ] **Step 2: Add Makefile target**

Edit `Makefile`. Add to `.PHONY` and add the target:
```makefile
scenarios-foundations: ## Run the Foundations end-to-end scenario
	bash scripts/scenarios-foundations.sh
```

- [ ] **Step 3: Run the scenario end-to-end**

```bash
make scenarios-foundations
```
Expected: 4 PASS + "ALL FOUNDATIONS CHECKS PASSED".

- [ ] **Step 4: Commit**

```bash
git add scripts/scenarios-foundations.sh Makefile
git commit -m "test(scenarios): end-to-end Foundations (owner filter + mail + missions + recovery)"
```

---

## Task 16: Final integration + docs sweep

**Files:**
- Modify: `README.md` (add Foundations section)

**Interfaces:**
- No new code.

- [ ] **Step 1: Update README.md**

Append after the existing "Graph schema" section:
```markdown
## Missions (Foundations)

A twaky instance is scoped to a single owner (`TWAKY_OWNER_EMAIL` in `.env`).
Every event that doesn't concern the owner is dropped at ingest — the
`event_log` and graph stay owner-only.

Missions are the unit of orchestration. A Mission is declared by natural
language, planned by Atlas (sub-project 2), and traverses:

    declared → planning → running ⇄ awaiting_user → done | failed | cancelled

State lives in the `mission` Postgres table; the fine-grained per-mission
execution state lives in the LangGraph checkpointer (`checkpoints` table,
same DB). At Atlas boot, `recovery.resume_missions_after_restart()`
reconciles: missions with no checkpoint are marked `failed` with reason
`checkpoint_lost_after_restart`.

Run the end-to-end scenario:

    make scenarios-foundations

Mail metadata is ingested from `mail:message:{received,expunged,flags:updated,moved}` —
body fetching (JMAP) is deferred to sub-project 2.

The `twaky:message:*` federation envelope is documented in
`src/twaky/missions/envelope.py` but not wired — sub-project 4.
```

- [ ] **Step 2: Verify full suite one last time**

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src/
```
Expected: all green.

- [ ] **Step 3: Commit + push**

```bash
git add README.md
git commit -m "docs: README section on Foundations (missions + owner scoping + mail)"
git push origin main
```

---

## Rollback

If any task lands buggy, `git revert` its commit. All schema changes are additive:

```bash
docker exec twaky-pg psql -U twaky -d twaky -c "DROP TABLE IF EXISTS mission CASCADE;"
docker exec twaky-pg psql -U twaky -d twaky -c "DROP TABLE IF EXISTS checkpoints CASCADE; DROP TABLE IF EXISTS checkpoint_writes CASCADE; DROP TABLE IF EXISTS checkpoint_blobs CASCADE;"
```

Owner filter revert:
```bash
git revert <sha of Task 3 commit>
docker compose -f /home/mmaudet/deploy/kickstart-maudet-cloud/docker-compose.yaml build twaky-ingest
docker compose -f /home/mmaudet/deploy/kickstart-maudet-cloud/docker-compose.yaml up -d --force-recreate twaky-ingest
```

---

## Self-review (author, before handoff)

- **Spec coverage:** every section of the spec is covered — Mission table (T5), Pydantic model (T6), guards (T7), engine + transitions (T10), Langfuse (T11), owner scoping (T1+T2+T3), mail ingest (T4), P2P envelope (T13), LangGraph seam (T9), restart recovery (T12), testing (T5–T14 unit + integration, T15 E2E), rollout notes (rollback section).
- **Placeholder scan:** no "TBD/TODO/fill in details" in step bodies. The one intentional TBD (signature scheme for P2P) is called out in the envelope's docstring and the spec sub-project 4 handoff.
- **Type consistency:** `PlanStep`, `Mission`, `MissionState`, `Envelope`, `Intent` are defined in T6/T13 and used with matching names in T7/T8/T10/T12. Engine function names in T10 match the interfaces block and match calls in T12/T15.
- **Ambiguity:** file paths and function signatures are exact throughout; tests contain concrete assertions.

---

## Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-01-twaky-foundations.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
