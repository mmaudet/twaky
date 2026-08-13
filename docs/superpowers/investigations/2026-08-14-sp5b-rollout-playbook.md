# SP5b Rollout Playbook

## Prerequisites

- All 14 SP5b tasks merged to `main`.
- `mail_sentinel_observer_enabled=False` in `.env` (default).
- Migration `012_init_write_side.sh` applied to twaky-pg.

## Rollout Steps

### 1. Deploy with flag OFF

Verify no regression on ingest path.

```bash
docker compose build twaky-sentinel twaky-api twaky-frontend
docker compose up -d --force-recreate --no-deps twaky-sentinel twaky-api twaky-frontend
```

Watch for 30 minutes:

```bash
docker exec twaky-pg psql -U twaky -d twaky -c \
  "SELECT count(*), max(started_at) FROM sentinel_run WHERE sentinel_name='mail' AND started_at > now() - INTERVAL '30 min';"
```

**Expected outcome:** Ingest should keep processing at the usual rate (no slowdown, 
no `ERROR` outcomes in logs).

### 2. Enable observer flag on athena for 48 h

Edit `/home/mmaudet/deploy/kickstart-maudet-cloud/.env` (or wherever the sentinel 
container reads env), add:

```
MAIL_SENTINEL_OBSERVER_ENABLED=true
```

Then:

```bash
docker compose up -d --force-recreate --no-deps twaky-sentinel
```

### 3. Monitor at 6 h / 24 h / 48 h

Run these queries on `twaky-pg`:

```sql
-- Count auto-learned memories by source
SELECT count(*), source FROM mail_sentinel_memory
WHERE source LIKE 'auto_%' GROUP BY source;

-- Count high-confidence active patterns
SELECT count(*) FROM mail_sentinel_learned_pattern
WHERE evidence_count >= 3 AND confidence >= 0.9;

-- Extraction outcomes in last 24h
SELECT extraction_outcome, count(*) FROM mail_sentinel_observation
WHERE observed_at > now() - INTERVAL '24h' GROUP BY 1;

-- Check for sentinel errors
SELECT count(*) FROM sentinel_run
WHERE sentinel_name='mail' AND outcome='error' AND started_at > now() - INTERVAL '24h';

-- Compare ingest processed count
SELECT count(*) FROM sentinel_run
WHERE sentinel_name='mail' AND started_at > now() - INTERVAL '48h';
```

**Expected outcomes:**

- `auto_*` memory count grows over 48h as user acts on mails.
- `learned_pattern` active count (evidence >= 3, confidence >= 0.9) grows if 
  3+ consistent sender actions occurred.
- `extraction_outcome` shows mostly "success" with near-zero "error" counts.
- `error` outcomes stay near 0 throughout the 48h window.
- Ingest processed count unchanged from the 48h before flag-on (no slowdown).

### 4. If green after 48 h, flip the default to `True` in code

Update `src/twaky/config.py`:

```python
mail_sentinel_observer_enabled: bool = Field(default=True)
```

Then:

```bash
git add src/twaky/config.py
git commit -m "chore(sp5b): enable observer by default post-rollout"
git push origin main
```

Wait for CI/CD to build and deploy.

### 5. If red, flip flag OFF

```bash
sed -i 's/MAIL_SENTINEL_OBSERVER_ENABLED=true/MAIL_SENTINEL_OBSERVER_ENABLED=false/' .env
docker compose up -d --force-recreate --no-deps twaky-sentinel
```

**No code rollback needed.** Investigate via:

- Langfuse traces (search for extraction errors in the trace dashboard).
- Observation error rows:
  ```sql
  SELECT error_repr FROM mail_sentinel_observation 
  WHERE extraction_outcome='error' 
  ORDER BY observed_at DESC 
  LIMIT 20;
  ```
- Sentinel run logs:
  ```bash
  docker logs twaky-sentinel --tail=500 | grep -i error
  ```

## Success Criteria (aligned with spec §2)

After 48 h of flag-on, all of the following must hold:

- [ ] **Memory growth**: `SELECT count(*) FROM mail_sentinel_memory WHERE source LIKE 'auto_%'` > 0.
- [ ] **Pattern activation**: At least one active learned pattern: 
  `SELECT count(*) FROM mail_sentinel_learned_pattern WHERE evidence_count >= 3 AND confidence >= 0.9` > 0.
- [ ] **Ingest stability**: Error rate unchanged from 48 h prior (no new sentinel errors).
- [ ] **Manual smoke test**: 
  1. Draft a reply to any email via the UI.
  2. Edit it substantially (e.g., replace greeting, add/remove paragraphs).
  3. Send it.
  4. Within 2 minutes, check `/sentinels/mail` → Memories tab.
  5. A new `auto_diff` memory should appear, tagged with the sender email.

## Rollback Procedure

If issues arise at any point:

1. **Disable observer**: Flip `MAIL_SENTINEL_OBSERVER_ENABLED=false` in `.env` 
   and redeploy sentinel.
2. **Monitor recovery**: Watch ingest rate and error count drop back to baseline 
   within 10 minutes.
3. **Investigate**: Use queries and logs above to identify the root cause 
   (e.g., LLM timeout, database constraint violation, malformed extraction output).
4. **Fix & retry**: Do not flip default to `True` in code until the cause is resolved 
   and verified in a staging rollout.

## Notes

- **No data loss**: Observer writes to separate tables (`mail_sentinel_memory`, 
  `mail_sentinel_learned_pattern`, `mail_sentinel_observation`). Original ingest 
  pipeline (`mail_sentinel_rule`) is unaffected.
- **Performance**: Observer runs as a separate async task per `observer_tick` 
  (default every 5 min). Does not block ingest.
- **Langfuse tracing**: All extraction calls are traced via `structured_call`. 
  Review traces if extraction_outcome='error'.
