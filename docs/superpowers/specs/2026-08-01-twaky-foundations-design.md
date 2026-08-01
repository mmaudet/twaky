# Twaky Foundations — Design (Sub-project 1 of 5)

**Status:** draft, awaiting user review
**Date:** 2026-08-01
**Owner:** mmaudet
**Related:** first slice of the Control Tower vision (see mockup in session notes). Sub-projects 2–5 (Agents+Atlas, API+UI, Federation, Write-side) will each get their own design + plan cycle.

---

## 1. Purpose

Establish the invariants that every subsequent twaky sub-project will build on:

1. A **Mission** domain object with a clear state machine and DB backing.
2. Explicit **owner scoping** so a twaky instance ingests only its owner's data.
3. **Mail ingest** wired into the existing bus (metadata only for now).
4. A **P2P envelope** documented for future federation, without deploying it yet.
5. A clean **runtime seam** between the coarse-grained Mission state (Postgres) and the fine-grained execution state (LangGraph checkpointer).

Everything else in Foundations (agents, API, UI, federation, write-side integrations) is explicitly out of scope and deferred to later sub-projects.

## 2. Non-goals

- No agent implementation (Chronos/Plume/Iris are sub-project 2).
- No HTTP API, no frontend (sub-project 3).
- No actual P2P deployment, no signature scheme (sub-project 4).
- No write-side integrations to CalDAV, JMAP, etc. (sub-project 5).
- No RAG, no vector store (explicitly out per user decision).
- No JMAP mail body fetching (only metadata from the event bus).

## 3. Architecture

```
    ┌──────────────────────────────────────────────────────────────┐
    │                    twaky (mono-user instance)                │
    │                                                              │
    │  RabbitMQ ──▶ ingest ──▶ event_log ──▶ projector ──▶ AGE     │
    │  (existing)   +owner       (Postgres)   (existing)     graph │
    │                filter                                   +Email│
    │                                                              │
    │  ┌──────────────────────────────────────────────────────┐   │
    │  │  NEW: mission table (Postgres, DB=twaky)             │   │
    │  │       + LangGraph checkpointer tables (Postgres)     │   │
    │  │       + engine.py (state-transition contract)        │   │
    │  └──────────────────────────────────────────────────────┘   │
    │                                                              │
    │  ┌──────────────────────────────────────────────────────┐   │
    │  │  NEW (documented, not deployed): twaky:message:*     │   │
    │  │       enveloppe for future federation (sub-project 4)│   │
    │  └──────────────────────────────────────────────────────┘   │
    └──────────────────────────────────────────────────────────────┘
```

New Postgres artefacts (all in the existing `twaky` DB):

- Table `mission`
- Tables `checkpoints`, `checkpoint_writes`, `checkpoint_blobs` (created by `langgraph.checkpoint.postgres.PostgresSaver.setup()`)

New Python packages:

- `src/twaky/missions/` — `models.py`, `engine.py`, `guards.py`
- `src/twaky/mappers/mail_message_*.py` — 4 new mappers

## 4. Mission model

### 4.1 Schema

```sql
CREATE TABLE mission (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_email         TEXT NOT NULL,           -- == TWAKY_OWNER_EMAIL of this instance
    declared_by         TEXT NOT NULL,           -- normally == owner_email; may differ for
                                                 -- missions received via federation (sub-project 4)
    declared_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    intent_text         TEXT NOT NULL,           -- free-form NL from the user
    plan                JSONB,                   -- filled by Atlas at planning → running
    state               TEXT NOT NULL DEFAULT 'declared'
                        CHECK (state IN ('declared','planning','running',
                                         'awaiting_user','done','failed','cancelled')),
    state_reason        TEXT,                    -- why the last transition happened
    due_at              TIMESTAMPTZ,
    artifacts           JSONB NOT NULL DEFAULT '[]'::jsonb,
                                                 -- intermediate + final results, each entry
                                                 -- is {agent, tool, at, kind, ref, summary}
    langfuse_session_id TEXT,                    -- to jump from a mission to its Langfuse session
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX mission_live_idx ON mission (state)
    WHERE state IN ('declared','planning','running','awaiting_user');
CREATE INDEX mission_owner_state_idx ON mission (owner_email, state);
```

### 4.2 State machine

```
declared ──▶ planning ──▶ running ──▶ awaiting_user ──▶ running
    │            │            │              │              │
    ▼            ▼            ▼              ▼              ▼
cancelled    cancelled     done          cancelled        done
                          failed          failed         failed
```

Rules:

- `done`, `failed`, `cancelled` are terminal — no outgoing transition.
- Any non-terminal state can transition to `cancelled` (user or system abort).
- `running ⇄ awaiting_user` may loop many times (multiple user checkpoints in one mission).
- All other transitions are forbidden and raise `InvalidTransition` from the engine.

### 4.3 Grain

A mission is declared by natural-language intent (`intent_text`). Atlas, at the `planning` transition, produces a `plan` (list of `{agent, tool, args}` steps). The plan is runtime-editable: Atlas may append or reorder steps while `state='running'`. The UI (sub-project 3) shows the current plan and updates as it evolves.

## 5. Owner scoping

### 5.1 Configuration

New env var `TWAKY_OWNER_EMAIL` (added to `src/twaky/config.py` via `pydantic-settings`), **required** — the container refuses to start without it, rather than ingesting orphaned data.

Added to `.env.example` and `.env`.

### 5.2 Ingest filter

In `src/twaky/ingest.py`, before `_insert_event`:

```python
if not _matches_owner(message.exchange, payload, settings.twaky_owner_email):
    await message.ack()
    log.debug("dropped: not for owner", exchange=message.exchange)
    continue
```

`_matches_owner(exchange, payload, owner_email)` dispatches by exchange family:

- `calendar:event:*` → `owner_email in {organizer.email} ∪ {a.email for a in attendees}`
- `sabre:contact:*` → `owner_email == payload.email` (assumes contact addressbook is owner's; TBD confirm with real payload)
- `mail:message:*` → `owner_email == payload.user`
- unknown family → drop with `log.warn("no owner rule for exchange")` (safe default: drop)

Events failing the check are **acked and dropped silently** — not sent to DLQ. event_log stays owner-scoped.

## 6. Mail ingest

### 6.1 Exchanges bound (added to `AGENT_EXCHANGES` default)

- `mail:message:received`
- `mail:message:expunged`
- `mail:message:flags:updated`
- `mail:message:moved`

Other `mail:*` (mailbox lifecycle, quota, jmap state) are noise for the current use cases and are not bound in Foundations. They can be added later via env override without code change.

### 6.2 Node schema

An `Email` node in the AGE `twake` graph:

```
Email {
    message_id  : STRING (natural key, MERGE on this)
    user        : STRING (mailbox owner email — always == TWAKY_OWNER_EMAIL after filtering)
    mailbox_path: STRING (namespace + user + name, joined with '/')
    received_at : STRING (ISO 8601)
    deleted     : BOOLEAN
    read        : BOOLEAN
}
```

No relationships in Foundations. `SENT_BY`, `SENT_TO`, `THREADED_WITH` require content fetch — deferred to sub-project 2.

### 6.3 Mappers

Following the existing `sqlmapper` pattern with MERGE on natural key + SET:

- `mail_message_received.py` → creates the node, sets `deleted=false`, sets `received_at`, `mailbox_path`, `user`.
- `mail_message_expunged.py` → `MERGE (e:Email {message_id}) SET e.deleted = true`.
- `mail_message_flags_updated.py` → `MERGE (e:Email {message_id}) SET e.read = <bool from \Seen>`.
- `mail_message_moved.py` → `MERGE (e:Email {message_id}) SET e.mailbox_path = <new>`.

`mappers/__init__.py` registry extended with the 4 new routes.

## 7. LangGraph seam (Mission engine)

### 7.1 Two layers

**Layer 1 — Mission table:** the user-visible coarse state. Read by the API (sub-project 3) and by the future P2P protocol. Written *only* through `engine.py` transition functions.

**Layer 2 — LangGraph checkpointer:** the fine-grained runtime state of an in-flight mission. Uses `langgraph.checkpoint.postgres.PostgresSaver` writing to the same `twaky` Postgres database. `thread_id = str(mission.id)` — the mission id is the correlation key between both layers.

### 7.2 Engine API (`src/twaky/missions/engine.py`)

```python
def declare(intent_text: str, owner_email: str, declared_by: str,
            due_at: datetime | None = None) -> Mission
def start_planning(mission_id: UUID) -> None            # declared → planning
def commit_plan(mission_id: UUID, plan: list[dict]) -> None  # planning → running
def request_user_input(mission_id: UUID, reason: str,
                       artifact: dict) -> None          # running → awaiting_user
def resume(mission_id: UUID, user_response: dict) -> None    # awaiting_user → running
def finish(mission_id: UUID, outcome: Literal['done','failed'],
           artifacts: list[dict], reason: str = '') -> None  # running → done|failed
def cancel(mission_id: UUID, reason: str) -> None       # any non-terminal → cancelled
```

Each function:

1. `SELECT ... FOR UPDATE` the mission row (avoid races between concurrent transitions).
2. Call `guards.check_transition(current_state, target_state)` — raises `InvalidTransition` on illegal move.
3. Update the row (state, state_reason, updated_at, optionally artifacts/plan).
4. Emit the corresponding LangGraph side effect (`saver.put`, `graph.astream`, `interrupt`, `resume`) when applicable.
5. Emit a Langfuse span (`mission.<transition>`) as child of the mission's `langfuse_session_id`.

Atlas (sub-project 2) is required to go through these functions — never touch the `mission` table directly.

### 7.3 Restart resilience

At Atlas boot:

```python
for m in session.execute(
    select(Mission).where(Mission.state.in_(['planning','running','awaiting_user']))
):
    try:
        state = saver.get(config={'configurable': {'thread_id': str(m.id)}})
        if state is None:
            engine.finish(m.id, outcome='failed',
                          artifacts=[], reason='checkpoint_lost_after_restart')
        else:
            atlas.resume(m, state)  # rebuild StateGraph, resume execution
    except Exception as e:
        log.exception("resume failed", mission_id=m.id, err=str(e))
```

## 8. P2P envelope (documented, not deployed)

### 8.1 Envelope

Future exchange `twaky:message:request` (direct type, routing key = recipient's `owner_email`), queue per instance `twaky.inbox.<owner_email>`:

```json
{
  "envelope_version": "1",
  "message_id": "urn:uuid:<uuid4>",
  "correlation_id": "urn:uuid:<uuid4>",
  "from": "alice@twake-dev.maudet.cloud",
  "to":   "bob@twake-dev.maudet.cloud",
  "sent_at":    "2026-08-01T14:00:00Z",
  "expires_at": "2026-08-01T14:05:00Z",
  "intent":  "ask_availability",
  "payload": { ... intent-specific ... }
}
```

### 8.2 Initial intents

| Intent | Payload shape | Response intent |
|---|---|---|
| `ask_availability` | `{on_behalf_of_mission, window, duration_minutes}` | `ack` with `{slots: [{from,to}...]}` |
| `propose_meeting` | `{on_behalf_of_mission, ics_draft}` | `ack` with `{accepted: bool, counter?: ics_draft}` |
| `delegate_task` | `{on_behalf_of_mission, intent_text}` | `ack` with `{new_mission_id, expected_completion}` |
| `share_info` | `{topic, text, refs: [...]}` | `ack` |
| `ack` | `{ok: bool, message?: string, ...intent-specific}` | none |

Extensible: adding an intent means adding a row here + a handler, no envelope change.

### 8.3 Deferred

- **Signature scheme** — `TBD sub-project 4`. Candidate options: JWT signed with the user's LDAP `userCertificate`, or mTLS at the transport layer. Not chosen in Foundations because federation is not deployed here.
- **Exchange + queue declarations** — will be declared by the sub-project 4 ingest layer.
- **Retry / DLQ policy** — same shape as `agent.graph.ingest.dlq`, specified in sub-project 4.

Foundations only fixes the envelope contract so downstream code can rely on it.

## 9. Error handling

| Case | Behavior |
|---|---|
| Ingest — malformed payload | Reject to `agent.graph.ingest.dlq`, event_log `status='error'`. Existing behavior, unchanged. |
| Ingest — event not for owner | Ack + drop silently. `log.debug`. No event_log row. |
| Mission engine — illegal transition | Raise `InvalidTransition`. Row unchanged. Caller responsibility (Atlas or API). |
| Mission engine — DB row locked | Wait (default psycopg behavior). Timeout via `SET LOCAL statement_timeout`. |
| LangGraph — checkpoint lost after crash | Detected at boot; auto-transition `failed` with reason `checkpoint_lost_after_restart`. |
| LangGraph — checkpointer down | Log error, mission stays in current state, retried at next reconcile loop (planned). |

## 10. Testing strategy

### 10.1 Unit (pytest, < 1 s total)

- `tests/missions/test_engine_transitions.py` — every legal + illegal transition, guard errors, timestamp coherence.
- `tests/missions/test_mission_model.py` — Pydantic → SQLAlchemy roundtrip.
- `tests/mappers/test_mail_*.py` — Cypher shape assertions (mirror the existing `test_mappers.py` pattern).
- `tests/ingest/test_owner_filter.py` — `_matches_owner` dispatch table for calendar/sabre/mail with synthetic payloads.

### 10.2 Integration (pytest + ephemeral Postgres+AGE, < 30 s)

- `tests/integration/test_ingest_owner_filter.py` — publish 2 messages (one for owner, one for someone else) via a local in-memory broker, assert event_log has exactly 1 row.
- `tests/integration/test_mail_roundtrip.py` — synth mail event → event_log → projector → Email node in graph → assert properties.
- `tests/integration/test_mission_engine_roundtrip.py` — declare → plan → run → awaiting_user → resume → done, assert DB state at each step + Langfuse spans captured.
- `tests/integration/test_restart_resilience.py` — simulate crash between engine `commit_plan` and LangGraph checkpoint write, assert boot auto-transitions to `failed`.

### 10.3 End-to-end (bash + live docker compose stack, ~30 s)

Extend `scripts/scenarios.sh` with a new `scripts/scenarios-foundations.sh`:

1. `TWAKY_OWNER_EMAIL=alice@…` restart ingest + projector.
2. Publish a mail event for Alice → assert Email node exists.
3. Publish a mail event for Bob → assert nothing landed in Alice's event_log.
4. Declare a mission via engine.declare (in a Python one-liner via `docker compose run twaky-agent python -c ...`).
5. Force the mission through all 6 states via direct engine calls.
6. Kill the twaky-agent container mid-way through `running`, restart, assert the mission auto-transitions to `failed`.

### 10.4 CI

Existing `.github/workflows/ci.yml` covers unit + integration (skips integration tests when Postgres is not reachable). Nothing to add for Foundations.

## 11. Rollout

Everything in Foundations is additive:

- New table (`mission`) — created by a new SQL init script `sql/004_init_mission.sh` (runs on first-boot volume init; for existing volumes, a one-shot manual `psql -f` is documented in the migration section of the plan).
- New env var `TWAKY_OWNER_EMAIL` — required, breaking for anyone running without it. Documented as an upgrade note.
- New mappers — additive, no conflict with existing exchanges.
- Owner filter in ingest — will drop events that were previously ingested; expected and desired.

No data migration needed for the graph: existing nodes (from earlier scenarios) can be wiped or left alone; they'll simply be "unfiltered legacy data". A clean install starts empty and stays owner-scoped from day one.

## 12. Open questions to close before implementation

- The exact schema of a `sabre:contact:*` payload — need to sniff one real event to confirm the owner-matching rule.
- Whether `mail:message:flags:updated` payload includes the full flag set or a delta — depends on what mail-events-bridge exposes; needs a peek.
- The Langfuse session-id lifecycle when a mission spans hours or days — a single long-lived session, or session-per-day linked by common `mission_id`? Not blocking; the code will start with single session and be revised if that hurts UX.

## 13. Sub-projects that will build on this

- **Sub-project 2 — Agents + Atlas skeleton.** Uses `engine.py` to drive missions. Registers Chronos/Plume/Iris as LangChain `@tool` functions. Atlas as a `StateGraph` checkpointed via the LangGraph seam.
- **Sub-project 3 — API + frontend.** Exposes `mission` CRUD and a `WS /events` stream. Renders the Control Tower.
- **Sub-project 4 — Federation.** Implements the P2P envelope defined here; picks a signature scheme; deploys multi-instance.
- **Sub-project 5 — Write-side actions.** Writes to CalDAV / JMAP under human approval; extends the `awaiting_user` state with an approval workflow.
