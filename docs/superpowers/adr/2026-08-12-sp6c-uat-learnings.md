# ADR: SP6c Early Spam Filter — UAT learnings

- **Status:** Accepted
- **Date:** 2026-08-12
- **Author:** overnight polish loop (Claude Opus 4.7)
- **Context:** SP6c live UAT against Linagora tmail (JMAP + James) on the
  primary owner's real inbox. Findings drove the changes committed on
  branch `sp6c`.

## Decisions

### 1. Spam & phishing-alert mails move to the JMAP `junk` role mailbox

**Decision.** On a `spam` or `phishing-alert` verdict, the `_terminate`
node calls `Email/set` with `mailboxIds` patched to remove the current
mailbox(es) and set the JMAP `junk` role mailbox (Indésirables). The
`$junk` keyword is still written; if the mailbox move fails (missing
role, no permission), we fall back to keyword-only.

**Why.** UAT showed that setting `$junk` alone leaves the message in
INBOX unless the operator has a James sieve filter wired up. Every
tester assumed a spam verdict meant "moved to junk" — because that is
the observable behaviour every mail client on the planet ships. Silent
divergence is a trust bug.

**How to apply.** New buckets that should hide the message from INBOX
must use `set_keywords_bulk(email_id, {kw: True}, mailbox_patches={...})`
— never `set_keyword` alone. Buckets that must stay visible (e.g.
`newsletter`) continue to use `set_keyword` only.

### 2. Rules mutation stays behind the CLI, not the UI, until SP6d

**Decision.** No REST endpoints for rules CRUD in SP6c. Operators
inspect / toggle rules via `twaky mail-sentinel rules …`. The UI stays
read-only: it shows priority + enabled state.

**Why.** Rules are the sharpest lever in the pipeline: a bad priority
buries every legitimate rule below it. UAT surfaced enough classifier
bugs that we did not want a shiny UI encouraging test-drive edits
before the classifier itself is stable. CLI is opt-in for operators
who know what they are doing.

**How to apply.** When SP6d picks this up, prefer a two-step
Propose/Apply flow (Rules editor previews the affected pending
decisions before writing) over a plain PATCH endpoint.

### 3. Live tests self-skip; destructive-DB tests opt-in via env

**Decision.** Any test that would `DELETE` or `TRUNCATE` on the
production DSN is gated on `TWAKY_ALLOW_DESTRUCTIVE_TESTS=1`. Any test
that requires a specific external service (Postgres, RabbitMQ, JMAP,
LLM) `pytest.skip`s cleanly when the DNS lookup or TCP dial fails.

**Why.** During SP6c, four fixtures ran `_wipe()` against the live
production DB when `TWAKY_PG_HOST=172.27.0.33` was set, silently
purging `mail_sentinel_spam_decision` rows the tester was inspecting
in the UI five seconds earlier. See
`docs/superpowers/investigations/2026-08-12-spam-decision-purge.md`.

**How to apply.** New integration tests: import `destructive_wipe_allowed`
from `tests/_conftest_helpers` and skip when it returns False. Never
call `DELETE ... FROM <production_table>` without the guard.

### 4. LLM pipeline gets a circuit breaker + per-node timeouts

**Decision.** Every LangGraph node in the mail pipeline is wrapped by
`resilient_node(name, fn, timeout_s=30)` using a
`ThreadPoolExecutor.submit(...).result(timeout=…)` pattern. The LLM
call additionally consults `LLMCircuitBreaker` (threshold 5,
cool-off 300s, single probe on wake).

**Why.** During UAT the local qwen endpoint stalled for 90+ seconds
under load, blocking every subsequent inbox event in the daemon's
bounded semaphore. One slow upstream should degrade one event, not
the whole sentinel.

**How to apply.** When adding a new node that calls out to a slow
service, wrap it with `resilient_node` — the timeout applies before
the outer `SENTINEL_TIMEOUT_S`. For new external services, add a
similar breaker (dataclass in `robustness.py`) rather than baking
retry logic into the call site.

### 5. Isolated owner_email in daemon tests uses `.invalid` TLD (RFC 6761)

**Decision.** Integration tests that declare a mission but do not want
the live atlas daemon to race them use `owner_email=…@test.invalid`.
Tests that hit the recovery path additionally
`monkeypatch.setattr(atlas_daemon.settings, "twaky_owner_email", …)`.

**Why.** RFC 6761 §6.4 reserves `.invalid` as unresolvable — no real
mailbox can ever collide. Using unique in-domain fake emails
(`test-XXX@example.com`) had two sessions of flakiness where CI's
smtp gateway silently accepted the mail and confused the assertions.

**How to apply.** Any test that touches `mission` state and does not
need the daemon to run the workflow should follow the same pattern.
See `tests/integration/test_daemon_recovery.py` for the canonical
shape.

## Consequences

- Deferred to SP6d: SSE broker leak on `client.stream()` unwind (flake
  #4 blocked here — needs `SSEBroker.stop` code change); daemon
  `AWAITING_USER → DONE` fallthrough (illegal transition path in
  `_run_mission_sync`); Rules CRUD REST + UI editor.
- The junk-mailbox move requires a `role: junk` mailbox to exist in
  the account. Twake Mail provisions this by default; other JMAP
  servers may not. The keyword-only fallback keeps the classifier
  useful in that case.
- Circuit breaker singleton is process-local: a multi-worker sentinel
  fleet does not share the failure count. Acceptable for the current
  single-process daemon; revisit if we scale out.
