# SP6c — Early Spam Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **The spec at `docs/superpowers/specs/2026-08-10-sp6c-early-spam-filter-design.md` is the source of truth for every design detail; this plan is the sequencing + TDD scaffold on top of it.** Read the relevant spec section before starting each task.

**Goal:** Insert a `spam_triage` stage between `load_thread` and `match_rules` in the mail-sentinel pipeline that routes clearly-spam mails to one of three buckets (`spam` / `newsletter` / `phishing-alert`), consuming rspamd's upstream verdict as the primary signal and using an ECONOMY-tier local Qwen3-VL LLM only on the grey-zone residual. Bias hard toward false-negatives (<0.5% HAM FP tolerance).

**Architecture:** New pipeline node `spam_triage` in `src/twaky/sentinels/mail/nodes.py`, gated by `sentinel.config_values.spam_filter_enabled` (default false). New table `mail_sentinel_spam_decision` with restore audit. New REST subrouter `/mail-sentinel/spam` (list + restore + stats). New 6th tab "Recent Spam" on `/sentinels/mail` with toggle + list + restore. New `MailAdapter.set_keyword` method + `set_keywords_bulk` for atomic multi-keyword restore. New LLM `UseCase.SPAM_CHECK` at ECONOMY tier + new `spam_check_prompt` module. Only `phishing-alert` bucket emits a mission (audit trail for owner); `spam` and `newsletter` are silent, observable via the tab.

**Tech Stack:** Python 3.12, psycopg3 (raw SQL), FastAPI, pydantic v2, `langchain_litellm.ChatLiteLLM` via SP6 T12 `structured_call`, `httpx` for JMAP, Next.js 15, TanStack Query v5, openapi-fetch, shadcn/ui.

## Global Constraints

Copied verbatim from spec §15 — every task's requirements implicitly include this section.

- **Endpoints**: `/mail-sentinel/spam/*` at API root, no `/api` prefix (frontend rewrites via `next.config.ts`).
- **Table**: `mail_sentinel_spam_decision` (singular, unquoted).
- **No NOTIFY channel** for spam_decisions — single-writer, readers tolerate few-second staleness.
- **Migration convention**: `sql/011_init_spam_decision.sh` matches SP6 T1 / SP6b T1 template (bash + heredoc'd psql).
- **Signal source enum values** (DB CHECK): exactly `{'rspamd_junk_keyword','rspamd_nonjunk_pass_through','rspamd_status_reject','rspamd_status_rewrite','heuristic_newsletter','llm_grey_zone'}`.
- **Bucket enum values** (DB CHECK): exactly `{'spam','newsletter','phishing-alert'}` (hyphen in `phishing-alert` intentional).
- **`spam_filter_enabled` default false** — SP6c ships inactive; owner opts in via UI toggle. Node checks this FIRST before any other work.
- **LLM SPAM_CHECK use case**: mapped to `Tier.ECONOMY`; MUST use `Hardening.COMPACT` (third-party content is instruction-adversarial).
- **Confidence thresholds**: default `spam_llm_confidence_threshold=0.85` (spam/phishing-alert), `spam_llm_newsletter_threshold=0.70` (newsletter). Both exposed in `config_values` for tuning.
- **Retention defaults**: 30 days active + 90 days restored. Purge in hourly housekeeping.
- **Restore JMAP op**: single `Email/set` call with `keywords/$junk=False` + `keywords/nonjunk=True` + `keywords/__spam__=False` + `keywords/newsletter=False`. Do NOT touch `mailboxIds`.
- **Adapter contract**: mails classified as spam/phishing-alert stay in INBOX with `label:__spam__` + `$junk=True` keyword. **The sentinel does NOT call `archive()` for spam decisions** — decoupling makes Restore trivial.
- **Newsletter continuation**: bucket=newsletter labels + sets `nonjunk=True` + returns to pipeline (continues through match_rules etc.). Bucket in {spam, phishing-alert} returns to END.
- **Only phishing-alert emits a mission** (per spec §5.3 Q9 answer). Spam and newsletter are silent.
- **Order of decision cascade**: first-match-wins. No score accumulation across stages.
- **Grey-zone LLM safety**: if LLM `bucket=none` or confidence below threshold, pipeline PASSES THROUGH (bucket=None) — never archives on uncertain LLM output.
- **`declared_by` prefix for phishing-alert missions**: `"sentinel:mail"` (unchanged from SP6).
- **Mono-user unchanged**: `require_owner` on all `/mail-sentinel/spam` endpoints; single owner in `settings.twaky_owner_email`.
- **rspamd status header**: parsed via regex `r'action=([\w\s]+?)(?:;|$)'` (case-insensitive) on the `org.apache.james.rspamd.status` header value.

## Sequencing rationale

Storage → adapter primitive → store → prompt+schema+UseCase → node → routing wiring → REST API → UI → E2E+docs. Eleven tasks in dependency order. T1-T3 build the backend base (migration + adapter method + store). T4-T5 add the LLM machinery. T6-T7 wire the node into the graph. T8 exposes the API. T9-T10 ship the UI. T11 tidies E2E + docs + rollout notes.

## Testing convention

Same as SP6/SP6b:
- Integration tests: `@pytest.mark.integration` + `@pytest.mark.skipif(not _reachable(), reason=...)`. Host shell needs `TWAKY_PG_HOST=172.27.0.33` for tests to actually hit the DB.
- Unit tests: no marker, no external services. Mock `structured_call` for LLM-facing code. `httpx.MockTransport` for JMAP mock.
- API tests: `TestClient(app) + _cookie()` helper from `tests/api/routers/test_skills.py`.
- FE tests: Vitest + MSW for hooks; Playwright for E2E.
- Every task runs its own tests + full gate suite before commit: `uv run ruff check … && uv run ruff format --check … && uv run mypy … && uv run pytest <task tests> -v`.

---

## File Structure

**Created files (new)**

| Path | Purpose |
|---|---|
| `sql/011_init_spam_decision.sh` | psql-heredoc migration: table + config_schema update on `mail` sentinel |
| `tests/sql/test_spam_decision_migration.py` | Static assertions on the migration script |
| `src/twaky/sentinels/mail/store/spam_decisions.py` | `SpamDecision` frozen dataclass + CRUD + stats + purge |
| `tests/sentinels/mail/store/test_spam_decisions.py` | Integration CRUD |
| `src/twaky/sentinels/mail/prompts/spam_check.py` | `spam_check_prompt(state, headers_summary, rspamd_action, owner_email)` |
| `tests/sentinels/mail/prompts/test_spam_check.py` | Prompt shape assertions |
| `tests/sentinels/mail/test_nodes_spam_triage.py` | Node unit tests (11 cases per spec §11.1) |
| `src/twaky/api/routers/mail_sentinel_spam.py` | `GET /mail-sentinel/spam`, `POST /{id}/restore`, `GET /stats` |
| `src/twaky/api/schemas/spam.py` | Pydantic `SpamDecision`, `SpamStats` |
| `tests/api/routers/test_mail_sentinel_spam.py` | 401/404/409/502 matrix |
| `frontend/src/hooks/use-mail-sentinel-spam.ts` | `useSpamDecisions`, `useSpamStats`, `useRestoreSpam` |
| `frontend/src/hooks/use-mail-sentinel-spam.test.tsx` | MSW-mocked hook tests |
| `frontend/src/app/sentinels/mail/recent-spam-tab.tsx` | Client-side Recent Spam tab (6th tab content) |
| `frontend/src/app/sentinels/mail/recent-spam-tab.test.tsx` | Vitest component tests |
| `frontend/tests/e2e/sentinels-mail-recent-spam.spec.ts` | Playwright: toggle + restore |
| `tests/evals/mail/spam/phishing_hard_attachment_dkim_none.yaml` | Eval fixture: LLM → phishing-alert |
| `tests/evals/mail/spam/newsletter_list_unsub.yaml` | Eval fixture: heuristic → newsletter (no LLM) |
| `tests/evals/mail/spam/promo_marketing_greylist.yaml` | Eval fixture: rspamd greylist → LLM grey zone |
| `tests/evals/mail/spam/personal_reply_thread.yaml` | Eval fixture: thread continuation → bucket=none |
| `tests/evals/mail/spam/ham_edge_invoice.yaml` | Eval fixture: FP protection — invoice → bucket=none |
| `tests/integration/test_spam_triage_end_to_end.py` | Real DB + InMemoryMailAdapter, injects $junk email, verifies decision row + label |

**Modified files (existing)**

| Path | Change |
|---|---|
| `src/twaky/sentinels/mail/adapter.py` | Add `set_keyword(email_id, keyword, value) -> None` to `MailAdapter` Protocol + both impls. Add `set_keywords_bulk(email_id, patches: dict[str, bool]) -> None` for atomic multi-keyword op (used by restore) |
| `src/twaky/sentinels/mail/schemas.py` | Add `SpamCheckOutput(bucket, confidence, reason)` pydantic model |
| `src/twaky/sentinels/mail/state.py` | Add `spam_bucket: str \| None` + `spam_decision_id: UUID \| None` to `MailAgentState` TypedDict |
| `src/twaky/sentinels/mail/llm/tiers.py` | Add `UseCase.SPAM_CHECK` + map to `Tier.ECONOMY` in `_MAPPING` |
| `src/twaky/sentinels/mail/nodes.py` | Add `make_spam_triage(ctx)`, helpers `_parse_rspamd_status`, `_header_heuristic_score`, `_terminate` |
| `src/twaky/sentinels/mail/pipeline.py` | Wire node between `load_thread` and `match_rules`; add conditional edge `_route_after_spam_triage` |
| `src/twaky/sentinels/runtime.py` | Extend `_housekeeping()` to call `spam_decisions.purge_active(30)` + `purge_restored(90)` |
| `src/twaky/api/main.py` | `include_router(mail_sentinel_spam.router)` |
| `frontend/src/app/sentinels/mail/page.tsx` | Add 6th `<TabsTrigger value="recent-spam">Recent Spam</TabsTrigger>` + `<TabsContent value="recent-spam"><RecentSpamTab /></TabsContent>` |
| `docs/api/openapi.yaml` | Regenerated via `make openapi` |
| `frontend/src/lib/api-types.d.ts` | Regenerated via `make api-types` |
| `README.md` | New sub-section "Recent Spam tab + restore" under Sentinels · Mail |

---

## Task 1: Migration `sql/011_init_spam_decision.sh` + tests

**Files:** create `sql/011_init_spam_decision.sh` + `tests/sql/test_spam_decision_migration.py`. **Refer to spec §6.1 + §6.2 for the exact table + config_values schema.**

**Interfaces:**
- Consumes: nothing.
- Produces: Postgres table `mail_sentinel_spam_decision` (11 columns, 3 indexes) + `UPDATE sentinel SET config_schema = jsonb_set(...) WHERE name='mail'` extending the schema with 5 new properties.

- [ ] **Step 1:** Model on `sql/008_init_sentinels.sh` (single-quoted heredoc). Include:
  - `CREATE TABLE IF NOT EXISTS public.mail_sentinel_spam_decision` per spec §6.1.
  - 3 indexes: `by_decided_at`, `by_sender`, `active` (partial WHERE restored_at IS NULL).
  - Second heredoc (unquoted) to `UPDATE sentinel SET config_schema = jsonb_set(config_schema, '{properties,spam_filter_enabled}', '{"type":"boolean","default":false}'::jsonb, true) WHERE name='mail'` (and 4 similar `jsonb_set` calls for the other 4 keys: `spam_llm_confidence_threshold`, `spam_llm_newsletter_threshold`, `spam_purge_active_days`, `spam_purge_restored_days`).
- [ ] **Step 2:** `chmod +x sql/011_init_spam_decision.sh`.
- [ ] **Step 3:** Write `tests/sql/test_spam_decision_migration.py` — static assertions:
  - Script exists + executable.
  - Contains `CREATE TABLE IF NOT EXISTS public.mail_sentinel_spam_decision`.
  - Contains `CHECK (bucket IN ('spam','newsletter','phishing-alert'))`.
  - Contains all 6 signal_source enum values in the CHECK.
  - Contains 3 index names.
  - Contains `jsonb_set(config_schema, '{properties,spam_filter_enabled}'` for each of the 5 new config keys.
  - Contains `WHERE name = 'mail'` on the UPDATE.
- [ ] **Step 4:** Run: `uv run pytest tests/sql/test_spam_decision_migration.py -v` → all pass.
- [ ] **Step 5:** Apply on live volume:
  ```bash
  docker exec -i twaky-pg bash /docker-entrypoint-initdb.d/011_init_spam_decision.sh
  docker exec -i twaky-pg psql -U "$POSTGRES_USER" -d twaky -c '\d mail_sentinel_spam_decision'
  docker exec -i twaky-pg psql -U "$POSTGRES_USER" -d twaky -c "SELECT jsonb_pretty(config_schema) FROM sentinel WHERE name='mail'"
  ```
  Expected: table with 11 columns; config_schema has 5 new `spam_*` properties.
- [ ] **Step 6:** Commit `feat(sp6c): init mail_sentinel_spam_decision table + config schema`.

---

## Task 2: Extend `MailAdapter` with `set_keyword` + `set_keywords_bulk`

**Files:** modify `src/twaky/sentinels/mail/adapter.py`. Modify `tests/sentinels/mail/test_adapter.py`.

**Interfaces:**
- Consumes: existing `JmapMailAdapter._call` (SP6b T10 shape with `token_provider`).
- Produces:
  - `MailAdapter.set_keyword(email_id: str, keyword: str, value: bool) -> None` (Protocol + both impls).
  - `MailAdapter.set_keywords_bulk(email_id: str, patches: dict[str, bool]) -> None` — atomic JMAP `Email/set` with multiple `keywords/<name>` patches in one call.

- [ ] **Step 1:** Add both methods to the `MailAdapter` Protocol (single-line `...` stubs with docstrings).
- [ ] **Step 2:** `InMemoryMailAdapter.set_keyword`: `self._keywords.setdefault(email_id, {})[keyword] = value` (add `self._keywords: dict[str, dict[str, bool]] = {}` to `__init__` if not present — reuse the `_labels` pattern).
- [ ] **Step 3:** `InMemoryMailAdapter.set_keywords_bulk`: iterate `patches.items()`, call `self.set_keyword(email_id, k, v)`.
- [ ] **Step 4:** `JmapMailAdapter.set_keyword`: builds JMAP call:
  ```python
  self._call("Email/set", {
      "update": {email_id: {f"keywords/{keyword}": value}},
  })
  ```
- [ ] **Step 5:** `JmapMailAdapter.set_keywords_bulk`: single call:
  ```python
  patch_dict = {f"keywords/{k}": v for k, v in patches.items()}
  self._call("Email/set", {"update": {email_id: patch_dict}})
  ```
- [ ] **Step 6:** Add tests in `tests/sentinels/mail/test_adapter.py`:
  - `test_in_memory_set_keyword_stores`: `adapter.set_keyword("e1", "$junk", True)` → `adapter._keywords["e1"]["$junk"] is True`.
  - `test_in_memory_set_keyword_can_clear`: set True then False → stored False.
  - `test_in_memory_set_keywords_bulk_all_at_once`: `set_keywords_bulk("e1", {"$junk": True, "nonjunk": False})` → both stored.
  - `test_jmap_set_keyword_calls_email_set` (MockTransport): assert single POST with body containing `"update": {"e1": {"keywords/$junk": true}}`.
  - `test_jmap_set_keywords_bulk_single_call` (MockTransport): assert ONE POST with body containing both `keywords/$junk: True` AND `keywords/nonjunk: True` in the same update patch (single JMAP round-trip).
- [ ] **Step 7:** Run: `TWAKY_PG_HOST=172.27.0.33 uv run pytest tests/sentinels/mail/test_adapter.py -v` → all pass (existing + 5 new).
- [ ] **Step 8:** Full gate: `uv run ruff check src/twaky/sentinels/mail/adapter.py tests/sentinels/mail/test_adapter.py && uv run ruff format --check <same> && uv run mypy src/twaky/sentinels/mail/adapter.py`.
- [ ] **Step 9:** Commit `feat(sp6c): MailAdapter.set_keyword + set_keywords_bulk`.

---

## Task 3: `spam_decisions` store

**Files:** create `src/twaky/sentinels/mail/store/spam_decisions.py` + `tests/sentinels/mail/store/test_spam_decisions.py`.

**Interfaces:**
- Consumes: `mail_sentinel_spam_decision` table (T1), `twaky.db.get_pool()`.
- Produces:
  - `@dataclass(frozen=True) class SpamDecision` — 12 fields matching the row (id, email_id, thread_id, sender_email, subject, received_at, bucket, signal_source, score, reason, restored_at, restored_by, decided_at — note: 13 columns total per spec §6.1, but the dataclass fields align 1-to-1).
  - Functions:
    - `insert(*, email_id, thread_id, sender_email, subject, received_at, bucket, signal_source, score, reason) -> UUID`
    - `get(decision_id: UUID) -> SpamDecision | None`
    - `list_recent(*, bucket: str | None = None, limit: int = 50, before: datetime | None = None) -> list[SpamDecision]`
    - `restore(decision_id: UUID, restored_by: str) -> SpamDecision` — raises `AlreadyRestored` if `restored_at is not None`; raises `SpamDecisionNotFound` if missing
    - `stats(days: int = 30) -> dict[str, int]` — keys `spam`, `newsletter`, `phishing_alert`, `restored`, `total_processed`
    - `purge_active(older_than_days: int) -> int`
    - `purge_restored(older_than_days: int) -> int`
  - Exceptions: `SpamDecisionNotFound(Exception)`, `AlreadyRestored(Exception)`.

- [ ] **Step 1:** Write module top-of-file (imports, dataclass, exceptions).
- [ ] **Step 2:** `insert()` — raw psycopg with `RETURNING id` (returns UUID only, not full row, for perf — 1 col vs 13):
  ```python
  with get_pool().connection() as conn, conn.cursor() as cur:
      cur.execute(
          "INSERT INTO mail_sentinel_spam_decision "
          "(email_id, thread_id, sender_email, subject, received_at, "
          " bucket, signal_source, score, reason) "
          "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
          (email_id, thread_id, sender_email, subject, received_at,
           bucket, signal_source, score, reason),
      )
      return cur.fetchone()[0]
  ```
- [ ] **Step 3:** `get()`, `list_recent()` — use `dict_row` factory; `list_recent` builds SQL with WHERE clauses joined by AND, ORDER BY decided_at DESC LIMIT %s.
- [ ] **Step 4:** `restore()`:
  ```sql
  UPDATE mail_sentinel_spam_decision
     SET restored_at = now(), restored_by = %s
   WHERE id = %s AND restored_at IS NULL
   RETURNING *
  ```
  If no row returned: check with a separate `SELECT id, restored_at FROM ... WHERE id=%s` to distinguish "already restored" (409) from "not found" (404); raise the appropriate exception.
- [ ] **Step 5:** `stats()`:
  ```sql
  SELECT
    COUNT(*) FILTER (WHERE bucket = 'spam')            AS spam,
    COUNT(*) FILTER (WHERE bucket = 'newsletter')       AS newsletter,
    COUNT(*) FILTER (WHERE bucket = 'phishing-alert')   AS phishing_alert,
    COUNT(*) FILTER (WHERE restored_at IS NOT NULL)     AS restored,
    COUNT(*)                                            AS total_processed
  FROM mail_sentinel_spam_decision
  WHERE decided_at > now() - %s * INTERVAL '1 day'
  ```
- [ ] **Step 6:** `purge_active` / `purge_restored` — DELETE ... WHERE restored_at IS NULL AND decided_at < now() - %s * INTERVAL '1 day' / restored_at IS NOT NULL AND decided_at < now() - %s * INTERVAL '1 day'. Returns `cur.rowcount`.
- [ ] **Step 7:** Write `tests/sentinels/mail/store/test_spam_decisions.py` (integration marker + skipif + `TWAKY_PG_HOST=172.27.0.33`):
  - `_wipe` fixture DELETE FROM mail_sentinel_spam_decision before + after each test.
  - `test_insert_returns_uuid`: insert → uuid; get(uuid) returns SpamDecision.
  - `test_list_recent_orders_desc_and_limits`: insert 3 rows at different times; list_recent(limit=2) returns 2 most recent in order.
  - `test_list_recent_filters_by_bucket`: insert one spam + one newsletter; list_recent(bucket="spam") returns 1.
  - `test_list_recent_before_cursor`: insert 3 with staggered decided_at; list_recent(before=middle_time, limit=10) returns only rows older than middle_time.
  - `test_restore_success_sets_fields`: insert + restore("me@x") → SpamDecision with restored_at not None and restored_by = "me@x".
  - `test_restore_twice_raises_already_restored`: insert + restore + restore → `AlreadyRestored`.
  - `test_restore_missing_raises_not_found`: restore(random_uuid) → `SpamDecisionNotFound`.
  - `test_stats_aggregation`: insert mix of buckets + one restored; `stats(30)` returns correct counts.
  - `test_purge_active_only_touches_non_restored`: insert 1 active old + 1 restored old + 1 fresh; force decided_at to past via UPDATE; purge_active(30) returns 1; the restored one remains.
  - `test_purge_restored_only_touches_restored`: similar setup; purge_restored(30) returns 1; the non-restored one remains.
- [ ] **Step 8:** Run: `TWAKY_PG_HOST=172.27.0.33 uv run pytest tests/sentinels/mail/store/test_spam_decisions.py -v` → all pass (no skips).
- [ ] **Step 9:** Gate + commit `feat(sp6c): mail_sentinel_spam_decision store`.

---

## Task 4: `SpamCheckOutput` schema + `UseCase.SPAM_CHECK` tier mapping

**Files:** modify `src/twaky/sentinels/mail/schemas.py`, `src/twaky/sentinels/mail/state.py`, `src/twaky/sentinels/mail/llm/tiers.py`. Modify `tests/sentinels/mail/test_schemas.py`, `tests/sentinels/mail/llm/test_tiers.py`.

**Interfaces:**
- Produces:
  - Pydantic `SpamCheckOutput(bucket: Literal["spam","newsletter","phishing-alert","none"], confidence: float 0-1, reason: str max_length=400)`.
  - Added to `MailAgentState` TypedDict: `spam_bucket: str | None`, `spam_decision_id: UUID | None`.
  - Added to `UseCase` enum: `SPAM_CHECK = "spam_check"`.
  - Added to `_MAPPING`: `UseCase.SPAM_CHECK: Tier.ECONOMY`.

- [ ] **Step 1:** Add `SpamCheckOutput` to `schemas.py`. Add to `__all__`.
- [ ] **Step 2:** Extend `MailAgentState` in `state.py` with 2 new optional fields.
- [ ] **Step 3:** Extend `UseCase` enum + `_MAPPING` in `tiers.py`.
- [ ] **Step 4:** Extend `tests/sentinels/mail/test_schemas.py`:
  - `test_spam_check_output_happy`: parses `{"bucket": "spam", "confidence": 0.9, "reason": "why"}`.
  - `test_spam_check_output_rejects_bad_bucket`: `bucket="junk"` → ValidationError.
  - `test_spam_check_output_confidence_bounds`: 1.5 → raises; -0.1 → raises.
  - `test_spam_check_output_reason_max_400`: 401 chars → raises.
- [ ] **Step 5:** Extend `tests/sentinels/mail/llm/test_tiers.py`:
  - `test_spam_check_is_economy_tier`: `tier_for(UseCase.SPAM_CHECK) is Tier.ECONOMY`.
  - Update `test_every_use_case_has_a_tier` to cover the new enum member (it should already iterate all UseCase members, so this needs no code change — verify).
- [ ] **Step 6:** Run: `uv run pytest tests/sentinels/mail/test_schemas.py tests/sentinels/mail/llm/test_tiers.py tests/sentinels/mail/test_state.py -v` → all pass.
- [ ] **Step 7:** Gate + commit `feat(sp6c): SpamCheckOutput schema + SPAM_CHECK UseCase (ECONOMY tier)`.

---

## Task 5: `spam_check_prompt` module

**Files:** create `src/twaky/sentinels/mail/prompts/spam_check.py` + `tests/sentinels/mail/prompts/test_spam_check.py`.

**Interfaces:**
- Consumes: `MailAgentState` (T4), helpers from `prompts/helpers.py` (SP6 T13).
- Produces: `spam_check_prompt(state: dict, headers_summary: str, rspamd_action: str | None, owner_email: str = "") -> str`.

- [ ] **Step 1:** Write the module. Include:
  - Preamble explaining the LLM's role (spam grey-zone classifier, bias to `none`).
  - `<user_info>` block via `user_info_block(owner_email)` (existing helper).
  - `<thread>` block via `email_list_block(state.get("thread") or [])` (existing helper).
  - `<rspamd_verdict>` block with the rspamd action if present, or "no upstream verdict".
  - `<headers_summary>` block with the compact header signals dict rendered as key: value lines.
  - Instruction: "Return `bucket` = spam only if this is clearly bulk marketing OR clearly phishing. Return `phishing-alert` for high-confidence phishing (impersonation, credential harvesting, suspicious attachments). Return `newsletter` for legitimate newsletters the owner subscribed to. Return `none` if uncertain. Confidence in [0,1]; below 0.85 for spam/phishing-alert or 0.70 for newsletter means the runtime will pass through — prefer accuracy over recall."
- [ ] **Step 2:** Write test file:
  - `test_prompt_contains_all_four_bucket_options`: prompt text contains "spam", "newsletter", "phishing-alert", "none".
  - `test_prompt_mentions_owner_email`: with owner_email="alice@x", prompt contains "alice@x".
  - `test_prompt_mentions_rspamd_action_when_given`: with rspamd_action="greylist", prompt contains "greylist".
  - `test_prompt_omits_rspamd_section_when_none`: with rspamd_action=None, prompt contains "no upstream verdict" (or equivalent phrasing).
  - `test_prompt_biases_toward_none`: prompt contains "uncertain" and "prefer accuracy over recall" (or synonyms — assert on keyword presence).
- [ ] **Step 3:** Run: `uv run pytest tests/sentinels/mail/prompts/test_spam_check.py -v` → 5/5.
- [ ] **Step 4:** Gate + commit `feat(sp6c): spam_check LLM prompt`.

---

## Task 6: `make_spam_triage` node

**Files:** modify `src/twaky/sentinels/mail/nodes.py`. Create `tests/sentinels/mail/test_nodes_spam_triage.py`. **Refer to spec §5.2 (signal cascade) + §5.3 (actions per bucket) + §9 (node signature sketch).**

**Interfaces:**
- Consumes: `NodeContext` (SP6 T17), `_sender_email` helper (SP6 T18), T2 `MailAdapter.set_keyword`, T3 `spam_decisions.insert`, T4 `UseCase.SPAM_CHECK` + `SpamCheckOutput`, T5 `spam_check_prompt`, `structured_call` (SP6 T12), `MissionEmitter.emit` (SP6 T4).
- Produces: `make_spam_triage(ctx: NodeContext) -> Callable[[MailAgentState], MailAgentState]`. Returns `{"spam_bucket": None}` (pass-through) OR `{"spam_bucket": <str>, "spam_decision_id": UUID, "actions_applied": [...]}` (terminal for spam/phishing-alert; pipeline-continuing for newsletter).

- [ ] **Step 1:** Append to `nodes.py`:
  - Constants: `_HEURISTIC_NEWSLETTER_MAX_SCORE = 5`, `_HEURISTIC_GREY_MIN_SCORE = 4`.
  - Regex: `_RSPAMD_ACTION_RE = re.compile(r'action=([\w\s]+?)(?:;|$)', re.IGNORECASE)`.
  - Helper `_parse_rspamd_status(headers: list[dict]) -> str | None`:
    ```python
    for h in headers:
        if h.get("name", "").lower() == "org.apache.james.rspamd.status":
            m = _RSPAMD_ACTION_RE.search(h.get("value", ""))
            if m:
                return m.group(1).strip().lower()
    return None
    ```
  - `@dataclass class _HeuristicResult`: `total_score: int`, `newsletter_signal: bool`, `summary: dict[str, Any]`.
  - Helper `_header_heuristic_score(email: dict) -> _HeuristicResult`:
    - Extract headers into a lower-cased dict.
    - `list_unsub_present = "list-unsubscribe" in h and "list-unsubscribe-post" in h`.
    - `dkim_present = "dkim-signature" in h`.
    - Extract `from` domain vs `return-path` domain — mismatch = True/False.
    - `has_attachment = email.get("hasAttachment", False)`.
    - Score: `+2 if list_unsub_present`, `+3 if not dkim_present`, `+3 if return_path_mismatch`, `+2 if has_attachment and not dkim_present`.
    - `newsletter_signal = list_unsub_present` (basic heuristic; can refine later).
    - Return `_HeuristicResult(total_score=score, newsletter_signal=..., summary={...})`.
  - Helper `_terminate(ctx, email, *, bucket, signal, score, reason) -> MailAgentState`: applies adapter side-effects + calls `spam_decisions.insert` + emits mission for phishing-alert + returns the state dict per spec §5.3 table.
- [ ] **Step 2:** Append `make_spam_triage(ctx)` implementing the 5-stage cascade per spec §5.2 (see spec §9 for the full body). Key points:
  - Gate check: `if not cfg.get("spam_filter_enabled", False): return {"spam_bucket": None}` FIRST (spec §5.5).
  - Stage 1 short-circuits before ANY other work.
  - Stage 4 (LLM) called only if `grey_zone = True`.
  - Stage 4 confidence thresholds read from `cfg` with defaults 0.85 / 0.70.
- [ ] **Step 3:** Write `tests/sentinels/mail/test_nodes_spam_triage.py` (integration marker + skipif + `TWAKY_PG_HOST=172.27.0.33`):
  - `_wipe` on mail_sentinel_spam_decision.
  - `_ctx(config_values=None)` helper builds `NodeContext` with MagicMock `base` whose `sentinel_row.config_values` = passed dict or default `{"spam_filter_enabled": True}`, and `base.mission_emitter.emit = MagicMock()`.
  - 11 tests per spec §11.1:
    - `test_disabled_when_config_flag_off` (spam_filter_enabled=False → immediate return, no side effects).
    - `test_stage1_junk_keyword_hard_archive`.
    - `test_stage1_nonjunk_pass_through`.
    - `test_stage2_rspamd_reject_archives`.
    - `test_stage2_rspamd_greylist_triggers_llm`.
    - `test_stage3_newsletter_heuristic_labels`.
    - `test_stage3_dkim_absent_returnpath_mismatch_triggers_grey`.
    - `test_stage4_llm_below_threshold_pass_through`.
    - `test_stage4_llm_phishing_above_threshold_emits_mission`.
    - `test_stage4_llm_newsletter_lower_threshold_accepts`.
    - `test_llm_never_called_when_no_grey_zone` — patches `structured_call` with a MagicMock, feeds Stage 1 $junk hit, asserts `assert_not_called()`.
- [ ] **Step 4:** Run: `TWAKY_PG_HOST=172.27.0.33 uv run pytest tests/sentinels/mail/test_nodes_spam_triage.py -v` → 11/11 pass (no skips).
- [ ] **Step 5:** Full mail suite regression: `TWAKY_PG_HOST=172.27.0.33 uv run pytest tests/sentinels/mail -v`.
- [ ] **Step 6:** Gate + commit `feat(sp6c): spam_triage pipeline node with 5-stage cascade`.

---

## Task 7: Wire `spam_triage` into the pipeline

**Files:** modify `src/twaky/sentinels/mail/pipeline.py`. Modify `tests/sentinels/mail/test_pipeline.py`.

**Interfaces:**
- Consumes: T6 `make_spam_triage`.
- Produces: `build_graph(ctx)` inserts `spam_triage` node between `load_thread` and `match_rules` with a conditional edge that routes terminal buckets to END.

- [ ] **Step 1:** In `build_graph()`:
  - Add `graph.add_node("spam_triage", nodes.make_spam_triage(ctx))`.
  - Replace `graph.add_edge("load_thread", "match_rules")` with `graph.add_edge("load_thread", "spam_triage")`.
  - Define `def _route_after_spam_triage(state: MailAgentState) -> str:` that returns `END` if `state.get("spam_bucket") in {"spam", "phishing-alert"}` else `"match_rules"`.
  - Add `graph.add_conditional_edges("spam_triage", _route_after_spam_triage, {END: END, "match_rules": "match_rules"})`.
- [ ] **Step 2:** Extend `tests/sentinels/mail/test_pipeline.py`:
  - `test_pipeline_spam_bucket_ends_early`: mock adapter with an email whose keywords contain `$junk`; run `process_email(ctx, "e1")`; assert `state.get("spam_bucket") == "spam"` and NO subsequent nodes ran (no draft, no thread_status). Verify by asserting `state.get("status")` is None (thread_status never wrote it) and `state.get("actions_applied")` doesn't contain any match_rules-derived actions.
  - `test_pipeline_newsletter_bucket_continues`: mock adapter with an email that hits the newsletter heuristic; assert `state.get("spam_bucket") == "newsletter"` AND `state.get("status")` is set (thread_status ran) — proves pipeline continued past spam_triage.
  - `test_pipeline_bucket_none_passes_through_unchanged`: mock adapter with plain HAM email; assert `state.get("spam_bucket")` is None and existing pipeline test assertions still hold (drafts, missions, etc.).
- [ ] **Step 3:** Run: `TWAKY_PG_HOST=172.27.0.33 uv run pytest tests/sentinels/mail/test_pipeline.py -v` → all pass (existing + 3 new).
- [ ] **Step 4:** Full mail suite: `TWAKY_PG_HOST=172.27.0.33 uv run pytest tests/sentinels/mail -v` → no regression.
- [ ] **Step 5:** Gate + commit `feat(sp6c): wire spam_triage node into pipeline`.

---

## Task 8: `/mail-sentinel/spam` CRUD API + housekeeping wiring

**Files:** create `src/twaky/api/routers/mail_sentinel_spam.py`, `src/twaky/api/schemas/spam.py`, `tests/api/routers/test_mail_sentinel_spam.py`. Modify `src/twaky/api/main.py`, `src/twaky/sentinels/runtime.py`. **Refer to spec §8 for endpoint shapes.**

**Interfaces:**
- Consumes: T3 `spam_decisions` store, T2 `MailAdapter.set_keywords_bulk`, `require_owner`.
- Produces:
  - `GET /mail-sentinel/spam?bucket=&limit=50&before=<iso>` → `list[SpamDecision]`.
  - `POST /mail-sentinel/spam/{id}/restore` → `SpamDecision` (updated).
  - `GET /mail-sentinel/spam/stats?days=30` → `SpamStats`.
  - Housekeeping cron in `_housekeeping()` calls `spam_decisions.purge_active(30)` + `purge_restored(90)` (reads retention from `mail` sentinel config_values if present, else defaults).

- [ ] **Step 1:** Write `src/twaky/api/schemas/spam.py`:
  ```python
  class SpamDecision(BaseModel):
      model_config = ConfigDict(extra="forbid")
      id: UUID
      email_id: str
      thread_id: str | None
      sender_email: str
      subject: str
      received_at: datetime
      bucket: str
      signal_source: str
      score: float | None
      reason: str | None
      restored_at: datetime | None
      restored_by: str | None
      decided_at: datetime


  class SpamStats(BaseModel):
      model_config = ConfigDict(extra="forbid")
      spam: int
      newsletter: int
      phishing_alert: int
      restored: int
      total_processed: int
  ```
- [ ] **Step 2:** Write router `src/twaky/api/routers/mail_sentinel_spam.py`. Follow SP6b T8 pattern. All endpoints have `Depends(require_owner)`. Restore endpoint:
  ```python
  @router.post("/{decision_id}/restore", response_model=SpamDecision)
  def restore(decision_id: UUID, _email: str = Depends(require_owner)) -> SpamDecision | Response:
      # Fetch to know email_id.
      d = spam_decisions.get(decision_id)
      if d is None:
          return error_response(404, "spam_decision_not_found", "no such decision")
      if d.restored_at is not None:
          return error_response(409, "already_restored", "already restored")
      # Restore in JMAP first (may fail).
      try:
          adapter = _get_mail_adapter()  # helper builds JmapMailAdapter from settings (see below)
          adapter.set_keywords_bulk(d.email_id, {
              "$junk": False, "nonjunk": True,
              "__spam__": False, "newsletter": False,
          })
      except Exception as e:
          return error_response(502, "jmap_restore_failed", str(e))
      # Then update DB.
      try:
          return spam_decisions.restore(decision_id, _email)
      except spam_decisions.AlreadyRestored:
          return error_response(409, "already_restored", "already restored")
  ```
  Helper `_get_mail_adapter()` — builds a `JmapMailAdapter` using the `RefreshManager` from SP6b T10 (`token_provider=get_manager("mail").sync_get_access_token`, `refresh_now=get_manager("mail").sync_force_refresh`). Same shape as `MailSentinel._build_adapter` from SP6 T24 refactored in SP6b T10.
- [ ] **Step 3:** Register router in `main.py`.
- [ ] **Step 4:** Extend `_housekeeping()` in `runtime.py`:
  ```python
  try:
      from twaky.sentinels.mail.store import spam_decisions
      # Read retention from mail sentinel config_values if present.
      cfg = registry.get("mail")
      active_days = int((cfg.config_values if cfg else {}).get("spam_purge_active_days", 30))
      restored_days = int((cfg.config_values if cfg else {}).get("spam_purge_restored_days", 90))
      active_purged = await asyncio.to_thread(spam_decisions.purge_active, active_days)
      restored_purged = await asyncio.to_thread(spam_decisions.purge_restored, restored_days)
      if active_purged:
          log.info("housekeeping: purged %d active mail_sentinel_spam_decision rows", active_purged)
      if restored_purged:
          log.info("housekeeping: purged %d restored mail_sentinel_spam_decision rows", restored_purged)
  except Exception:
      log.exception("housekeeping: spam_decisions purge failed")
  ```
  Import inside try so runtime still boots if the module has a bug — defense in depth mirroring SP6b T15 fix.
- [ ] **Step 5:** Write tests in `tests/api/routers/test_mail_sentinel_spam.py`:
  - `_env` autouse fixture (`API_SESSION_SECRET`, `TWAKY_OWNER_EMAIL`, `MODEL`, `TWAKY_SECRET_KEY`).
  - `_wipe` on mail_sentinel_spam_decision.
  - 10 tests:
    - `test_list_401_unauthenticated`.
    - `test_list_returns_empty_when_no_rows`.
    - `test_list_returns_paginated`: seed 5 rows via `spam_decisions.insert`; GET limit=3 → 3 rows most recent.
    - `test_list_filters_by_bucket`: seed 1 spam + 1 newsletter; GET ?bucket=spam → 1 row.
    - `test_restore_401_unauthenticated`.
    - `test_restore_404_missing`.
    - `test_restore_409_already_restored`: seed + restore + POST restore → 409.
    - `test_restore_502_when_jmap_fails`: monkeypatch `_get_mail_adapter()` returning MagicMock whose `set_keywords_bulk` raises; POST → 502 with code `jmap_restore_failed`.
    - `test_restore_happy_path_updates_and_returns`: monkeypatch adapter as MagicMock; POST → 200 with `restored_at` set + `restored_by = "alice@x"`.
    - `test_stats_401_unauthenticated`.
    - `test_stats_returns_aggregation`: seed 2 spam + 1 newsletter + 1 restored spam; GET stats?days=30 → `{spam:2, newsletter:1, phishing_alert:0, restored:1, total_processed:3}`.
- [ ] **Step 6:** Regenerate OpenAPI: `make openapi`.
- [ ] **Step 7:** Run: `TWAKY_PG_HOST=172.27.0.33 uv run pytest tests/api/routers/test_mail_sentinel_spam.py -v` → all pass (no skips).
- [ ] **Step 8:** Full API regression: `TWAKY_PG_HOST=172.27.0.33 uv run pytest tests/api tests/sentinels/mail -v` → no breakage.
- [ ] **Step 9:** Gate + commit `feat(sp6c): /mail-sentinel/spam CRUD API + housekeeping purge wiring`.

---

## Task 9: Frontend hooks + Recent Spam tab component

**Files:** create `frontend/src/hooks/use-mail-sentinel-spam.ts`, `.test.tsx`, `frontend/src/app/sentinels/mail/recent-spam-tab.tsx`, `.test.tsx`. **Refer to spec §7 for UI shape.**

**Interfaces:**
- Consumes: T8 REST API + existing SP6 T25 `useSentinel("mail")` + `usePatchSentinel("mail")` for toggle read/write.
- Produces:
  - Hooks: `useSpamDecisions({bucket?, limit?, before?})`, `useSpamStats(days=30)`, `useRestoreSpam()`.
  - `<RecentSpamTab />` client component (mirrors SP6b T11 Auth tab pattern).

- [ ] **Step 1:** Regenerate types: `cd frontend && make api-types` — `SpamDecision` and `SpamStats` schemas must land in `frontend/src/lib/api-types.d.ts`.
- [ ] **Step 2:** Write `use-mail-sentinel-spam.ts` with 3 hooks matching SP6 T27 shape (openapi-fetch client + TanStack Query v5 v5). Query key: `['mail-spam-decisions', {bucket, before}]`. Mutation invalidates both `['mail-spam-decisions']` and `['mail-spam-stats']`.
- [ ] **Step 3:** Write `use-mail-sentinel-spam.test.tsx` — 6 MSW tests:
  - `test_list_returns_shape`.
  - `test_list_error_propagates`.
  - `test_stats_returns_shape`.
  - `test_restore_success_invalidates_lists`.
  - `test_restore_409_already_propagates`.
  - `test_restore_502_jmap_propagates`.
- [ ] **Step 4:** Write `recent-spam-tab.tsx` (client component):
  - Read spam_filter_enabled via `useSentinel("mail")` (SP6 T27 hook); write via `usePatchSentinel("mail")` with `{config_values: {...current, spam_filter_enabled: newValue}}`.
  - Header: shadcn `Switch` + label "Spam filter" + stats line ("Last 30 days · N archived · M restored") sourced from `useSpamStats(30)`.
  - Table: shadcn `Table` with 6 columns (Bucket icon, From, Subject, Received relative, Signal, Actions). Uses `useSpamDecisions({limit: 50, before: cursorState})` for data. Pagination via next/prev buttons updating `cursorState`.
  - Restore button: `AlertDialog` for confirm → `useRestoreSpam().mutate(decision.id)` on confirm.
  - Empty states: "Spam filter is off" if `!spam_filter_enabled`, else "Spam filter is on, no decisions yet" if list empty.
- [ ] **Step 5:** Write `recent-spam-tab.test.tsx` — 5 Vitest tests:
  - `test_off_state_renders_toggle_and_disabled_message`.
  - `test_on_state_renders_stats_and_empty_message_when_no_rows`.
  - `test_on_state_renders_table_rows`.
  - `test_restore_click_opens_dialog`.
  - `test_restore_confirm_calls_mutation`.
- [ ] **Step 6:** Run: `cd frontend && npm run lint && npm run typecheck && npm test -- --run` → all pass. Report explicit total.
- [ ] **Step 7:** Commit `feat(sp6c): frontend Recent Spam hooks + tab component`.

---

## Task 10: Wire Recent Spam as 6th tab on `/sentinels/mail`

**Files:** modify `frontend/src/app/sentinels/mail/page.tsx`.

**Interfaces:**
- Consumes: T9 `<RecentSpamTab />`.
- Produces: 6th tab visible + functional in the UI.

- [ ] **Step 1:** Import `<RecentSpamTab />` from `./recent-spam-tab`.
- [ ] **Step 2:** After the SP6b Auth tab, add:
  ```tsx
  <TabsTrigger value="recent-spam">Recent Spam</TabsTrigger>
  ```
  and inside the `<Tabs>` body:
  ```tsx
  <TabsContent value="recent-spam" className="mt-4">
    <RecentSpamTab />
  </TabsContent>
  ```
- [ ] **Step 3:** Run: `cd frontend && npm run lint && npm run typecheck && npm test -- --run && npm run build` → all green; `/sentinels/mail` still generated as static.
- [ ] **Step 4:** Commit `feat(sp6c): wire Recent Spam tab into /sentinels/mail`.

---

## Task 11: Eval fixtures + Playwright E2E + docs + rollout notes

**Files:** create 5 YAML fixtures under `tests/evals/mail/spam/`, `tests/integration/test_spam_triage_end_to_end.py`, `frontend/tests/e2e/sentinels-mail-recent-spam.spec.ts`. Modify `README.md`. Extend `tests/evals/mail/test_evals.py` (SP6 T30 harness) to consume the new fixtures.

**Interfaces:**
- Consumes: everything above.
- Produces:
  - 5 YAML fixtures per spec §11.4.
  - 1 backend integration test (real DB + InMemoryMailAdapter).
  - 1 Playwright spec (toggle + restore flow).
  - README section documenting the toggle + restore UI + retention.

- [ ] **Step 1:** Write 5 YAML fixtures:
  - `phishing_hard_attachment_dkim_none.yaml` — email with no dkim + attachment + return-path mismatch + subject "Verify your bank account"; expected `bucket=phishing-alert`, mission emitted.
  - `newsletter_list_unsub.yaml` — email with `list-unsubscribe` + `list-unsubscribe-post` + from `news@newsletter.example.com`; expected `bucket=newsletter` via heuristic (no LLM).
  - `promo_marketing_greylist.yaml` — email with `org.apache.james.rspamd.status: greylist`; expected LLM called; `bucket in {spam, newsletter, none}` (accept any — LLM-dependent).
  - `personal_reply_thread.yaml` — thread with 3 messages (owner ↔ contact); expected `bucket=none` always.
  - `ham_edge_invoice.yaml` — automated invoice from known domain with valid dkim + no attachment; expected `bucket=none` (FP protection).

  YAML shape (extend the SP6 T30 fixture shape):
  ```yaml
  name: <str>
  # No rule this time — spam_triage runs BEFORE match_rules.
  config_values:
    spam_filter_enabled: true
  email:
    id: <str>
    threadId: <str> | null
    from: [{email: <str>}]
    to: [{email: <str>}]
    subject: <str>
    preview: <str>
    receivedAt: <ISO>
    keywords: {}          # or {"$junk": true} / {"nonjunk": true}
    headers:              # list of {name, value}
      - name: "..."
        value: "..."
    hasAttachment: bool
  fake_llm_outputs:       # only for grey-zone cases; keyed by UseCase
    spam_check: {bucket: "...", confidence: 0.NN, reason: "..."}
  expected:
    bucket: spam | newsletter | phishing-alert | none
    llm_called: bool
    mission_emitted: bool
  ```
- [ ] **Step 2:** Extend `tests/evals/mail/test_evals.py` to load fixtures from `tests/evals/mail/spam/`, seed `mail` sentinel's config_values with `spam_filter_enabled: true` for the run, patch `structured_call` with the per-fixture `fake_llm_outputs` dispatcher, run `process_email(ctx, email_id)`, assert `state.get("spam_bucket") == expected.bucket`, assert LLM call count matches `expected.llm_called`, and assert mission_emitter called-count matches `expected.mission_emitted`.
- [ ] **Step 3:** Run: `TWAKY_PG_HOST=172.27.0.33 uv run pytest tests/evals -v` → passes 3 SP6 fixtures + 5 SP6c fixtures = 8/8.
- [ ] **Step 4:** Write `tests/integration/test_spam_triage_end_to_end.py`:
  - Fixture seeds `mail` sentinel row's config_values with `spam_filter_enabled: true` + `spam_llm_confidence_threshold: 0.85`; cleanup restores.
  - Test 1: `test_junk_keyword_produces_spam_decision_row_and_labels`: inject email with `keywords: {$junk: true}` into InMemoryMailAdapter; run pipeline; assert (a) row inserted in `mail_sentinel_spam_decision` with `signal_source='rspamd_junk_keyword'`, (b) `adapter._keywords[email_id]["__spam__"]` was set, (c) `adapter._keywords[email_id]["$junk"]` set to True.
  - Test 2: `test_nonjunk_keyword_leaves_pipeline_intact`: inject email with `keywords: {nonjunk: true}`; verify NO spam_decision row + pipeline reaches `thread_status`.
- [ ] **Step 5:** Write Playwright spec `frontend/tests/e2e/sentinels-mail-recent-spam.spec.ts`:
  - Navigate to `/sentinels/mail?tab=recent-spam`.
  - Preconditions setup via API (using session cookie): PATCH `/mail-sentinel` to set `spam_filter_enabled: true`; insert 1 spam_decision row via a test helper endpoint OR directly via psql (choose whichever the SP6/SP6b Playwright suite uses).
  - Verify toggle is ON.
  - Verify row visible in the table with expected bucket + sender + subject.
  - Click Restore → confirm dialog → confirm.
  - Verify row shows "Restored on <date>" text; refetch confirms `restored_at` in the API response.
- [ ] **Step 6:** Modify `README.md` — under the existing "Sentinels · Mail" section, add:
  ```markdown
  ### Recent Spam tab + Restore

  The mail sentinel can optionally short-circuit spam BEFORE the full LLM
  pipeline runs. Enable via /sentinels/mail#recent-spam:

  1. Toggle "Spam filter" ON.
  2. From now on, incoming inbox mails get classified into one of four
     buckets:
     - **spam**: silently labeled + $junk keyword set; stays in INBOX but
       your existing Twake Mail filters can move it to Junk.
     - **newsletter**: labeled `newsletter` + `nonjunk` keyword set;
       stays in INBOX; you can create rules that match `label:newsletter`.
     - **phishing-alert**: labeled + `$junk` + a mission is emitted for
       your review under /missions.
     - **none**: pass-through (unchanged pipeline).
  3. Review decisions in the Recent Spam tab; click Restore on any row
     to clear the spam keywords (mail reappears clean in INBOX).

  Retention: 30 days for active decisions, 90 days for restored (audit
  trail). Owner can tune thresholds via PATCH /sentinels/mail with
  `config_values: {spam_llm_confidence_threshold: 0.90, ...}`.
  ```
- [ ] **Step 7:** Backend integration: `TWAKY_PG_HOST=172.27.0.33 uv run pytest tests/integration/test_spam_triage_end_to_end.py -v` → 2/2 PASS.
- [ ] **Step 8:** Full regression: `TWAKY_PG_HOST=172.27.0.33 uv run pytest tests/sentinels tests/oauth tests/api tests/sql tests/cli tests/missions tests/evals tests/integration -v` → no regressions.
- [ ] **Step 9:** Playwright attempt: `cd frontend && npm run test:e2e -- --grep 'recent-spam'`. If pre-existing container issue blocks it (same as SP6 T29 / SP6b T12), document that specs are written + lint-clean but execution deferred.
- [ ] **Step 10:** Gate: `uv run ruff check … && uv run ruff format --check … && uv run mypy …` on all touched paths.
- [ ] **Step 11:** Commit `test(sp6c): eval fixtures + E2E specs + README + integration test`.

---

## Wrap-up

After T11 lands + CI green:

1. **Full sanity suite**: `TWAKY_PG_HOST=172.27.0.33 RABBITMQ_URL=... uv run pytest tests/sentinels tests/oauth tests/api tests/sql tests/cli tests/missions tests/evals tests/integration -v` — no regressions vs main (except the known pre-existing flakes documented in SP6/SP6b ledgers).
2. **Deploy migration**: `docker exec -i twaky-pg bash /docker-entrypoint-initdb.d/011_init_spam_decision.sh`.
3. **Restart**: `docker compose restart twaky-api twaky-sentinel`. Container boots with `spam_filter_enabled=false` — no behavioral change until owner opts in.
4. **Manual smoke** per spec §13: owner opens `/sentinels/mail#recent-spam`, toggles ON, waits for a spam inbox arrival, verifies the row appears + Restore works.
5. Invoke `superpowers:finishing-a-development-branch` to decide merge vs PR.

## Self-review notes (for the plan writer)

- **Spec coverage**: every spec §1-15 section maps to a task. §1 Goal → T6+T7; §2 Scope → whole plan; §3 out-of-scope → not covered (correct); §4 Q4 empirical → drives T6 helper `_parse_rspamd_status` design; §5 Architecture → T6+T7; §6.1 Table → T1; §6.2 Config values → T1; §6.3 UseCase → T4; §7 UI → T9+T10; §8 REST → T8; §9 Node signature → T6; §10 Store → T3; §11 Testing → each task's tests + T11 evals; §12 Observability → included in T6 via trace + T8 stats endpoint; §13 Rollout → wrap-up; §14 File impact → File Structure section above; §15 Constraints → Global Constraints section verbatim.
- **Type consistency**: `SpamDecision` dataclass in T3 matches `SpamDecision` pydantic model in T8 (same field names + types). `MailAdapter.set_keyword` in T2 matches usage in T6 (`_terminate` helper) and T8 (`set_keywords_bulk` on restore path). `UseCase.SPAM_CHECK` in T4 matches consumption in T5 prompt + T6 node call. `SpamCheckOutput` fields in T4 match parsing in T6 node.
- **Regression guards**: T3 test `test_restore_twice_raises_already_restored` prevents accidental double-restore path. T6 test `test_llm_never_called_when_no_grey_zone` prevents accidental LLM cost regression. T8 test `test_restore_502_when_jmap_fails` verifies partial-failure semantics (DB stays consistent when JMAP fails).
- **Cross-task ordering**: T1 (migration) blocks everything (table needs to exist for T3+T8+T11 tests). T2 (adapter) blocks T6 (node needs `set_keyword`) + T8 (restore needs `set_keywords_bulk`). T3 (store) blocks T6+T8+T11. T4 (schema+tier) blocks T5+T6. T5 (prompt) blocks T6. T6 (node) blocks T7+T11 integration. T7 (pipeline wiring) blocks T11 integration. T8 (API) blocks T9 (hooks). T9 blocks T10 (page wiring). T10+T8 block T11 (E2E).
- **Deferred (SP6d) items called out in spec** — not in this plan: approach B (learned-pattern sender filter), approach D (SearXNG tie-breaker), owner-feedback ingestion via JMAP Email/changes.
- **Compatibility with existing SP6/SP6b tests**: T6's spam_triage node returns `{"spam_bucket": None}` when disabled, which is the default. Pipeline test additions in T7 verify the pass-through path preserves existing behavior. Existing `test_pipeline.py` tests (SP6 T24) that don't set `spam_filter_enabled: true` will hit the disabled path, get `{"spam_bucket": None}`, and behave identically to today.
