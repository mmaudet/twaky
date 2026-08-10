# SP6b — JMAP OAuth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **The spec at `docs/superpowers/specs/2026-08-10-sp6b-jmap-oauth-design.md` is the source of truth for every design detail; this plan is the sequencing + TDD scaffold on top of it.** Read the relevant spec section before starting each task.

**Goal:** Replace the manually-pasted `JMAP_BEARER_TOKEN` env var with a full OIDC authorization code flow against the owner's LemonLDAP-NG + auto-refresh loop, storing encrypted OAuth credentials in a new `oauth_credential` table, exposed via a CRUD API and a new "Auth" tab under `/sentinels/mail`.

**Architecture:** New Python packages `src/twaky/crypto/` (Fernet helper) and `src/twaky/oauth/` (models + repository + RefreshManager + config listener). Two new API routers on `twaky-api`: `oauth_jmap` (browser code flow via authlib) and `mail_sentinel_auth` (CRUD status/refresh/disconnect). New 5th tab "Auth" on `/sentinels/mail`. Refactor of `JmapPollingEventSource` + `JmapMailAdapter` + `MailSentinel._build_adapter` to consume `RefreshManager.get_access_token()` instead of `settings.jmap_bearer_token`. New table `oauth_credential` with Fernet-encrypted `refresh_token_enc` + `access_token_enc` columns, keyed by `sentinel_name`.

**Tech Stack:** Python 3.12, psycopg3 (raw SQL), FastAPI, pydantic v2, `cryptography` (Fernet), `authlib` (already in use for owner OIDC), `httpx`, `itsdangerous` (state cookie signing), Next.js 15 App Router, TanStack Query v5, openapi-fetch, shadcn/ui.

## Global Constraints

Copied verbatim from spec §16 — every task's requirements implicitly include this section.

- **Endpoints**: `/oauth/jmap/*` + `/mail-sentinel/auth/*` at API root, no `/api` prefix (frontend rewrites via `next.config.ts`).
- **New table**: `oauth_credential` (singular, unquoted).
- **NOTIFY channel**: `oauth_credential_changed`, always via `pg_notify(channel, payload)` function form — NEVER `NOTIFY channel, %s` (regression `1b7b58d` on 2026-08-03).
- **Migration file convention**: `sql/010_init_oauth_credential.sh` matches the SP6 T1 template (`sql/008_init_sentinels.sh`).
- **Encryption at rest**: every `*_enc TEXT` column stores `Fernet.encrypt(...)` base64 ASCII. Losing `TWAKY_SECRET_KEY` = losing all credentials. **Never log decrypted secrets.**
- **State cookie**: `twaky_jmap_state`, signed with `settings.api_session_secret` via `itsdangerous.TimestampSigner`, 10-min TTL, `HttpOnly`, `Secure`, `SameSite=Lax`.
- **PKCE**: mandatory (S256), even though the client is confidential — defense in depth.
- **Refresh cadence**: refresh when `access_token_expires_at < now() + 60s`; single-flight lock; in-process cache 30 s.
- **401 handling in JMAP calls**: exactly one force-refresh + retry; second 401 → surface as `last_refresh_error='401_after_refresh'` and stop the poll loop.
- **`return_to` validation**: relative URLs only (must start with `/`, no scheme/host), else default to `/sentinels/mail?tab=auth`. Reuse the SP5 `_safe_return_to` helper from `src/twaky/api/routers/oauth.py`.
- **Error envelope**: SP4/SP5 shape with new codes `oauth_credential_not_found`, `refresh_failed`, `oauth_flow_error`, `validation_failed`.
- **`declared_by` prefix**: sentinel-emitted missions stay `sentinel:mail` (unchanged from SP6).
- **Deletion cascade**: `oauth_credential.sentinel_name` has `ON DELETE CASCADE` on FK to `sentinel(name)`. Reverse does not cascade.
- **Mono-user**: `settings.twaky_owner_email` implicit; only the owner (`require_owner` dep) can drive the OAuth flow.
- **Retired env vars**: `JMAP_BEARER_TOKEN` and `JMAP_ACCOUNT_EMAIL` removed from `.env.example`. Runtime must not crash if either is set — just ignore.
- **Prereq (operator, before merge)**: OIDC client `twaky-mail-sentinel` registered in LemonLDAP-NG manager per spec §3 (refresh_token=1, refresh_token_rotation=1, additional_audiences=james, redirect_uris=`https://twaky.twake-dev.maudet.cloud/oauth/jmap/callback`, allow_offline=1). Captured client_secret goes into `.env` as `JMAP_OAUTH_CLIENT_SECRET`.

## Sequencing rationale

Storage → crypto → refresh manager → API → refactor consumers → UI → E2E. Twelve tasks in dependency order. T1-T5 build the backend engine bottom-up. T6 wires cache invalidation via NOTIFY. T7-T8 expose the API surface. T9-T10 refactor SP6's JMAP code paths to consume the refresh manager. T11 ships the UI. T12 tidies E2E + docs + retires legacy env vars.

## Testing convention

Same as SP6:
- Integration tests: `@pytest.mark.integration` + `@pytest.mark.skipif(not _reachable(), reason=...)`. Host shell needs `TWAKY_PG_HOST=172.27.0.33` for tests to actually hit the DB (twaky-pg is on `twake-network`, hostname not resolvable from host).
- Unit tests: no marker, no external services. `httpx.MockTransport` for OAuth token endpoint mocking.
- API tests: `TestClient(app) + _cookie()` helper from `tests/api/routers/test_skills.py`.
- FE tests: Vitest + MSW for hooks; Playwright for E2E.
- Every task runs its own tests + full gate suite before commit: `uv run ruff check … && uv run ruff format --check … && uv run mypy … && uv run pytest <task tests> -v`.

---

## File Structure

**Created files (new)**

| Path | Purpose |
|---|---|
| `sql/010_init_oauth_credential.sh` | psql-heredoc migration: table + 2 triggers |
| `src/twaky/crypto/__init__.py` | Package init |
| `src/twaky/crypto/secrets.py` | Fernet encrypt/decrypt under `TWAKY_SECRET_KEY` |
| `src/twaky/oauth/__init__.py` | Package init |
| `src/twaky/oauth/models.py` | `OAuthCredential` frozen dataclass |
| `src/twaky/oauth/repository.py` | psycopg CRUD: `get`, `upsert`, `delete` |
| `src/twaky/oauth/refresh_manager.py` | `RefreshManager` + async single-flight refresh + sync wrapper for the JMAP adapter |
| `src/twaky/oauth/config_listener.py` | LISTEN `oauth_credential_changed` → invalidate manager cache |
| `src/twaky/api/routers/oauth_jmap.py` | `GET /oauth/jmap/login`, `GET /oauth/jmap/callback` |
| `src/twaky/api/routers/mail_sentinel_auth.py` | `GET`, `POST /refresh`, `DELETE` on `/mail-sentinel/auth` |
| `src/twaky/api/schemas/oauth.py` | Pydantic `AuthStatus` + response models |
| `frontend/src/hooks/use-mail-sentinel-auth.ts` | `useMailSentinelAuth`, `useForceRefresh`, `useDisconnect` |
| `frontend/src/hooks/use-mail-sentinel-auth.test.tsx` | MSW-mocked hook tests |
| `frontend/src/app/sentinels/mail/auth-tab.tsx` | Client-side Auth tab (5th tab content) |
| `frontend/src/app/sentinels/mail/auth-tab.test.tsx` | Vitest component tests |
| `tests/sql/test_oauth_credential_migration.py` | Static assertions on the migration script |
| `tests/crypto/__init__.py` | Empty |
| `tests/crypto/test_secrets.py` | Fernet helper unit tests |
| `tests/oauth/__init__.py` | Empty |
| `tests/oauth/test_models.py` | Dataclass shape |
| `tests/oauth/test_repository.py` | Integration CRUD |
| `tests/oauth/test_refresh_manager.py` | httpx MockTransport for token endpoint |
| `tests/oauth/test_config_listener.py` | Real NOTIFY invalidates cache |
| `tests/api/routers/test_oauth_jmap.py` | login/callback flow with mocked LemonLDAP |
| `tests/api/routers/test_mail_sentinel_auth.py` | CRUD 401/404/422 matrix |
| `tests/integration/test_jmap_refresh_live.py` | Opt-in `EVAL_LIVE=1` full-loop live test |
| `frontend/tests/e2e/sentinels-mail-auth-connect.spec.ts` | Playwright: Connect → callback → Connected state |
| `frontend/tests/e2e/sentinels-mail-auth-disconnect.spec.ts` | Playwright: Disconnect → Not connected |

**Modified files (existing)**

| Path | Change |
|---|---|
| `pyproject.toml` | Ensure `cryptography>=42` and `authlib>=1.3` in `[project].dependencies` (likely already transitively present; make explicit) |
| `src/twaky/config.py` | Add fields `twaky_secret_key`, `jmap_oauth_client_id`, `jmap_oauth_client_secret`, `jmap_oauth_issuer`, `jmap_oauth_scope`; retire read-only alias for `jmap_bearer_token` (keep field for backward-compat but stop reading it) |
| `.env.example` | Add SP6b block; remove `JMAP_BEARER_TOKEN` + `JMAP_ACCOUNT_EMAIL` |
| `src/twaky/api/main.py` | Register `oauth_jmap.router` + `mail_sentinel_auth.router` |
| `src/twaky/sentinels/sources/jmap_poll.py` | Replace static bearer with `RefreshManager.get_access_token()` per request; 401 → force refresh + retry once |
| `src/twaky/sentinels/mail/adapter.py` | `JmapMailAdapter` gains `token_provider: Callable[[], str]`; each `_call` re-sets `Authorization` header from provider; 401 → provider.force_refresh + retry |
| `src/twaky/sentinels/mail/sentinel.py` | `_build_adapter` instantiates `RefreshManager("mail")` + passes its sync `get_access_token` as the adapter's `token_provider` |
| `frontend/src/app/sentinels/mail/page.tsx` | Add 5th tab `<TabsTrigger value="auth">Auth</TabsTrigger>` + `<TabsContent value="auth"><AuthTab /></TabsContent>` |
| `frontend/src/lib/api-types.d.ts` | Regenerated via `make api-types` after T7 + T8 register new endpoints |
| `docs/api/openapi.yaml` | Regenerated via `make openapi` |
| `README.md` | Retire "Obtaining a JMAP token" DevTools section; add "Connect JMAP account" walkthrough; document `TWAKY_SECRET_KEY` generation |

---

## Task 1: Migration `sql/010_init_oauth_credential.sh` + static tests

**Files:** create `sql/010_init_oauth_credential.sh` + `tests/sql/test_oauth_credential_migration.py`. **Refer to spec §5 for the exact table + trigger definitions.**

**Produces:** table `oauth_credential` (13 columns), 2 PG functions (`notify_oauth_credential_changed`, `oauth_credential_bump_updated_at`), 2 triggers (AFTER INSERT/UPDATE/DELETE for NOTIFY; BEFORE UPDATE for updated_at).

- [ ] **Step 1:** Write `sql/010_init_oauth_credential.sh` modeled on `sql/008_init_sentinels.sh` (bash + `psql -v ON_ERROR_STOP=1` + single-quoted `<<-'EOSQL'` heredoc so dollar-quoted plpgsql passes through unexpanded).
- [ ] **Step 2:** `chmod +x sql/010_init_oauth_credential.sh`.
- [ ] **Step 3:** Static assertion tests in `tests/sql/test_oauth_credential_migration.py`, mirroring `tests/sql/test_sentinels_migration.py`:
  - script exists + executable
  - contains `CREATE TABLE IF NOT EXISTS public.oauth_credential`
  - contains `sentinel_name TEXT NOT NULL UNIQUE REFERENCES sentinel(name) ON DELETE CASCADE`
  - contains `provider TEXT NOT NULL CHECK (provider ~ '^[a-z][a-z0-9_-]{0,63}$')`
  - contains `refresh_token_enc TEXT` + `access_token_enc TEXT` + `access_token_expires_at TIMESTAMPTZ`
  - **regression guard**: contains `pg_notify('oauth_credential_changed'` (function form, NOT `NOTIFY channel, %s`)
  - contains `AFTER INSERT OR UPDATE OR DELETE ON public.oauth_credential`
  - contains `BEFORE UPDATE ON public.oauth_credential`
  - contains `NEW.updated_at := now()` in the bump function
- [ ] **Step 4:** `uv run pytest tests/sql/test_oauth_credential_migration.py -v` → all pass.
- [ ] **Step 5:** Apply on live volume:
  ```
  docker exec -i twaky-pg bash /docker-entrypoint-initdb.d/010_init_oauth_credential.sh
  docker exec -i twaky-pg psql -U "$POSTGRES_USER" -d twaky -c '\d oauth_credential'
  ```
  Expected: table present with the 13 columns.
- [ ] **Step 6:** Commit `feat(sp6b): init oauth_credential table`.

---

## Task 2: Config fields + `.env.example`

**Files:** modify `src/twaky/config.py`, `.env.example`. **Refer to spec §12.**

**Produces:** 5 new `Settings` fields (`twaky_secret_key`, `jmap_oauth_client_id`, `jmap_oauth_client_secret`, `jmap_oauth_issuer`, `jmap_oauth_scope`), matching env vars in `.env.example`, `JMAP_BEARER_TOKEN` + `JMAP_ACCOUNT_EMAIL` removed from `.env.example` (but `jmap_bearer_token` field kept in Settings — with a docstring "deprecated, ignored" — to avoid breaking any deployment that still has it set).

- [ ] **Step 1:** Grep `src/twaky/config.py` for existing style. Add 5 new fields with defaults matching spec §12 (empty strings for secrets, concrete defaults for the URL + scope).
- [ ] **Step 2:** Update `.env.example`: append the SP6b block from spec §12; remove the two retired vars (`JMAP_BEARER_TOKEN=`, `JMAP_ACCOUNT_EMAIL=`).
- [ ] **Step 3:** Sanity check imports: `python -c "from twaky.config import settings; print(settings.jmap_oauth_scope)"` → `"openid profile email offline_access"`.
- [ ] **Step 4:** Gate: `uv run ruff check src/twaky/config.py && uv run ruff format --check src/twaky/config.py && uv run mypy src/twaky/config.py`.
- [ ] **Step 5:** Commit `chore(sp6b): add OAuth config fields, retire JMAP_BEARER_TOKEN`.

---

## Task 3: Fernet encryption helper

**Files:** create `src/twaky/crypto/__init__.py` (empty), `src/twaky/crypto/secrets.py`, `tests/crypto/__init__.py` (empty), `tests/crypto/test_secrets.py`. Ensure `cryptography>=42` in `pyproject.toml`.

**Interfaces:**
- Consumes: `settings.twaky_secret_key`.
- Produces:
  - `encrypt(plaintext: str) -> str` — Fernet ciphertext as ASCII base64.
  - `decrypt(ciphertext: str) -> str` — reverse; raises `cryptography.fernet.InvalidToken` on tamper.
  - `class SecretsUnavailable(RuntimeError)` — raised when key is unset or malformed. Message MUST include the generation command.
  - Re-export `InvalidToken` for convenience.

- [ ] **Step 1:** Verify `cryptography` in `pyproject.toml`; if absent, add `cryptography>=42` to `[project].dependencies` and `uv sync`.
- [ ] **Step 2:** Write `secrets.py` per spec §6 (the full code block is included there — mirror it exactly, with `@lru_cache(maxsize=1)` on `_fernet()`).
- [ ] **Step 3:** Write 5 unit tests in `tests/crypto/test_secrets.py`:
  - `test_encrypt_decrypt_roundtrip`: with a valid Fernet key set via `monkeypatch.setattr(settings, "twaky_secret_key", Fernet.generate_key().decode())`, assert `decrypt(encrypt("hello")) == "hello"`. Cache invalidation: use `_fernet.cache_clear()` in a fixture.
  - `test_missing_key_raises_secrets_unavailable`: key is empty string → `SecretsUnavailable` with "TWAKY_SECRET_KEY" in the message.
  - `test_malformed_key_raises_secrets_unavailable`: key = `"not-a-fernet-key"` → `SecretsUnavailable`.
  - `test_tampered_ciphertext_raises_invalid_token`: encrypt, flip one char in the ciphertext, decrypt → `InvalidToken`.
  - `test_wrong_key_raises_invalid_token`: encrypt with key A, swap to key B, decrypt → `InvalidToken`.
- [ ] **Step 4:** `uv run pytest tests/crypto/test_secrets.py -v` → 5/5 pass.
- [ ] **Step 5:** Gate ruff/format/mypy on `src/twaky/crypto/` + `tests/crypto/`.
- [ ] **Step 6:** Commit `feat(sp6b): Fernet secret encryption helper`.

---

## Task 4: `oauth_credential` models + repository

**Files:** create `src/twaky/oauth/__init__.py` (empty), `src/twaky/oauth/models.py`, `src/twaky/oauth/repository.py`, `tests/oauth/__init__.py` (empty), `tests/oauth/test_models.py`, `tests/oauth/test_repository.py`.

**Interfaces:**
- Consumes: `oauth_credential` table (T1), `twaky.db.get_pool()`.
- Produces:
  - `@dataclass(frozen=True) class OAuthCredential` — 13 fields matching the row (id, sentinel_name, provider, client_id, token_endpoint, session_url, scope, refresh_token_enc, access_token_enc, access_token_expires_at, account_email, account_name, last_refresh_at, last_refresh_error, created_at, updated_at). Note `refresh_token_enc` and `access_token_enc` are `str | None` — a fresh row after auth start has both set; a partial state where only refresh is set is not valid.
  - Repository functions in `twaky.oauth.repository`:
    - `get(sentinel_name: str) -> OAuthCredential | None`
    - `upsert(*, sentinel_name, provider, client_id, token_endpoint, session_url, scope, refresh_token_enc, access_token_enc, access_token_expires_at, account_email, account_name) -> OAuthCredential` — INSERT ... ON CONFLICT (sentinel_name) DO UPDATE SET ... RETURNING *. Also clears `last_refresh_error`, sets `last_refresh_at = now()`.
    - `update_after_refresh(*, sentinel_name, access_token_enc, access_token_expires_at, refresh_token_enc: str | None = None) -> OAuthCredential` — UPDATE ONLY the token fields; if `refresh_token_enc` is None, keep the existing value; sets `last_refresh_at = now()`, `last_refresh_error = NULL`.
    - `set_error(sentinel_name: str, error: str) -> None` — UPDATE `last_refresh_error = %s` only.
    - `delete(sentinel_name: str) -> None` — DELETE; no-op if missing.
  - `class OAuthCredentialNotFound(Exception)` — for callers that need to distinguish missing from other errors.

- [ ] **Step 1:** Write `models.py` (frozen dataclass, tz-aware `datetime` fields).
- [ ] **Step 2:** Write `repository.py` following the style of `src/twaky/sentinels/repository.py` (raw psycopg + `dict_row`, allowlist patches, `RETURNING *` on writes).
- [ ] **Step 3:** Write `test_models.py` unit tests: dataclass frozen (assignment raises `FrozenInstanceError`), field count + types match spec §5.
- [ ] **Step 4:** Write `test_repository.py` integration tests (`@pytest.mark.integration` + `_reachable()` skipif + `TWAKY_PG_HOST=172.27.0.33`):
  - `_wipe` fixture DELETEs FROM oauth_credential before/after each test.
  - `test_get_missing_returns_none`.
  - `test_upsert_inserts_new_row`: upsert with all fields → get returns row with correct values.
  - `test_upsert_updates_existing_row`: two upserts with the same sentinel_name → single row, second one wins.
  - `test_update_after_refresh_preserves_refresh_when_none`: upsert once, then update_after_refresh without new refresh_token → refresh_token_enc unchanged.
  - `test_update_after_refresh_rotates_refresh`: same as above but pass a new refresh_token_enc → column updated.
  - `test_set_error_only_touches_that_column`: upsert, set_error("invalid_grant"), assert other fields unchanged.
  - `test_delete_is_idempotent`: delete twice, no error.
  - `test_delete_cascades_from_sentinel`: NOT tested here (destructive — would drop the seed row). Note as a manual verification instead.
- [ ] **Step 5:** Run + gates: `TWAKY_PG_HOST=172.27.0.33 uv run pytest tests/oauth/ -v` → all pass. Full gate suite on src/tests.
- [ ] **Step 6:** Commit `feat(sp6b): OAuthCredential models + repository`.

---

## Task 5: `RefreshManager`

**Files:** create `src/twaky/oauth/refresh_manager.py`, `tests/oauth/test_refresh_manager.py`. **Refer to spec §7.**

**Interfaces:**
- Consumes: `oauth.repository`, `twaky.crypto.secrets.encrypt`/`decrypt`, `httpx.AsyncClient`.
- Produces:
  - `class RefreshManager(sentinel_name: str)`:
    - `async get_access_token(self) -> str` — cache hit (< 30 s) → return cached; else re-read DB, if `access_token_expires_at > now() + 60s` decrypt + cache + return; else `_refresh()` + return.
    - `async force_refresh(self) -> str` — bypass cache + expiry check, do `_refresh()`.
    - `def invalidate(self) -> None` — clear cache (called by NOTIFY listener).
    - `def sync_get_access_token(self) -> str` — synchronous wrapper (runs the async in a fresh event loop). Used by the JMAP adapter which runs in a thread pool (T8 SP6 runtime does `asyncio.to_thread(inst.process, ...)`).
  - `class RefreshFailed(Exception)` — raised on token endpoint error; message includes the OAuth error code (`invalid_grant`, `invalid_client`, `server_error`, `network`).
  - Internal: `asyncio.Lock` per instance (single flight). Module-level `dict[str, RefreshManager]` singleton cache so `MailSentinel._build_adapter` gets the same instance across process() calls.
  - Constants: `_CACHE_TTL_S = 30.0`, `_EXPIRY_SKEW_S = 60.0`.

- [ ] **Step 1:** Write `refresh_manager.py`. Internal `_cached_token: str | None`, `_cached_at: float`, `_lock: asyncio.Lock`. `_refresh()`:
  ```python
  async def _refresh(self) -> str:
      async with self._lock:
          cred = await asyncio.to_thread(repository.get, self.sentinel_name)
          if cred is None or cred.refresh_token_enc is None:
              raise RefreshFailed("no_credential")
          refresh_token = decrypt(cred.refresh_token_enc)
          async with httpx.AsyncClient(timeout=30.0) as client:
              try:
                  r = await client.post(cred.token_endpoint, data={
                      "grant_type": "refresh_token",
                      "refresh_token": refresh_token,
                      "client_id": cred.client_id,
                      "client_secret": settings.jmap_oauth_client_secret,
                  })
              except httpx.HTTPError as e:
                  await asyncio.to_thread(repository.set_error, self.sentinel_name, f"network:{e.__class__.__name__}")
                  raise RefreshFailed("network") from e
          if r.status_code >= 400:
              body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
              err = body.get("error", f"http_{r.status_code}")
              await asyncio.to_thread(repository.set_error, self.sentinel_name, err)
              raise RefreshFailed(err)
          data = r.json()
          new_access = data["access_token"]
          new_refresh = data.get("refresh_token")  # None if server didn't rotate
          expires_in = int(data.get("expires_in", 3600))
          expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
          await asyncio.to_thread(
              repository.update_after_refresh,
              sentinel_name=self.sentinel_name,
              access_token_enc=encrypt(new_access),
              access_token_expires_at=expires_at,
              refresh_token_enc=encrypt(new_refresh) if new_refresh else None,
          )
          self._cached_token = new_access
          self._cached_at = time.monotonic()
          return new_access
  ```
- [ ] **Step 2:** `get_access_token()`: check `time.monotonic() - self._cached_at < _CACHE_TTL_S and self._cached_token`; else re-read DB, check expiry against now + skew; if fresh, decrypt + cache; else `_refresh()`.
- [ ] **Step 3:** `sync_get_access_token()`: use `asyncio.run(self.get_access_token())` — precedent is `Delegation.delegate()` in `src/twaky/sentinels/delegation.py`. Same "assumes not inside a running loop" caveat, document in the docstring.
- [ ] **Step 4:** Module-level `_MANAGERS: dict[str, RefreshManager] = {}` + `get_manager(sentinel_name) -> RefreshManager` factory that returns the singleton per name. This ensures the cache is shared across callers within one process.
- [ ] **Step 5:** Write tests in `test_refresh_manager.py` (unit — httpx MockTransport + monkeypatched repository):
  - `test_cache_hit_avoids_network`: seed the manager with a cached token, call `get_access_token` — assert MockTransport was NOT called.
  - `test_expired_triggers_refresh`: seed DB with `access_token_expires_at = now() - 1s`, MockTransport returns `{access_token: "new", expires_in: 3600}` → `get_access_token()` returns "new"; assert repository.update_after_refresh was called.
  - `test_refresh_rotates_when_server_provides_new_refresh`: MockTransport returns `refresh_token` in the response → `update_after_refresh` called with `refresh_token_enc` set (not None).
  - `test_refresh_keeps_old_refresh_when_server_omits_it`: MockTransport response has NO `refresh_token` → `update_after_refresh` called with `refresh_token_enc=None`.
  - `test_invalid_grant_records_error_and_raises`: MockTransport returns 400 `{error: "invalid_grant"}` → `set_error("invalid_grant")` called, `RefreshFailed("invalid_grant")` raised.
  - `test_5xx_records_network_and_raises`: MockTransport returns 500 → error is `http_500`, `RefreshFailed` raised.
  - `test_single_flight_lock_serializes_concurrent_refresh`: monkeypatch MockTransport to sleep 100 ms; spawn 3 `get_access_token()` concurrently; assert MockTransport called exactly once (single-flight).
  - `test_sync_wrapper_returns_same_result`: use asyncio to prep state, then call `sync_get_access_token()` from a thread → returns the async result.
- [ ] **Step 6:** Gate + commit `feat(sp6b): RefreshManager with single-flight refresh + sync wrapper`.

---

## Task 6: `oauth/config_listener.py` — invalidate on NOTIFY

**Files:** create `src/twaky/oauth/config_listener.py`, `tests/oauth/test_config_listener.py`. Mirrors `src/twaky/sentinels/config_listener.py` (T3 SP6).

**Interfaces:**
- Consumes: Postgres NOTIFY channel `oauth_credential_changed`, `RefreshManager._MANAGERS` module registry.
- Produces:
  - `async run_oauth_config_listener(dsn: str, *, stop_event: asyncio.Event, channel: str = "oauth_credential_changed") -> None` — LISTEN loop; on each notify, `get_manager(payload).invalidate()`. Reconnect on `OperationalError` with exponential backoff 1s→30s (same pattern as T3 SP6).

- [ ] **Step 1:** Write `config_listener.py`. Copy the reconnect + backoff shape from `src/twaky/sentinels/config_listener.py`; replace `SentinelRegistry` calls with `get_manager(name).invalidate()`.
- [ ] **Step 2:** Integration test in `test_config_listener.py` (`@pytest.mark.integration` + skipif + `TWAKY_PG_HOST` override):
  - Test: upsert an oauth_credential row, spawn `run_oauth_config_listener` task, wait 300 ms, update the row (via repository.update_after_refresh with a dummy value), poll for `_MANAGERS["mail"]._cached_token is None` within 2 s.
- [ ] **Step 3:** Wire the listener into the sentinel runtime: in `src/twaky/sentinels/runtime.py::SentinelRuntime.run()`, spawn `asyncio.create_task(run_oauth_config_listener(...))` alongside the existing `run_config_listener` for sentinels. Regression test: T8 runtime tests still pass.
- [ ] **Step 4:** Gate + commit `feat(sp6b): oauth_credential_changed NOTIFY listener + runtime wiring`.

---

## Task 7: OAuth code flow endpoints (`/oauth/jmap/login` + `/oauth/jmap/callback`)

**Files:** create `src/twaky/api/routers/oauth_jmap.py`, `tests/api/routers/test_oauth_jmap.py`. Modify `src/twaky/api/main.py` to `include_router(oauth_jmap.router)`. **Refer to spec §8.**

**Interfaces:**
- Consumes: `authlib` (existing owner-OIDC pattern in `src/twaky/api/routers/oauth.py`), `itsdangerous.TimestampSigner`, `settings.api_session_secret`, `oauth.repository.upsert`, `crypto.secrets.encrypt`, `require_owner` dep.
- Produces:
  - `GET /oauth/jmap/login?return_to=<safe>` → 302 to `settings.jmap_oauth_issuer + "/oauth2/authorize?..."` with PKCE + state cookie.
  - `GET /oauth/jmap/callback?code=…&state=…` → POST token exchange + userinfo + session probe → `repository.upsert()` → 302 to `return_to` (default `/sentinels/mail?tab=auth&status=connected`).

- [ ] **Step 1:** Write `oauth_jmap.py`. Use `authlib.integrations.starlette_client.OAuth` with a `twaky-mail-sentinel` OAuth registration (client_id/client_secret from settings, `.well-known/openid-configuration` at `settings.jmap_oauth_issuer + "/.well-known/openid-configuration"`).
- [ ] **Step 2:** `login` endpoint: generate `code_verifier` + `code_challenge_s256`, generate `state` (secrets.token_urlsafe(32)), set cookie `twaky_jmap_state = signer.sign(json.dumps({state, code_verifier, return_to, ts})).decode()` with 10-min TTL + HttpOnly + Secure + SameSite=Lax; 302 to authorize URL.
- [ ] **Step 3:** `callback` endpoint: read + verify cookie (signature + age); assert `state` matches; POST token endpoint with `grant_type=authorization_code`, `code`, `code_verifier`, `client_id`, `client_secret`, `redirect_uri`; parse response `{access_token, refresh_token, expires_in, token_type}`.
- [ ] **Step 4:** GET userinfo (`{issuer}/oauth2/userinfo` with Bearer access_token) → capture `email` + `name`.
- [ ] **Step 5:** GET `settings.jmap_session_url` with Bearer access_token → verify 200 (proves the token is accepted by James); do NOT parse the response.
- [ ] **Step 6:** `repository.upsert(sentinel_name="mail", provider="linagora_lemonldap", client_id=settings.jmap_oauth_client_id, token_endpoint=<from oidc metadata>, session_url=settings.jmap_session_url, scope=settings.jmap_oauth_scope, refresh_token_enc=encrypt(refresh_token), access_token_enc=encrypt(access_token), access_token_expires_at=now()+expires_in, account_email=userinfo.email, account_name=userinfo.name)`.
- [ ] **Step 7:** Delete state cookie, 302 to `_safe_return_to(state.return_to, default="/sentinels/mail?tab=auth&status=connected")`.
- [ ] **Step 8:** Error paths (state mismatch, cookie expired, code exchange 4xx, session probe fails): 302 to `/sentinels/mail?tab=auth&status=error&reason=<code>` (codes: `state_mismatch`, `state_expired`, `code_exchange_failed`, `session_probe_failed`).
- [ ] **Step 9:** Register router in `src/twaky/api/main.py`.
- [ ] **Step 10:** Write `test_oauth_jmap.py` using `httpx.MockTransport` to mock LemonLDAP-NG:
  - `test_login_redirects_to_authorize_with_pkce_and_state`: GET /oauth/jmap/login → 302, Location contains `client_id=twaky-mail-sentinel`, `code_challenge`, `code_challenge_method=S256`, `state`, `response_type=code`; Set-Cookie has `twaky_jmap_state`.
  - `test_callback_happy_path`: mock cookie + mock token endpoint returning `{access_token: "at", refresh_token: "rt", expires_in: 3600}` + mock userinfo `{email: "me@x", name: "Me"}` + mock session URL 200 → 302 to `/sentinels/mail?tab=auth&status=connected`; assert oauth_credential row upserted with `account_email="me@x"`.
  - `test_callback_state_mismatch`: cookie has state A, query has state B → 302 with `status=error&reason=state_mismatch`; no DB row created.
  - `test_callback_cookie_expired`: cookie signed 11 min ago → 302 with `status=error&reason=state_expired`.
  - `test_callback_code_exchange_failed`: token endpoint returns 400 → 302 with `status=error&reason=code_exchange_failed`; no DB row.
  - `test_callback_session_probe_failed`: token OK, session URL returns 401 → 302 with `status=error&reason=session_probe_failed`; NO DB row (don't store a token James won't accept).
  - `test_unauthenticated_returns_401` on both `/login` and `/callback` (no `twaky_session` cookie).
- [ ] **Step 11:** Gate + commit `feat(sp6b): OAuth code flow endpoints`.

---

## Task 8: `/mail-sentinel/auth` CRUD API

**Files:** create `src/twaky/api/routers/mail_sentinel_auth.py`, `src/twaky/api/schemas/oauth.py`, `tests/api/routers/test_mail_sentinel_auth.py`. Modify `src/twaky/api/main.py` to include the router. **Refer to spec §9.**

**Interfaces:**
- Consumes: `oauth.repository`, `oauth.refresh_manager.get_manager`, `require_owner`.
- Produces:
  - `GET /mail-sentinel/auth` → `AuthStatus`:
    ```py
    class AuthStatus(BaseModel):
        connected: bool
        provider: str | None
        account_email: str | None
        account_name: str | None
        session_url: str | None
        access_token_expires_at: datetime | None
        last_refresh_at: datetime | None
        last_refresh_error: str | None
    ```
  - `POST /mail-sentinel/auth/refresh` → `AuthStatus` (forces `manager.force_refresh()`, re-reads DB, returns fresh status). 409 `{"code": "oauth_credential_not_found"}` if no row. 502 `{"code": "refresh_failed", "message": "<error>"}` on RefreshFailed.
  - `DELETE /mail-sentinel/auth` → 204. Idempotent (missing row → still 204).

- [ ] **Step 1:** Write `schemas/oauth.py` (`AuthStatus` model, `ConfigDict(extra="forbid")` not needed since read-only, but include for consistency).
- [ ] **Step 2:** Write `mail_sentinel_auth.py`. Endpoints per above. Every endpoint has `_email: str = Depends(require_owner)`.
- [ ] **Step 3:** Register router in `main.py`.
- [ ] **Step 4:** Write tests in `test_mail_sentinel_auth.py` (`_env` autouse fixture from `tests/api/routers/test_skills.py` — pattern is `monkeypatch.setenv("API_SESSION_SECRET", ...)` + `_cookie()` helper). Integration marker if the tests hit real DB.
  - `test_get_returns_disconnected_when_no_row`: `GET /mail-sentinel/auth` → 200 with `connected=false, provider=null, ...`.
  - `test_get_returns_connected_after_upsert`: seed via repository.upsert, GET → `connected=true, account_email=<seeded>`.
  - `test_get_401_unauthenticated`.
  - `test_refresh_409_when_no_credential`.
  - `test_refresh_calls_manager_and_returns_updated_status`: monkeypatch `get_manager` to return a MagicMock whose `force_refresh` is AsyncMock returning "new-token"; then verify GET is fresh.
  - `test_refresh_502_when_refresh_failed`: manager.force_refresh raises `RefreshFailed("invalid_grant")` → 502 with body `{"code": "refresh_failed", "message": "invalid_grant"}`.
  - `test_delete_204_when_row_present`: seed row, DELETE → 204, GET → connected=false.
  - `test_delete_204_when_no_row`: DELETE without prior upsert → 204 (idempotent).
  - `test_delete_401_unauthenticated`.
- [ ] **Step 5:** Regenerate OpenAPI: `make openapi` (or the equivalent script).
- [ ] **Step 6:** Gate + commit `feat(sp6b): mail-sentinel/auth CRUD API`.

---

## Task 9: Refactor `JmapPollingEventSource` to use `RefreshManager`

**Files:** modify `src/twaky/sentinels/sources/jmap_poll.py`. Modify `tests/sentinels/sources/test_jmap_poll.py`.

**Interfaces:**
- Consumes: `oauth.refresh_manager.get_manager(sentinel_name)`.
- Produces: same public shape (`stream(*, stop_event) -> AsyncIterator[tuple[Event, Ack]]`), but Authorization headers now come from `manager.get_access_token()` (fresh per HTTP request) instead of the constructor's `bearer_token`.

- [ ] **Step 1:** Change constructor: remove `bearer_token` param. Add `refresh_manager: RefreshManager` param (default: `get_manager(sentinel_name)` — but keep the param for testability).
- [ ] **Step 2:** Replace class attribute `self.bearer_token` with `self._manager`. Remove the module-level `HEADERS` construction — build headers per-request.
- [ ] **Step 3:** In `_discover_session`, `_seed_state`, `_fetch_changes`, `_fetch_emails`: replace `Authorization: Bearer <self.bearer_token>` with `Authorization: Bearer <await self._manager.get_access_token()>` fetched inside the coroutine right before the HTTP call. Do not cache in a local — different HTTP calls may span a refresh boundary.
- [ ] **Step 4:** 401 handling: catch `httpx.HTTPStatusError` where `status_code == 401`; call `await self._manager.force_refresh()`; retry the SAME call once; if still 401, log error + `repository.set_error(self.sentinel_name, "401_after_refresh")` + sleep poll_interval + continue outer loop.
- [ ] **Step 5:** Update `test_jmap_poll.py`: the existing tests inject a `bearer_token`. Change them to inject a mock RefreshManager returning a fixed token (`AsyncMock(get_access_token=AsyncMock(return_value="mock-token"))`). Existing test assertions remain valid.
- [ ] **Step 6:** Add a new test `test_401_triggers_refresh_and_retry`: mock JMAP responds 401 first time, 200 second time. Assert `manager.force_refresh` was called exactly once + second call to JMAP had the new token in its Authorization header.
- [ ] **Step 7:** Add `test_double_401_records_error`: JMAP responds 401 twice consecutively. Assert `repository.set_error` called with `"401_after_refresh"`. (Repository call can be mocked.)
- [ ] **Step 8:** Run: `TWAKY_PG_HOST=172.27.0.33 RABBITMQ_URL=... uv run pytest tests/sentinels/sources/test_jmap_poll.py -v` → all pass (target ~4-6 tests).
- [ ] **Step 9:** Gate + commit `refactor(sp6b): JmapPollingEventSource consumes RefreshManager`.

---

## Task 10: Refactor `JmapMailAdapter` + `MailSentinel._build_adapter`

**Files:** modify `src/twaky/sentinels/mail/adapter.py`, `src/twaky/sentinels/mail/sentinel.py`. Modify `tests/sentinels/mail/test_adapter.py`, `tests/sentinels/mail/test_sentinel.py`.

**Interfaces:**
- `JmapMailAdapter.__init__` loses `bearer_token`. Gains `token_provider: Callable[[], str]` — a sync callable returning a currently-valid access token. Each `_call(method, args)` invokes it before setting the `Authorization` header on the sync httpx client.
- `MailSentinel._build_adapter(ctx)` instantiates `manager = get_manager("mail")` and passes `token_provider=manager.sync_get_access_token`.

- [ ] **Step 1:** `JmapMailAdapter.__init__`: replace `bearer_token: str` with `token_provider: Callable[[], str]`. Remove the constructor's `_client` header presetting.
- [ ] **Step 2:** In `_call`: build `headers = {"Authorization": f"Bearer {self.token_provider()}", "Accept": "application/json", "Content-Type": "application/json"}` per request. Pass to `client.post(..., headers=headers)`.
- [ ] **Step 3:** 401 handling: catch httpx.HTTPStatusError with status 401; if the token_provider has a `force_refresh` method, call it; retry once. Since the provider is a plain Callable, the adapter checks `hasattr(token_provider, "__self__")` and calls `token_provider.__self__.sync_force_refresh()` if present. (Simpler: pass BOTH `token_provider` and `refresh_now: Callable[[], None]` params. Do the latter.)
- [ ] **Step 4:** `MailSentinel._build_adapter`: use `manager = get_manager("mail")`; pass `token_provider=manager.sync_get_access_token, refresh_now=manager.sync_force_refresh` (add `sync_force_refresh` to RefreshManager as a mirror of `sync_get_access_token` on the same asyncio.run pattern).
- [ ] **Step 5:** Update `test_adapter.py` JMAP tests to inject `token_provider=lambda: "test-token"` instead of `bearer_token="test-token"`. Add `test_adapter_401_calls_refresh_now_and_retries`: token_provider returns "old"; MockTransport responds 401 first time, 200 second; `refresh_now` MagicMock invoked → next `token_provider()` returns "new" → assert 200 return + refresh_now called once.
- [ ] **Step 6:** Update `test_sentinel.py::test_process_processed_default` etc. — `_build_adapter` is patched, so no test change needed unless the assertion touched constructor args.
- [ ] **Step 7:** Run: `TWAKY_PG_HOST=172.27.0.33 uv run pytest tests/sentinels/mail/test_adapter.py tests/sentinels/mail/test_sentinel.py -v` → all pass.
- [ ] **Step 8:** Gate + commit `refactor(sp6b): JmapMailAdapter accepts token_provider + refresh_now callables`.

---

## Task 11: Frontend — Auth tab, hooks, page wiring

**Files:** create `frontend/src/hooks/use-mail-sentinel-auth.ts`, `.test.tsx`, `frontend/src/app/sentinels/mail/auth-tab.tsx`, `.test.tsx`. Modify `frontend/src/app/sentinels/mail/page.tsx`. **Refer to spec §10.**

**Interfaces:**
- Hooks:
  - `useMailSentinelAuth()` — GET status, `staleTime: 30_000`.
  - `useForceRefresh()` — mutation on `POST /mail-sentinel/auth/refresh`; invalidates the status query on success.
  - `useDisconnect()` — mutation on DELETE; invalidates + confirmation dialog handled by the tab.
- `<AuthTab />` client component with 4 states:
  - Loading: spinner.
  - Not connected: `<Button>` "Connect JMAP account" → `window.location.assign('/api/oauth/jmap/login?return_to=' + encodeURIComponent(window.location.pathname + '?tab=auth'))`.
  - Connected: status card with green dot, account email, expires-in badge, buttons Force refresh + Reconnect + Disconnect.
  - Error banner (parse `?status=error&reason=X` from URL): red banner + Retry (same as Connect).

- [ ] **Step 1:** Regenerate `frontend/src/lib/api-types.d.ts` via `make api-types` (or `cd frontend && npm run api-types`) so `AuthStatus` is available.
- [ ] **Step 2:** Write the 3 hooks in `use-mail-sentinel-auth.ts`, matching the shape of `use-sentinels.ts` (openapi-fetch client, TanStack Query v5). Query key: `['mail-sentinel-auth']`. Mutation `onSuccess` calls `queryClient.invalidateQueries({ queryKey: ['mail-sentinel-auth'] })`.
- [ ] **Step 3:** Write 5-7 MSW tests in `use-mail-sentinel-auth.test.tsx`: happy-path status, `connected=false` shape, refresh mutation invalidates, disconnect mutation invalidates, refresh 502 propagates error.
- [ ] **Step 4:** Write `auth-tab.tsx` with the 4 states. Use shadcn `Alert`, `Button`, `Card`, `AlertDialog` primitives (already installed for other pages).
- [ ] **Step 5:** Write `auth-tab.test.tsx` (Vitest + Testing Library): Not-connected renders Connect button; Connected renders account email + Force refresh + Reconnect + Disconnect; Disconnect click opens confirmation dialog; Confirm calls the mutation.
- [ ] **Step 6:** Modify `page.tsx` to add a 5th `<TabsTrigger value="auth">Auth</TabsTrigger>` and `<TabsContent value="auth"><AuthTab /></TabsContent>` after Runs.
- [ ] **Step 7:** `cd frontend && npm run lint && npm run typecheck && npm test -- --run` → all pass. `npm run build` → no errors.
- [ ] **Step 8:** Commit `feat(sp6b): frontend Auth tab + hooks`.

---

## Task 12: E2E specs + docs + retire legacy env vars

**Files:** create `frontend/tests/e2e/sentinels-mail-auth-connect.spec.ts`, `sentinels-mail-auth-disconnect.spec.ts`, `tests/integration/test_jmap_refresh_live.py`. Modify `README.md`.

**Produces:**
- Two Playwright specs mirroring `sentinels-toggle.spec.ts` (T29 SP6) shape: navigate → click → assert.
- Live integration test opt-in via `EVAL_LIVE=1` + all `JMAP_OAUTH_*` env vars: real token refresh against LemonLDAP-NG + James session probe. Skipped by default.
- README section "Sentinels · Mail — Connect JMAP account" documenting: the LemonLDAP client registration (link to spec §3), `TWAKY_SECRET_KEY` generation, the Connect flow via UI, expected token lifecycle.

- [ ] **Step 1:** Write `sentinels-mail-auth-connect.spec.ts`. Use the existing `forgeSessionCookie()` pattern from `frontend/tests/e2e/skills-create.spec.ts`. Steps: goto `/sentinels/mail?tab=auth`; if already connected (from previous run), DELETE via API; assert "Connect JMAP account" button visible; click → wait for URL to be under `auth.twake-dev.maudet.cloud/oauth2/authorize` (LemonLDAP session cookie is pre-set via storageState so no login prompt); wait for callback → wait for URL back on `/sentinels/mail?tab=auth&status=connected`; assert "Connected as" text visible.
- [ ] **Step 2:** Write `sentinels-mail-auth-disconnect.spec.ts`. Precondition: connected (seed via API if not). Click Disconnect → confirm dialog → confirm → status card shows "Not connected".
- [ ] **Step 3:** Write `tests/integration/test_jmap_refresh_live.py`: `@pytest.mark.integration` + `@pytest.mark.skipif(os.environ.get("EVAL_LIVE") != "1" or not settings.jmap_oauth_client_secret, reason=...)`. Test: instantiate a `RefreshManager("mail-test")`; seed a row with a real refresh_token (from env `JMAP_TEST_REFRESH_TOKEN` — human sets it up once); call `force_refresh()`; assert returns a non-empty access token; use it against `settings.jmap_session_url` → 200.
- [ ] **Step 4:** Retire from `README.md`: remove the "Obtaining a JMAP token" DevTools walkthrough (T9 SP6 added it). Replace with "Connect JMAP account" walkthrough: prereq client registration (link to spec §3), generate `TWAKY_SECRET_KEY` (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`), UI flow, expected behavior on token expiry.
- [ ] **Step 5:** Confirm `.env.example` cleanup from T2 removed `JMAP_BEARER_TOKEN` + `JMAP_ACCOUNT_EMAIL` and added the SP6b block. Any residual reference in README removed.
- [ ] **Step 6:** Run backend integration: `TWAKY_PG_HOST=172.27.0.33 uv run pytest tests/integration/test_jmap_refresh_live.py -v` — skipped without `EVAL_LIVE=1` (expected).
- [ ] **Step 7:** Attempt E2E: `cd frontend && npm run test:e2e -- --grep 'sentinels-mail-auth'`. If the pre-existing container `.cache/uv` perm-denied issue (see SP6 T29 ledger note) still blocks, document that the specs are written + lint-clean but execution is deferred.
- [ ] **Step 8:** Commit `test(sp6b): E2E specs + live refresh test + README + retire legacy env vars`.

---

## Wrap-up

After T12 lands + CI green:

1. **Full sanity suite**: `TWAKY_PG_HOST=172.27.0.33 RABBITMQ_URL=... uv run pytest tests/sentinels tests/oauth tests/crypto tests/api tests/sql tests/cli tests/missions tests/evals -v` — no regressions vs main (except the 3 known pre-existing flakes documented in SP6 ledger).
2. **Deploy prereqs**: operator registers LemonLDAP client (spec §3), generates `TWAKY_SECRET_KEY`, populates `.env`.
3. **Migration**: `docker exec -i twaky-pg bash /docker-entrypoint-initdb.d/010_init_oauth_credential.sh`.
4. **Restart**: `docker compose restart twaky-api twaky-sentinel`.
5. **Manual smoke** per spec §13 checklist: connect via UI → send self an email → mission appears in `/missions`.
6. Invoke `superpowers:finishing-a-development-branch` to decide merge vs PR.

## Self-review notes (for the plan writer)

- **Spec coverage**: every spec section (§3 prereq → T-prereq operator step + docs in T12; §4 architecture → T3+T4+T5+T7+T8+T9+T10; §5 SQL → T1; §6 crypto → T3; §7 RefreshManager → T5; §8 code flow → T7; §9 CRUD API → T8; §10 UI → T11; §11 refactor → T9+T10; §12 env vars → T2+T12; §13 testing → each task's tests + T12 E2E; §14 deployment → wrap-up above; §15 tasks → 1:1 mapping; §16 constraints → verbatim in Global Constraints).
- **Type consistency**: `RefreshManager.get_access_token()` returns `str` (from `_refresh` or cache); `sync_get_access_token()` returns `str`; `token_provider: Callable[[], str]` in the adapter. `OAuthCredential.refresh_token_enc: str | None` matches T4 dataclass + T5 refresh flow + T7 callback upsert.
- **Regression guards**: T1 test asserts `pg_notify('oauth_credential_changed'` (function form); T5 test `test_single_flight_lock_serializes_concurrent_refresh` prevents the classic thundering-herd bug; T9 `test_double_401_records_error` prevents infinite refresh loops.
- **Cross-cutting**: T6 wires the NOTIFY listener into `SentinelRuntime.run()` so the sentinel container invalidates its RefreshManager cache when twaky-api rewrites the credential (user re-connects, disconnects, or force-refreshes). Without T6 the sentinel would keep polling with a stale cached access_token for up to 30 s after a UI action.
- **Deferred by design**: `token_endpoint` is stored per-row in T4 for future providers that aren't LemonLDAP-NG; T5's `_refresh()` reads it from the credential row rather than hardcoding — mirror this consistency in T7's callback (derive `token_endpoint` from `{issuer}/oauth2/token` OR from the OIDC metadata document).
- **Scope contract**: JMAP `save_draft` hardening (M4 from SP6 review) stays deferred here — that's a separate JmapMailAdapter concern and can ship as SP6c or a small standalone PR after SP6b.
