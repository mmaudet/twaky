# SP5b — Write-side Learning (Design)

**Date:** 2026-08-13
**Status:** Draft — awaiting user review
**Author:** Michel-Marie Maudet + Claude (brainstorming)
**Sub-project:** SP5b (formerly deferred as "SP7 write-side" in the SP5 sentinels design)

## 1. Problem

The mail sentinel runs 24/7 and produces drafts, but the two tables
designed to make it *smarter over time* — `mail_sentinel_memory` and
`mail_sentinel_learned_pattern` — remain empty. Consequences:

- Drafts never adapt to the user's real writing preferences. Every draft
  is generated from the same static style profile (`style_profile.py`).
- Recurring sender→folder or sender→trust patterns are never captured.
  Every mail from a repeat sender goes through the full rule cascade +
  potentially an LLM call.
- The user has to manually create rules for behaviors the sentinel could
  learn autonomously.

The MVP (SP5-SP6) deliberately deferred the write-side to keep scope
tight. SP5b closes the gap.

## 2. Goal

After SP5b ships:

1. When the owner sends a mail whose body differs > 5 % from an
   AI-generated draft that Twaky produced, at least one
   `mail_sentinel_memory` row is inserted with the correct `kind` +
   `scope` inferred by an LLM diff extractor.
2. When the owner moves a mail from Spam back to Inbox (or vice versa),
   a `mail_sentinel_learned_pattern` row with `rule_name="trust_sender"`
   (or `"block_sender"`) is inserted or bumped. After 3 consistent
   observations from the same sender, the pattern becomes active and
   short-circuits the spam triage.
3. When the owner moves a mail from Inbox to a custom mailbox (e.g.
   `Facturation`), a `mail_sentinel_learned_pattern` row with
   `rule_name="label:Facturation"` is inserted or bumped. After
   activation, future mails from the same sender are auto-labeled
   without any LLM call.
4. Newly learned lessons are auto-saved. The owner sees them in the
   `/sentinels/mail` UI and can revoke any individual lesson with one
   click. Auto-saved memories carry a 7-day TTL that auto-extends by
   7 days each time they are used in a draft.
5. The learning pipeline is best-effort: any failure logs an error and
   continues; ingest is never blocked.
6. A feature flag `settings.mail_sentinel_observer_enabled` (default
   `False`) governs the entire observer path. Toggle without redeploy.

## 3. Non-goals (out of scope for SP5b)

- Autonomous mail sending. The `submit()` method on the JMAP adapter
  still raises `NotImplementedError`. That remains for a future sprint.
- Cross-user federation. SP5b remains mono-user (owner_email implicit).
- Webhook integration with Twake Mail. The observer relies exclusively
  on JMAP polling to remain client-agnostic (Twake Web, Twake Mobile,
  Thunderbird, any JMAP client all work identically).
- Replacing the existing `retrieve_memories` node. SP5b only improves
  its ranking; the node itself is not rewritten.

## 4. Global Constraints

- Language: Python 3.12 for backend, TypeScript for frontend.
- LLM: Mistral-Small-3.2-24B-Instruct-2506-FP8 via
  `https://chat.lucie.ovh.linagora.com/v1/` (config already in place
  via `MAIL_SENTINEL_*` env vars).
- Database: PostgreSQL 15 (twaky-pg container).
- Frontend: Next.js 15 App Router, following existing patterns.
- Migration tooling: Alembic (existing setup in `alembic/versions/`).
- Feature flag naming: snake_case boolean settings.
- Structured LLM outputs: Pydantic models, `hardening=COMPACT` for
  extractors (no expensive JSON self-repair — extraction is
  non-critical).
- The observer must never block the existing ingest path. Any failure
  is logged and the tick continues.
- All new tables use `TIMESTAMPTZ` and `UUID PRIMARY KEY DEFAULT
  gen_random_uuid()` following the codebase convention.
- Ruff + mypy + pytest must stay green.

## 5. Architecture

### 5.1 High-level view

```
Poller JMAP (existing)                    Poller SP5b (new)
       │                                          │
       │ Email/query INBOX + Email/get            │ Email/changes per watched mailbox
       │ (inbound mail)                           │ (Sent, Spam, Trash, custom folders)
       ▼                                          ▼
┌──────────────────────────┐              ┌──────────────────────────────┐
│ MailSentinel.process()   │              │ MailSentinel.observe()       │
│ pipeline (unchanged):    │              │ (NEW) pipeline:              │
│  match_rules             │              │  classify_observation        │
│  → decide_action         │              │  → route to extractor        │
│  → draft_reply           │              │  → save memory/pattern       │
└──────────────────────────┘              └──────────────────────────────┘
       │                                          │
       ▼                                          ▼
┌────────────────────────────────────────────────────────────────────┐
│ Postgres:                                                          │
│   mail_sentinel_memory       (existing, +4 columns)                │
│   mail_sentinel_learned_pattern (existing, unchanged)              │
│   mail_sentinel_mailbox_state (NEW — JMAP state per mailbox)       │
│   mail_sentinel_observation   (NEW — audit log, 30d purge)         │
└────────────────────────────────────────────────────────────────────┘
```

Two separate paths share the same container, the same OAuth-authenticated
JMAP client, and the same store modules. They never overlap:
`process` handles inbound mail and produces drafts + missions;
`observe` handles user actions and produces memories + patterns.

### 5.2 New Python modules

- `src/twaky/sentinels/mail/observer.py` — the extended JMAP poller.
  Called once per tick from the existing `_jmap_poll_loop`. Iterates
  watched mailboxes, queries `Email/changes`, classifies each
  observation, and dispatches to the appropriate extractor.
- `src/twaky/sentinels/mail/extractors/__init__.py`
- `src/twaky/sentinels/mail/extractors/draft_diff.py` — matches a sent
  mail against a recent mission and extracts memories from the
  AI-vs-shipped diff via LLM.
- `src/twaky/sentinels/mail/extractors/reclassification.py` — pure
  deterministic logic. Increments trust_sender / block_sender patterns.
- `src/twaky/sentinels/mail/extractors/folder_move.py` — increments
  `label:X` patterns; optionally calls a cheap LLM to decide whether the
  move deserves a durable memory beyond the pattern.
- `src/twaky/sentinels/mail/prompts/extract_memory_from_diff.py` — LLM
  prompt returning `list[ExtractedMemory]`.
- `src/twaky/sentinels/mail/prompts/extract_memory_from_move.py` — LLM
  prompt returning `{should_extract, memory?}`.
- `src/twaky/sentinels/mail/store/mailbox_state.py` — CRUD for
  `mail_sentinel_mailbox_state`.
- `src/twaky/sentinels/mail/store/observations.py` — CRUD for
  `mail_sentinel_observation` (idempotent insert on unique constraint).

Modules extended (not rewritten):

- `src/twaky/sentinels/mail/store/memories.py` — new columns handled
  (source, sender_email, mission_id, confidence), new `touch(ids)`
  method that pushes `expires_at` forward by 7 days, new ranked
  `list_for_prompt(sender, domain)` method.
- `src/twaky/sentinels/mail/nodes.py` — `retrieve_memories` calls the
  new `list_for_prompt` + `touch`; `match_rules` gains branches for
  `learned_pattern.rule_name.startswith("label:")` and for
  `rule_name in ("trust_sender", "block_sender")`.
- `src/twaky/sentinels/mail/sentinel.py` — new `observe()` method
  parallel to `process()`, called from the poller loop.
- `src/twaky/config.py` — new `mail_sentinel_observer_enabled: bool`
  field, new `mail_sentinel_watched_mailbox_roles: str =
  "sent,junk,trash"` (comma-separated).

## 6. Detection layer (observer)

Every 60 seconds, the observer runs one tick against every watched
mailbox. Watched mailboxes are resolved dynamically:

1. `Mailbox/query` returns all the account's mailboxes.
2. Filter: keep mailboxes whose `role` is in the configured list
   (`sent`, `junk`, `trash` by default) PLUS every mailbox whose `role`
   is null AND whose `parentId` is null AND whose name is not one of
   the standard system names (`Inbox`, `Drafts`, `Templates`,
   `Outbox`, `Archive`). This captures user-created top-level folders
   like `Facturation`, `Recrutement`.
3. For each watched mailbox, load the last-known `jmap_state` from
   `mail_sentinel_mailbox_state`. If absent (bootstrap), call
   `Mailbox/get` to read the current `state`, store it, and skip the
   tick — no historical replay.
4. Call `Email/changes` with `sinceState=<last_seen>`. Receive
   `created[]`, `updated[]`, `destroyed[]`, and the new state.
5. For each affected email ID, call `Email/get` to load
   `{mailboxIds, keywords, from, subject, threadId, sentAt,
   receivedAt, headers}`.
6. Classify the change per the table in §6.1.
7. Dispatch to the extractor. Insert into `mail_sentinel_observation`
   ON CONFLICT DO NOTHING (idempotence key: `(email_id, mailbox_id,
   observation_type)`).
8. UPSERT `mail_sentinel_mailbox_state (mailbox_id, jmap_state, ...)`
   ONLY after the tick's extractions completed. A crash mid-tick leaves
   the state stale so the next tick replays; the UNIQUE constraint on
   observations absorbs duplicates.

### 6.1 Observation classification

Deterministic logic in `observer.py`, no LLM:

| Detected signal | JMAP condition | Observation type |
|---|---|---|
| `draft_sent` | Email appeared in mailbox with `role=sent` AND has an `In-Reply-To` or `References` header | routes to `draft_diff` extractor |
| `unmarked_spam` | Email removed from mailbox with `role=junk` OR keyword `$junk` removed | routes to `reclassification(direction=out)` |
| `marked_spam` | Email added to mailbox with `role=junk` OR keyword `$junk` added | routes to `reclassification(direction=in)` |
| `moved_to_custom` | Email moved from mailbox with `role=inbox` to a mailbox with `role=null` (custom) | routes to `folder_move` |

Emails that don't match any signal are ignored (no observation row
inserted).

## 7. Extraction layer

### 7.1 `draft_diff` extractor (LLM-based, richest signal)

Trigger: an email with observation_type `draft_sent`.

1. **Match a mission**. Query:
   ```sql
   SELECT m.*
   FROM mission m
   WHERE m.state = 'awaiting_user'
     AND m.declared_by = 'sentinel:mail'
     AND m.owner_email = %s
     AND m.created_at > now() - INTERVAL '7 days'
   ORDER BY m.created_at DESC
   LIMIT 20
   ```
   Then filter in-process: keep only missions whose `artifacts` jsonb
   contains a message-id matching the sent mail's `In-Reply-To` or
   `References` header. If no match, insert observation with
   `extraction_outcome='skipped_no_match'` and return.
2. **Load the original draft** from `mission.artifacts[0].body` (schema
   already used by SP6d).
3. **Trivial-diff guard**. Compute a Levenshtein ratio between the AI
   draft and the sent body. If `ratio < 0.05` (< 5 % change), skip with
   `extraction_outcome='skipped_trivial'`.
4. **LLM call** using `extract_memory_from_diff` prompt with the `chat`
   tier and `hardening=COMPACT`. Structured output:
   ```python
   class ExtractedMemory(BaseModel):
       kind: Literal['fact', 'procedure', 'preference']
       scope: Literal['sender', 'domain', 'global']
       scope_value: str  # for scope=global, use "*"
       content: str  # <=200 chars, actionable lesson
       confidence: float  # 0.0-1.0

   class DraftDiffOutput(BaseModel):
       memories: list[ExtractedMemory]
       should_delete_previous_memory_ids: list[UUID] = []
   ```
5. **Filter**: keep only memories with `confidence >= 0.7`.
6. **Delete contradicted memories** if any listed in
   `should_delete_previous_memory_ids`.
7. **Insert** each surviving memory into `mail_sentinel_memory`:
   - `source='auto_diff'`
   - `mission_id=<matched mission>`
   - `confidence=<from LLM>`
   - `sender_email=<recipient of the sent mail>` when `scope='sender'`
   - `evidence` jsonb: `{"ai_draft": "...", "shipped": "...",
     "mission_id": "..."}`
8. **Transition the mission** to `state='done'`,
   `state_reason='draft_sent_by_user'`.
9. Insert observation with `extraction_outcome='extracted'`,
   `memory_ids=<inserted UUIDs>`.

### 7.2 `reclassification` extractor (deterministic, no LLM)

Trigger: observation_type `marked_spam` or `unmarked_spam`.

Pure logic:

| direction | Effect on pattern | Effect on memory |
|---|---|---|
| `unmarked_spam` | `lp_store.record_decision(sender, "trust_sender", confidence_hint=0.95)` | `kind='fact', scope='sender', scope_value=sender, content="Legit sender — do not classify as spam.", source='auto_reclass', confidence=1.0` |
| `marked_spam` | `lp_store.record_decision(sender, "block_sender", confidence_hint=0.9)` | `kind='fact', scope='sender', scope_value=sender, content="Treat this sender as spam by default.", source='auto_reclass', confidence=1.0` |

Additionally: if a row exists in `mail_sentinel_spam_decision` for this
email_id with `restored_at IS NULL`, update
`restored_at=now(), restored_by='user'`. The spam-decision table stays
consistent with the user's authoritative action.

Observation inserted with `extraction_outcome='extracted'`,
`pattern_ids=[<record_decision result>]`,
`memory_ids=[<inserted memory>]`.

### 7.3 `folder_move` extractor (hybrid)

Trigger: observation_type `moved_to_custom`.

1. **Resolve destination**: `Mailbox/get(destination_mailbox_id)` →
   `name` (e.g. "Facturation"). Sanitize name for the pattern key
   (alphanumeric + hyphen only, matches JMAP flag naming rules).
2. **Pattern (always)**: `lp_store.record_decision(sender,
   f"label:{sanitized_name}", confidence_hint=0.85)`.
3. **LLM (economy tier)** using `extract_memory_from_move` prompt.
   Structured output:
   ```python
   class FolderMoveOutput(BaseModel):
       should_extract: bool
       memory: ExtractedMemory | None
   ```
   The prompt gives the LLM the sender history (how many times this
   sender was seen before) and the destination folder, and asks whether
   this move reflects a durable relationship worth memorizing.
4. If `should_extract=True` and `memory.confidence >= 0.7`, insert the
   memory with `source='auto_move'`.
5. Observation inserted with the appropriate outcome.

## 8. Storage

### 8.1 Migration `alembic/versions/xxxx_sp5b_write_side.sql`

```sql
-- 1. Extend mail_sentinel_memory
ALTER TABLE mail_sentinel_memory
  ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'
    CHECK (source IN ('manual', 'auto_diff', 'auto_reclass', 'auto_move')),
  ADD COLUMN sender_email TEXT,
  ADD COLUMN mission_id UUID REFERENCES mission(id) ON DELETE SET NULL,
  ADD COLUMN confidence NUMERIC(3,2) CHECK (confidence >= 0 AND confidence <= 1);

CREATE INDEX mail_sentinel_memory_by_source
  ON mail_sentinel_memory (source, created_at DESC);

-- 2. Mailbox state
CREATE TABLE mail_sentinel_mailbox_state (
  mailbox_id  TEXT PRIMARY KEY,
  role        TEXT,
  name        TEXT,
  jmap_state  TEXT NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3. Observation log
CREATE TABLE mail_sentinel_observation (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email_id           TEXT NOT NULL,
  mailbox_id         TEXT NOT NULL,
  observation_type   TEXT NOT NULL
    CHECK (observation_type IN ('draft_sent','marked_spam','unmarked_spam','moved_to_custom')),
  observed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  extraction_outcome TEXT NOT NULL
    CHECK (extraction_outcome IN ('extracted','skipped_trivial','skipped_no_match','error')),
  memory_ids         UUID[] DEFAULT '{}',
  pattern_ids        UUID[] DEFAULT '{}',
  error_repr         TEXT,
  UNIQUE (email_id, mailbox_id, observation_type)
);

CREATE INDEX mail_sentinel_observation_recent
  ON mail_sentinel_observation (observed_at DESC);
```

Reversible: `alembic downgrade -1` drops the two new tables and drops
the four added columns. Existing memories survive intact (source reverts
to 'manual' via DEFAULT before column removal).

### 8.2 TTL policy

`mail_sentinel_memory.expires_at` defaults to `now() + 7 days` (existing
schema, unchanged). New behavior: each time a memory is injected into a
`draft_reply` prompt, `mem_store.touch(ids)` sets
`expires_at = now() + 7 days`. Memories that keep being useful stay
alive indefinitely; orphaned memories expire naturally.

A UI action "Keep permanent" (button in Memories tab) sets
`expires_at = NULL`. The `list_for_prompt` query already handles NULL
correctly (`WHERE expires_at IS NULL OR expires_at > now()`).

### 8.3 Housekeeping

New daily task `housekeeping_observations` in the existing sentinel
housekeeping loop:

```sql
DELETE FROM mail_sentinel_observation
WHERE observed_at < now() - INTERVAL '30 days';
```

Observations are for debug/audit only, not load-bearing beyond
idempotence within the current tick cycle.

## 9. Injection back into the pipeline

### 9.1 `retrieve_memories` node (existing, evolved)

Old ranking: unordered / by scope only. New SQL:

```sql
WITH candidates AS (
  SELECT id, kind, scope, scope_value, content, confidence, source,
         (CASE scope
            WHEN 'sender' THEN 3.0
            WHEN 'domain' THEN 1.5
            WHEN 'global' THEN 1.0
          END) AS scope_weight,
         COALESCE(confidence, 0.5) AS conf,
         EXTRACT(EPOCH FROM (now() - created_at)) / 86400.0 AS age_days
  FROM mail_sentinel_memory
  WHERE ((scope = 'sender' AND scope_value = %s)
      OR (scope = 'domain' AND scope_value = %s)
      OR (scope = 'global'))
    AND (expires_at IS NULL OR expires_at > now())
)
SELECT id, kind, scope, scope_value, content, source
FROM candidates
ORDER BY (scope_weight * conf * exp(-age_days / 30.0)) DESC
LIMIT %s;
```

The `LIMIT` is `settings.mail_sentinel_memory_inject_max` (default 16,
config already exists).

After retrieval, the node calls `mem_store.touch([id, id, ...])` on the
returned IDs before returning them in the state.

### 9.2 `match_rules` node (existing, evolved)

Add two new branches before the existing rule cascade:

```python
active_pattern = lp_store.by_sender(sender_email)
if active_pattern is not None:
    if active_pattern.rule_name.startswith("label:"):
        return {"matched_by": "learned_pattern",
                "rule_name": active_pattern.rule_name}
    if active_pattern.rule_name == "trust_sender":
        # Skip spam triage and go straight to draft
        return {"matched_by": "learned_pattern",
                "rule_name": "trust_sender",
                "skip_spam_triage": True}
    if active_pattern.rule_name == "block_sender":
        return {"matched_by": "learned_pattern",
                "rule_name": "block_sender",
                "bucket": "spam"}
```

The `skip_spam_triage` and `bucket` fields are new; the pipeline routing
respects them. Existing behavior when no active pattern exists is
unchanged.

### 9.3 Prompt (`draft_reply`) — unchanged format

The `<memories>` block already exists and consumes an unstructured list
of `{id, content}`. Source metadata is not injected into the prompt —
the LLM treats auto and manual memories identically. This matches how
the design already thinks of memories: a lesson is a lesson.

### 9.4 Run trace

Each `sentinel_run.trace` (jsonb) gains a `memories_used: [uuid,...]`
entry listing the IDs of memories injected during that run. Powers the
UI "this draft used these memories" audit view.

## 10. UI (frontend)

### 10.1 Memories tab

Refresh the existing `frontend/src/app/sentinels/mail/page.tsx` Memories
sub-tab. New card component `MemoryCard.tsx`:

```
Memories                                        [+ New memory]

[Filter: All ▼]  [Source: All ▼]  [Scope: All ▼]

┌─────────────────────────────────────────────────────────────────┐
│ 🤖 auto_diff · sender · alexandre@linagora.com    conf 0.85    │
│ Michel utilise 'Bonjour' et non 'Cher' avec Alexandre           │
│ Learned 2 days ago, expires in 5 days · from mission #de208d63  │
│ [Forget]  [Keep permanent]                                       │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ ✍️ manual · global                                              │
│ Toujours signer 'Michel-Marie', jamais 'Michel'                 │
│ Created 12 days ago, no expiry                                  │
│ [Edit]  [Delete]                                                 │
└─────────────────────────────────────────────────────────────────┘
```

- Badge icon: 🤖 for `auto_*`, ✍️ for `manual`.
- `Forget` → existing `DELETE /mail-sentinel/memories/{id}` endpoint.
- `Keep permanent` → new endpoint `PATCH
  /mail-sentinel/memories/{id}` with `{persist: true}` sets
  `expires_at=NULL`.
- Filters by `source`, `scope`, `kind`.
- Default sort: `created_at DESC`.
- Mission link → opens `/missions/{id}` in new tab (audit trail).

### 10.2 Learned Patterns tab

Cards gain a type badge and a "savings" hint:

```
Learned Patterns                             12 active · 3 candidates

┌────────────────────────────────────────────────────────────────┐
│ comptable@fournisseur.com                                       │
│ 🏷️ label:Facturation      conf 0.95  ·  8 samples              │
│ Saves ~1 LLM call/msg · last confirmed 2 days ago              │
│ [Forget]                                                        │
└────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────┐
│ newsletter@notifiche-medium.com                                 │
│ ✅ trust_sender      conf 0.90  ·  3 samples                    │
│ Won't be flagged as spam anymore                                │
│ [Forget]                                                        │
└────────────────────────────────────────────────────────────────┘
```

Rows split into two sections: "active" (`is_active == True`) and
"candidates" (`evidence_count < 3` or `confidence < 0.9`).

### 10.3 Runs tab — new sub-tab Observations

The existing Runs page gets a second sub-tab:

```
Runs [Ingest] [Observations]

Observations (last 100)

Time                    Type              Email                        Outcome
──────────────────────────────────────────────────────────────────────────────
2026-08-13 17:42:11    draft_sent        Re: Surcotisations URSSAF    ✅ 2 memories extracted
2026-08-13 17:31:04    unmarked_spam     Newsletter Medium daily      ✅ trust_sender pattern
2026-08-13 16:22:59    moved_to_custom   Facture N°2026-0812          ⏭️ skipped_trivial (1 sample)
```

Click on a row → detail modal with the diff (for draft_sent), the
memories/patterns created, and a link to the source email in Twake Mail.

### 10.4 No new top-level tab

The existing `Rules · Memories · Learned Patterns · Runs · Auth ·
Recent Spam` bar is unchanged.

### 10.5 Frontend estimate

Roughly 1.5 dev-days:

- `MemoryCard.tsx` (new, ~120 LOC)
- Memories page filter UI (~60 LOC extension)
- `LearnedPatternCard.tsx` update (~40 LOC)
- `RunsObservationsSubtab.tsx` (new, ~150 LOC)
- Regenerate `frontend/src/api/generated.ts` via `make api-types`

## 11. REST API

New / modified endpoints:

- `GET /mail-sentinel/memories` — response gains `source`,
  `confidence`, `mission_id`.
- `PATCH /mail-sentinel/memories/{id}` — new, accepts `{persist: bool}`.
- `GET /mail-sentinel/observations` — new, paginated, filterable by
  type and outcome.

## 12. Feature flag rollout

`settings.mail_sentinel_observer_enabled` default `False`.

Rollout plan:

1. Merge SP5b PRs into main with flag OFF.
2. Deploy on athena, verify no regression on ingest path.
3. Enable flag on athena for 48h. Monitor:
   - `mail_sentinel_observation` rows accumulate
   - `mail_sentinel_memory` gets `auto_*` rows
   - No spike in `sentinel_run` errors
4. If green, flip default to `True` in a follow-up commit.
5. If red, flip flag OFF; investigate; no code rollback needed.

## 13. Testing

### 13.1 Unit tests — extractors

- `tests/sentinels/mail/extractors/test_draft_diff.py` — 8 cases via
  YAML fixtures, LLM mocked: greeting change, closing change, phrasing
  preference, factual addition, minimal diff < 5 %, non-reply mail
  (skip), no mission match, LLM confidence < 0.7.
- `tests/sentinels/mail/extractors/test_reclassification.py` — 6 cases,
  no LLM: marked_spam, unmarked_spam, restored decision, idempotence on
  re-observation, cumulative 3 samples → active pattern, existing
  trust_sender bump.
- `tests/sentinels/mail/extractors/test_folder_move.py` — 4 cases, LLM
  mocked: `should_extract=True` → memory + pattern, `should_extract=
  False` → pattern only, custom folder not resolved → skip, INBOX →
  INBOX (no-op).

### 13.2 Unit tests — observer and state

- `tests/sentinels/mail/test_observer.py` — mock JMAP client,
  sequenced `Email/changes` responses, verifies dispatch to correct
  extractor, bootstrap skips historical replay, crash mid-tick re-runs
  next tick without duplicates.

### 13.3 Store tests

- `tests/sentinels/mail/store/test_memories_extended.py` — new
  `source` field, `mission_id` FK, `touch()` extends TTL,
  `list_for_prompt` returns weighted ranking.
- `tests/sentinels/mail/store/test_mailbox_state.py` — UPSERT
  `jmap_state`, bootstrap on absent row.
- `tests/sentinels/mail/store/test_observations.py` — idempotent
  insert via UNIQUE constraint, 30-day purge.

### 13.4 End-to-end

- `tests/sentinels/mail/test_observe_pipeline.py` — spin the real
  `MailSentinel.observe()` against an in-memory JMAP adapter and a real
  Postgres fixture (existing pytest fixture in the suite). Verifies one
  complete cycle produces correct rows and that a subsequent `process`
  call uses the new memories and patterns in `retrieve_memories` and
  `match_rules`.

### 13.5 Eval fixtures

- `tests/evals/mail_sp5b/` — 3 YAML fixtures:
  - `draft_diff_preference_change.yaml` — greeting preference lesson
  - `reclassification_3_samples.yaml` — cumulative trust_sender
    activation
  - `folder_move_no_repetition.yaml` — single move, no pattern
    activation

Runnable via the existing eval harness, produces a human-readable
report.

Coverage target: > 85 % on new modules, no regression on existing
modules.

## 14. Observability

### 14.1 Langfuse

Each LLM call in the extractors becomes a Langfuse trace with
`session_id="extract_{observation_id}"`. Prompt, mail, memory produced
visible for audit and prompt tuning.

### 14.2 Structured logs

Each observer tick logs one structured line:

```json
{
  "event": "observer_tick_done",
  "mailboxes_polled": 4,
  "observations_created": 2,
  "memories_created": 1,
  "patterns_updated": 1,
  "llm_calls": 2,
  "duration_ms": 3421
}
```

### 14.3 Stats dashboard (existing `/stats` page)

New tiles:

- Total auto-learned memories by source (bar chart).
- Active learned patterns count (single number).
- Observation activity last 24h (line chart).

## 15. Error handling

Learning is best-effort. Any failure logs and continues; ingest is
never blocked.

- **JMAP `Email/changes` failure** — 3-retry backoff (3 s, 9 s, 27 s).
  On persistent failure: skip this mailbox for this tick, retry next
  tick. `mail_sentinel_mailbox_state.jmap_state` NOT updated → next
  tick replays cleanly.
- **LLM failure** (timeout / rate limit / invalid JSON) — insert
  observation with `extraction_outcome='error'` + `error_repr`. No
  memory inserted. No retry (learning waits for the next similar
  observation).
- **Contradiction with an existing memory** — handled inside the
  extractor via `should_delete_previous_memory_ids` in the LLM output.
  No accumulated conflicts.
- **Mission not found for a sent draft** — not an error;
  `extraction_outcome='skipped_no_match'`. The owner wrote an original
  mail without going through a Twaky draft, nothing to learn from.
- **JMAP token expired** — the existing refresh mechanism handles it.
  No SP5b code needed.

### 15.1 Rollback

Two levels:

- **Feature flag OFF** — instant, no code deploy.
- **Database migration** — `alembic downgrade -1` reversibly drops the
  two new tables and the four added columns. Existing memories
  survive intact (source reverts to `manual` default before column
  removal).

## 16. File structure summary

```
src/twaky/sentinels/mail/
├── observer.py                    (NEW)
├── extractors/                    (NEW)
│   ├── __init__.py
│   ├── draft_diff.py
│   ├── reclassification.py
│   └── folder_move.py
├── prompts/
│   ├── extract_memory_from_diff.py    (NEW)
│   └── extract_memory_from_move.py    (NEW)
├── store/
│   ├── memories.py                (extended: source, sender_email,
│   │                               mission_id, confidence, touch,
│   │                               list_for_prompt)
│   ├── mailbox_state.py           (NEW)
│   └── observations.py            (NEW)
├── nodes.py                       (extended: retrieve_memories ranking,
│                                   match_rules learned_pattern branches)
└── sentinel.py                    (extended: observe() method)

src/twaky/config.py                (extended: 2 new settings)

alembic/versions/
└── xxxx_sp5b_write_side.sql       (NEW)

src/twaky/api/routers/
└── mail_sentinel.py               (extended: PATCH memories, GET
                                    observations)

frontend/src/app/sentinels/mail/
├── page.tsx                       (extended: filters on Memories tab)
├── components/
│   ├── MemoryCard.tsx             (NEW)
│   ├── LearnedPatternCard.tsx     (extended: type badge + savings)
│   └── RunsObservationsSubtab.tsx (NEW)

tests/sentinels/mail/
├── extractors/                    (NEW, 3 files)
├── store/
│   ├── test_memories_extended.py  (NEW)
│   ├── test_mailbox_state.py      (NEW)
│   └── test_observations.py       (NEW)
├── test_observer.py               (NEW)
└── test_observe_pipeline.py       (NEW)

tests/evals/mail_sp5b/             (NEW, 3 YAML fixtures)
```

## 17. Effort estimate

- Backend (observer + extractors + prompts + store) — 2 days
- Database migration + tests — 0.5 day
- Frontend (Memories/Patterns/Observations UI + API types) — 1.5 days
- Eval fixtures + progressive rollout — 0.5 day
- **Total: ~4.5 days**

## 18. Open questions (none blocking)

- Should custom-folder detection be auto (walk `Mailbox/query` result)
  or user-configured (explicit allowlist in the UI)?
  → Decision: auto for MVP. Users can revoke individual patterns via
  the Learned Patterns tab if a folder they didn't want to be tracked
  produces noise. If this becomes a source of friction, a future
  sprint can add an allowlist UI.
- Should the observer poll at the same 60 s interval as ingest or on
  its own schedule?
  → Decision: same tick, sequential (`observe` runs at the end of each
  `_jmap_poll_loop` iteration). Simpler ops, no separate scheduler.

---

## Appendix A — LLM prompt drafts (indicative)

### A.1 `extract_memory_from_diff`

```
You compare an AI-generated draft with what the user actually sent, and
extract durable lessons the AI can apply to future replies.

Return a JSON object with a "memories" array (0 or more items) and an
optional "should_delete_previous_memory_ids" array (default empty).

Guidelines:
- Only extract lessons that will apply beyond this specific mail.
- Prefer scope="sender" when the change is specific to this correspondent.
- Prefer scope="domain" when the change would apply to any correspondent
  in the same organization.
- Prefer scope="global" only when the lesson clearly applies to every
  reply the user writes.
- Ignore purely factual insertions the user added (dates, numbers, names
  present in the incoming mail) — those are context, not lessons.
- Include a confidence between 0 and 1. Use ≥0.9 only when the diff
  clearly demonstrates a durable preference.
- If a previous memory contradicts what the user just did, list its ID
  under should_delete_previous_memory_ids.

Sender: {sender_email}
Recipient: {recipient_email}
Thread language: {language}

AI draft:
"""
{ai_draft}
"""

User's sent version:
"""
{shipped_body}
"""

Previous memories for this sender:
{previous_memories}
```

### A.2 `extract_memory_from_move`

```
The user moved a mail from Inbox to a custom folder. Decide whether this
move reflects a durable relationship worth memorizing beyond the
statistical pattern that was already recorded.

Return JSON: {"should_extract": bool, "memory": ExtractedMemory | null}

Extract a memory only when:
- The sender has been seen ≥ 3 times before AND consistently classified,
  OR
- The destination folder name clearly implies a lasting role for the
  sender (e.g. "Facturation" for an accountant).

Skip when:
- First contact with a new sender (single move, no pattern yet).
- Destination folder name is generic (e.g. "Archive").

Sender: {sender_email} (seen {history_count} times before)
Destination folder: {folder_name}
Subject: {subject}
```

---

**End of design document.**
