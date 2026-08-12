# SP6d overnight polish — recap (2026-08-12)

Autonomous `/goal` run against branch `sp6d` via
superpowers:subagent-driven-development. 11 commits, 5 tasks, 3
fix rounds. No changes to `.env`, `deploy/`, `dev.twake.ai/`,
`docker-compose.yml`, or the `twaky-pg` volume. No `docker
restart`, no `--no-verify`, no destructive git ops. Migration
`sql/013_*.sh` committed but NOT executed against live DB —
operator will run it later.

## Delivered

| # | Livrable | Commit(s) | Fix rounds |
|---|---|---|---|
| T1 | Schema + adapter capture (env headers, origin mailbox) | `280e52f`, `517f948`, `afec0c2`, `56a72ad` | 1 |
| T2 | POST /mail-sentinel/rules/propose + shadow simulation | `5f1c529`, `9cd2a10` | 1 (schema realignment) |
| T3 | UI Rules editor with mandatory Propose/Apply | `2af17ca` | 0 |
| T4 | Recent Spam Origin column (opt-in ?with_provenance=1) | `7fd33ab`, `a959098`, `e82169b` | 1 |
| T5 | Docs (README, cookbook, ADR sp6d-decisions) | `f305019` | 0 |

Final whole-branch review by opus: **Ready to merge**. Zero
Critical, zero Important. Deferred minors are all style /
degenerate-edge items, none load-bearing.

## Spec correction landed inline

The initial SP6d spec incorrectly specified a `from_contains` /
`subject_contains` predicate schema for the propose endpoint. The
actual `rules_store` uses `conditions: list[{field, operator,
value}]` + top-level `combinator`. T2 fix-round-1 realigned the
endpoint AND the spec was updated in
`docs/superpowers/specs/2026-08-12-sp6d-draft.md` (search for
"Correction (2026-08-12, T2 review adjudication)").

## What the operator should do next

1. **Apply the migration** — `sql/013_add_spam_decision_provenance.sh`.
   Idempotent, non-blocking (no DEFAULT rewrite). Without it:
   - Origin column shows "—" for every row.
   - Propose simulation for `header:*` rules always returns
     `matched_count: 0` and `simulation_partial: true`.
   - `_terminate` silently drops provenance capture (fallback
     path in the store).

2. **Decide merge** — the /goal explicitly asked NOT to merge
   overnight. Options via
   `superpowers:finishing-a-development-branch`:
   - Merge locally to main.
   - Push and create a PR.
   - Keep as-is.

3. **Skim the ADR** — `docs/superpowers/adr/2026-08-12-sp6d-decisions.md`
   for the three decisions (simulation mandatory, schema unity,
   opt-in provenance).

4. **Test manually** — see the testing brief section below.

## Testing brief

After migration is applied:

### T2 propose endpoint

```bash
COOKIE=<twaky_session>
curl -s -X POST -H "Cookie: twaky_session=$COOKIE" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "test-propose",
       "priority": 45,
       "enabled": true,
       "conditions": [{"field":"from","operator":"contains","value":"@newsletter"}],
       "combinator": "all",
       "actions": ["label:test"],
       "window": {"kind":"recent","count":200}
     }' \
     https://twaky.${BASE_DOMAIN}/mail-sentinel/rules/propose | jq
```

Expected: JSON with `matched_count`, `would_shadow_count`,
`matched_examples`, `would_shadow`, `simulation_partial`.

### T3 UI Rules editor

- Navigate to `/sentinels/mail`, click **+ New rule**.
- Enter a rule matching some historical sender.
- Click **Preview matches** → panel shows summary + matches.
- Try clicking Apply — disabled.
- Tick "I have reviewed the matches" → Apply enables.
- Edit the JSON → panel collapses, Apply disappears.
- Click Preview again → new simulation.
- Apply.
- Verify the rule appears in the Rules tab.

### T4 Recent Spam Origin column

- Wait for the next spam-classified email (or trigger a replay).
- Recent Spam tab shows a new "Origin" column between "Received"
  and "Signal".
- For post-migration decisions: subdued badge with role
  (`inbox`, `newsletter`, etc.).
- For pre-migration decisions: em-dash with tooltip.
- For unknown roles: truncated mailbox id in `<code>`.

## Per-commit gate

Every commit passed:
- `uv run ruff check <touched files>`
- `uv run ruff format --check <touched files>`
- `uv run mypy <touched files>` (except one pre-existing
  nodes.py:671 fix landed as bonus in T2 fix-round-1)
- `uv run pytest <touched tests>` (self-skipping on live-DB /
  JMAP as appropriate)
- `git status` clean

## Deferred to SP6e / SP6f

All from the ledger, none load-bearing:
- `_reset_column_cache_for_tests` in `__all__` (style)
- `resolve_mailbox_role_by_id` empty-roles edge case
  (degenerate)
- `ProposeWindow.count` `le=2000` on pydantic (redundant with
  server-side guard)
- `restore` endpoint suppresses provenance (spec-consistent,
  worth clarifying)
- `propose-results.tsx` warning requires both `partial` and
  `reason` truthy (safe today, invariant implicit)
