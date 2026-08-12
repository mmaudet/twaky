# SP6e overnight polish — recap (2026-08-12)

Autonomous continuation of the /goal loop after SP6d shipped.
User was device-constrained ("pas mon laptop"), so the work
stayed pure code with pytest as the sole validation surface.

## Delivered — 13 commits on branch sp6e

| # | Livrable | Commits |
|---|---|---|
| — | Spec | `eacf8bc` |
| A | SSE broker cleanup (flake #4 root cause) | `ed82cb6`, `b3245a8`, `048fabd` |
| B | Daemon `AWAITING_USER → DONE` fallthrough guard | `ae50ed1` |
| C | `@pytest.mark.jmap_live` marker + `jmap_live_folder` fixture | `11eeccc`, `6ae1c3d` |
| D | 5-item deferred-minor bundle (per-commit) | `d48c8d3`, `b455e9d`, `9976214`, `c5dc846`, `ae634a1` |
| — | Final-review fix wave | `655ee9b` |

Final whole-branch review by Opus: **Ready to merge**, zero
Critical/Important findings. Two parked minor items (commit-
label overload cosmetic + notify.py cancel-twice defensive
design) documented in the ledger.

## What actually closes

- **Flake #4 (SSE broker leak).** The `notify.listen` generator
  now accepts a `stop_event: asyncio.Event | None = None` and
  polls `conn.notifies(timeout=poll_interval_s)` in a
  cooperative loop when set. Backward-compat preserved: existing
  callers that don't pass an event see byte-identical behaviour.
  The SSE broker passes its `_stop_event` in, then joins the
  executor thread on `stop()` via `asyncio.wait_for` on the raw
  future with a 2s cap. Threads named `notify_run*` no longer
  outlive test teardown. The end-to-end test
  `test_sse_delivers_mission_changed_end_to_end` runs green
  (also switched from `httpx.ASGITransport` to a real uvicorn
  server in a daemon thread — httpx's ASGITransport buffers the
  full response body before returning, which was a separate bug
  masking the broker leak).

- **Daemon `AWAITING_USER → DONE` illegal transition.**
  `_run_mission_sync`'s fallthrough now re-reads the mission
  from the DB (`repository.get(mid)`) before calling
  `engine.finish(mid, "done", ..., reason="ended_without_finish_marker")`.
  If the state is not `RUNNING` (typically `AWAITING_USER`
  after a resume returned without new pending), the guard logs
  a warning and returns — mission stays in its current state.
  The SP6c test-side workaround
  (`patch("engine.finish", stub)`) is removed; the test now
  asserts the mission remains in `AWAITING_USER` after the
  daemon returns.

- **`@pytest.mark.jmap_live` infrastructure.** Marker
  registered in `pyproject.toml`. `tests/conftest.py` ships an
  autouse `_skip_jmap_live` that self-skips marked tests when
  `TWAKY_JMAP_LIVE` is unset, plus a `jmap_live_folder`
  fixture that provisions a throwaway JMAP mailbox with prefix
  `zzz-twaky-test-<uuid>` via httpx (JmapClient has no mailbox
  methods today). Demo test in
  `tests/integration/test_jmap_roundtrip_live.py` exercises the
  fixture end-to-end (read-side only — send path awaits
  Email/set support in JmapClient). Meta-tests in
  `tests/test_jmap_live_marker.py` verify skip machinery via
  subprocess.

- **5 deferred-minor items closed** — see `polish(sp6e): D1..D5`
  commits: `_reset_column_cache_for_tests` out of `__all__`;
  `_mailboxes_fetched` flag prevents infinite re-fetch for
  role-less accounts; `ProposeWindow.count` gets `le=2000` at
  the pydantic layer (dead router guard removed);
  propose-results warning fires on `partial === true` with
  a fallback string; ADR sp6d-decisions gets a refinement note
  on restore endpoint provenance suppression.

## Test-suite delta

- New tests added: 15 (SSE unit + broker join, daemon fallthrough
  + running-normally, jmap_live marker meta-tests, adapter
  no-role-mailboxes, propose partial-null-reason).
- Tests re-enabled from previously flaky/parked state:
  `test_sse_delivers_mission_changed_end_to_end`,
  `test_awaiting_user_mission_takes_resume_branch`.
- No tests deleted.

## Deferred to SP6f (still open)

- **PG-backed multi-worker LLM circuit breaker.** Single-process
  breaker (SP6c) is fine for current scale.
- **CI nightly cron for `jmap_live` tests.** Marker + fixture
  shipped in SP6e; the CI schedule needs manual soak first.
- **JmapClient Email/set send path.** Would upgrade the SP6e.C
  demo test from read-side probe to a real send→poll→verify
  roundtrip.
- **Envelope headers backfill** (SP6d Origin column has NULL
  for pre-migration rows; forward-only is intentional).
- **qm competitor lessons.** Apply during SP4/SP5b/SP6f
  brainstorming, not in code today.

## What the operator can do next

1. **Merge PR** — the branch is green + reviewed. Use `gh pr
   merge <n> --merge` after opening; or merge locally.
2. **Deploy** — the sentinel/api/atlas/frontend containers
   already run the merged main code from earlier today; a
   rebuild + `docker compose up -d --force-recreate --no-deps
   <services>` picks up SP6e.
3. **Optional soak** — run `TWAKY_JMAP_LIVE=1
   JMAP_ENDPOINT=... JMAP_ACCOUNT_ID=... JMAP_TOKEN=... uv run
   pytest -m jmap_live` on a throwaway JMAP account to validate
   the fixture end-to-end. Look for `zzz-twaky-test-*` folders
   on the account afterwards to sweep any that survived
   destroy failures.
