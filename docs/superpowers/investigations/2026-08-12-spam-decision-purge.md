# Spam decision rows disappearing on twaky-sentinel recreate — 2026-08-12

## Symptom

During the 2026-08-11 SP6c UAT session, the live `mail_sentinel_spam_decision`
table was observed to be **empty** immediately after a `docker rm && docker
compose up -d twaky-sentinel` cycle. Six newsletter decisions the daemon
had classified during the previous hour had vanished — no purge log line,
no CASCADE, no obvious cause.

## Investigation

### Ruled out

- **Housekeeping cron**: `_housekeeping()` in `src/twaky/sentinels/runtime.py`
  sleeps 3600s before its first pass — recreate → immediate purge would
  need the sleep to elapse, which it did not (rows disappeared within
  seconds of restart).
- **Migration rollback**: `sql/011_init_spam_decision.sh` uses
  `CREATE TABLE IF NOT EXISTS` and does not `DROP` or `TRUNCATE`.
- **FK CASCADE**: the only FK on `mail_sentinel_spam_decision` is
  `email_id` → `TEXT` (no FK constraint), and the only referenced-by
  column is `mail_sentinel_run.event_ref` in the opposite direction with
  no cascade defined.
- **`docker rm` cascade**: `twaky-pg` was not touched by the recreate
  (only `twaky-sentinel` was `rm`'d), so the DB volume is intact.

### Root cause — `_wipe()` fixtures over a live DSN

`git grep 'DELETE FROM mail_sentinel_spam_decision'` returns **six** hits
(two in the store's `purge_active` / `purge_restored`, four in test
`_wipe()` autouse fixtures):

```
tests/sentinels/mail/test_nodes_spam_triage.py     — pytest.mark.integration
tests/sentinels/mail/store/test_spam_decisions.py  — pytest.mark.integration
tests/api/routers/test_mail_sentinel_spam.py       — pytest.mark.integration
tests/api/routers/test_mail_sentinel_runs.py       — pytest.mark.integration (added today)
```

Each of these declares:

```python
@pytest.fixture(autouse=True)
def _wipe():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_spam_decision;")
    yield
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mail_sentinel_spam_decision;")
```

And `_dsn()` resolves to `settings.pg_dsn` when `TWAKY_TEST_DSN` is not
set. `settings.pg_dsn` reads `TWAKY_PG_HOST` — the default in
`.env` is `TWAKY_PG_HOST=twaky-pg` (compose-internal DNS), but developers
running tests from athena override this to `TWAKY_PG_HOST=172.27.0.33`,
which **IS the live twaky-pg** container. There is no separate test DB.

Consequently, every `TWAKY_PG_HOST=172.27.0.33 uv run pytest tests/…`
invocation that includes any of the four files above **wipes the live
production spam_decision table** before and after each test.

The 2026-08-11 disappearance was almost certainly the shell session's
final `uv run pytest tests/sentinels/mail/test_adapter.py …` after the
save_draft / restore fixes — one of the imported test modules had a
top-level `_wipe` fixture that pytest collected and ran against the
live DB.

## Blast radius

- `mail_sentinel_spam_decision` — 100% of live rows wiped
- `sentinel_run` — the newly-added `test_mail_sentinel_runs.py::_truncate`
  now also wipes live `sentinel_run` rows for `sentinel_name = 'mail'`
- Any other tables the future integration tests decide to `_wipe()`

Silent — no warning, no confirmation, no dry-run. A developer running the
suite from their laptop against the shared dev DB destroys other people's
observed state instantly.

## Fix

Two-layer safety, both in this session:

1. **Environment gate**: every `_wipe()` fixture now consults
   `TWAKY_ALLOW_DESTRUCTIVE_TESTS` and skips the DELETE when it is not
   truthy. Developers opt in explicitly with
   `TWAKY_ALLOW_DESTRUCTIVE_TESTS=1 TWAKY_PG_HOST=172.27.0.33 uv run pytest …`
   — a wilful choice, no accident.

2. **Doc** — this file, referenced from `tests/api/routers/README.md`
   (todo) and every wipe fixture's docstring.

The right long-term fix is a separate `twaky_test` database provisioned
by CI (or a psql `BEGIN … ROLLBACK` wrapper for pytest-postgresql-style
isolation). Both are out of scope for this overnight goal — filed to
SP6d.

## What was lost

The six 2026-08-11 newsletter decisions:

| Sender | Subject |
|---|---|
| info@adflex24.com | Tot 7200 euro aan extra opbrengsten per jaar? |
| hello@unbiasedheadlines.com | UBH Morning Briefing — Tuesday, August 11 |
| notifications@github.com | Re: [linagora/tmail-flutter] TF-4744: Inherit typo… |
| notifications@github.com | Re: [linagora/tmail-flutter] TF-4744: Inherit typo… |
| notifications@github.com | [linagora/tmail-flutter] Fix TwakeInter font not l… |
| malyutina@cnews.ru | Ваша регистрация на CNews Forum 2026 |

The underlying JMAP mails are still in the user's mailbox — the sentinel
just lost the row that would have let them use the Restore UI. Cosmetic
loss.
