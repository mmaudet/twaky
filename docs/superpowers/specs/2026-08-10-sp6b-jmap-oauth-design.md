# SP6b — JMAP OAuth code flow + refresh loop + config CRUD

**Status**: design accepted 2026-08-10. Follow-up to SP6 (`docs/superpowers/specs/2026-08-10-sentinels-design.md`) which shipped `JmapPollingEventSource` reading a manually-pasted bearer token from `settings.jmap_bearer_token`. SP6b replaces that with a full OIDC authorization code flow + auto-refresh, using the owner's own twake-dev infrastructure (LemonLDAP-NG + Apache James).

## 1. Goal

Let the owner authenticate the mail sentinel against their JMAP server via a single click in the Twaky UI, and keep the connection alive indefinitely by silently refreshing the access token before every JMAP call. The bearer token stops being an operator-managed `.env` value and becomes an encrypted, database-stored credential that the runtime refreshes on demand.

## 2. In scope / out of scope

**In scope**
- New table `oauth_credential` (one row per sentinel, MVP is single-row for `mail`).
- Encryption helper (Fernet) with a project-wide `TWAKY_SECRET_KEY` env var.
- `RefreshManager` class fronting all JMAP calls with cached-and-refreshable access tokens.
- OAuth code flow endpoints on twaky-api: `GET /oauth/jmap/login`, `GET /oauth/jmap/callback`.
- CRUD API under `/mail-sentinel/auth`: status, force-refresh, disconnect.
- Frontend Auth tab (5th tab under `/sentinels/mail`).
- Refactor of `JmapPollingEventSource` + `JmapMailAdapter` to consume the refresh manager instead of the static env token.
- Migration retiring `JMAP_BEARER_TOKEN` + `JMAP_ACCOUNT_EMAIL` env vars.

**Explicitly out of scope**
- Multi-account (MVP: one sentinel = one credential row).
- Manual paste-refresh-token flow (dropped — the full code flow works immediately once LemonLDAP client is registered).
- Encryption key rotation / re-encrypt-in-place (SP6c).
- OAuth device flow (out-of-band devices) — SP6c.
- Support for JMAP servers behind non-OIDC auth (Basic, etc.) — SP7 or never.

## 3. Prerequisite (operator, before merge)

The owner registers a new OIDC client in LemonLDAP-NG (`https://auth.twake-dev.maudet.cloud/manager`) with the following configuration:

| Attribute | Value |
|---|---|
| `oidcRPMetaDataOptionsClientID` | `twaky-mail-sentinel` |
| `oidcRPMetaDataOptionsClientSecret` | generated 32+ char secret |
| `oidcRPMetaDataOptionsRedirectUris` | `https://twaky.twake-dev.maudet.cloud/oauth/jmap/callback` |
| `oidcRPMetaDataOptionsRefreshToken` | `1` |
| `oidcRPMetaDataOptionsRefreshTokenRotation` | `1` |
| `oidcRPMetaDataOptionsAdditionalAudiences` | `james` |
| `oidcRPMetaDataOptionsAllowOffline` | `1` |
| `oidcRPMetaDataOptionsAccessTokenExpiration` | 3600 (1 h) |
| `oidcRPMetaDataOptionsRefreshTokenExpiration` | 2592000 (30 d) |
| `oidcRPMetaDataOptionsBypassConsent` | `1` (same-domain) |
| scopes | `openid profile email offline_access` |

The client_secret goes into `JMAP_OAUTH_CLIENT_SECRET` in `.env` on twake-dev. Everything else lands in `.env.example` as concrete defaults for this deployment.

## 4. Architecture

```
                  ┌──────────────────────────────┐
[Owner UI]        │  Frontend /sentinels/mail    │
   click Connect─▶│  (5th tab: Auth)             │
                  └──────────────┬───────────────┘
                                 │ window.location = /api/oauth/jmap/login
                                 ▼
                  ┌──────────────────────────────┐         ┌──────────────────────────────┐
                  │ twaky-api                    │         │ auth.twake-dev.maudet.cloud  │
                  │  /oauth/jmap/login           │────────▶│  /oauth2/authorize           │
                  │   authlib redirect + PKCE    │  (302)  │   (LemonLDAP session reused) │
                  │                              │◀────────│  /oauth2/authorize?code=…    │
                  │  /oauth/jmap/callback        │  (302)  │                              │
                  │   POST token exchange ──────▶│───────▶│  /oauth2/token               │
                  │   INSERT oauth_credential    │         │   {access, refresh, exp}     │
                  │   pg_notify(oauth_cred_ch)   │         └──────────────────────────────┘
                  │                              │
                  │  /mail-sentinel/auth CRUD    │──── writes ────▶ oauth_credential
                  └──────────────────────────────┘                   (refresh_token,
                                                                      access_token both
                                                                      Fernet-encrypted)

                  ┌──────────────────────────────┐         ┌──────────────────────────────┐
                  │ twaky-sentinel               │         │ auth.twake-dev.maudet.cloud  │
                  │  RefreshManager              │──POST──▶│  /oauth2/token               │
                  │   .get_access_token(name):   │  refresh│   grant_type=refresh_token   │
                  │     ├─ in-process cache 30s  │◀────────│   {access, refresh?, exp}    │
                  │     ├─ if expires < now+60s: │         └──────────────────────────────┘
                  │     │   POST token endpoint  │
                  │     │   UPDATE cred (enc)    │         ┌──────────────────────────────┐
                  │     │   pg_notify(oauth_cred_│         │ jmap.twake-dev.maudet.cloud  │
                  │     │      changed)          │         │  /jmap/session               │
                  │     └─ return access_token   │──Bearer▶│  /jmap                       │
                  │                              │  token  └──────────────────────────────┘
                  │  JmapPollingEventSource ─────┼─┐
                  │  JmapMailAdapter ────────────┼─┴─▶ RefreshManager.get_access_token('mail')
                  └──────────────────────────────┘
```

**Two async processes cooperate on the same table:** twaky-api mutates on user action (connect/disconnect), twaky-sentinel mutates on refresh. Both fire `oauth_credential_changed` NOTIFY so the other side invalidates its local cache. `updated_at` + `access_token_expires_at` timestamps let both sides reason about staleness.

## 5. SQL schema

`sql/010_init_oauth_credential.sh` — same template as `sql/008_init_sentinels.sh` (bash + heredoc'd psql).

```sql
CREATE TABLE IF NOT EXISTS public.oauth_credential (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sentinel_name             TEXT NOT NULL UNIQUE
                              REFERENCES sentinel(name) ON DELETE CASCADE,
    provider                  TEXT NOT NULL CHECK (provider ~ '^[a-z][a-z0-9_-]{0,63}$'),
    client_id                 TEXT NOT NULL,
    -- token_endpoint et session_url dérivés du provider config; stockés pour éviter
    -- un round-trip discovery à chaque refresh.
    token_endpoint            TEXT NOT NULL,
    session_url               TEXT NOT NULL,
    scope                     TEXT NOT NULL DEFAULT 'openid profile email offline_access',
    -- Fernet-encrypted (str base64 stockée en TEXT pour lisibilité, tout est ASCII).
    refresh_token_enc         TEXT,
    access_token_enc          TEXT,
    access_token_expires_at   TIMESTAMPTZ,
    -- Métadonnées non secrètes issues du userinfo pour l'UI.
    account_email             TEXT,
    account_name              TEXT,
    -- Statut opérationnel.
    last_refresh_at           TIMESTAMPTZ,
    last_refresh_error        TEXT,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION public.notify_oauth_credential_changed()
    RETURNS trigger AS $NOTIFYFN$
BEGIN
    PERFORM pg_notify('oauth_credential_changed',
        COALESCE(NEW.sentinel_name, OLD.sentinel_name, 'ALL'));
    RETURN COALESCE(NEW, OLD);
END;
$NOTIFYFN$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS oauth_credential_notify ON public.oauth_credential;
CREATE TRIGGER oauth_credential_notify
    AFTER INSERT OR UPDATE OR DELETE ON public.oauth_credential
    FOR EACH ROW EXECUTE FUNCTION public.notify_oauth_credential_changed();

CREATE OR REPLACE FUNCTION public.oauth_credential_bump_updated_at()
    RETURNS trigger AS $BUMPFN$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$BUMPFN$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS oauth_credential_touch_updated_at ON public.oauth_credential;
CREATE TRIGGER oauth_credential_touch_updated_at
    BEFORE UPDATE ON public.oauth_credential
    FOR EACH ROW EXECUTE FUNCTION public.oauth_credential_bump_updated_at();
```

Regression guard identical to SP6: NOTIFY uses `pg_notify()` function form.

## 6. Encryption helper

`src/twaky/crypto/__init__.py` (empty) + `src/twaky/crypto/secrets.py`:

```python
"""Symmetric encryption for at-rest secrets (OAuth tokens, future skills API keys).

Uses Fernet (AES-128-CBC + HMAC-SHA256). Key comes from TWAKY_SECRET_KEY env var,
generated once via:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Loss of TWAKY_SECRET_KEY = loss of every encrypted credential. Store in the same
secrets manager as .env (currently just the deploy checkout on twake-dev).
"""

from __future__ import annotations

import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from twaky.config import settings

log = logging.getLogger(__name__)


class SecretsUnavailable(RuntimeError):
    """Raised when TWAKY_SECRET_KEY is unset or malformed."""


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = settings.twaky_secret_key
    if not key:
        raise SecretsUnavailable(
            "TWAKY_SECRET_KEY is not set. Generate with "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"` "
            "and add to .env."
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as e:  # ValueError / InvalidToken subclass
        raise SecretsUnavailable(f"TWAKY_SECRET_KEY malformed: {e}") from e


def encrypt(plaintext: str) -> str:
    """Return base64-encoded ciphertext (ASCII-safe for TEXT columns)."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Reverse of encrypt(). Raises InvalidToken on tamper or wrong key."""
    return _fernet().decrypt(ciphertext.encode()).decode()


__all__ = ["encrypt", "decrypt", "SecretsUnavailable", "InvalidToken"]
```

Adds `cryptography>=42` to `pyproject.toml` if not already present (likely already there transitively via other deps).

## 7. RefreshManager

`src/twaky/oauth/refresh_manager.py`:

```python
class RefreshManager:
    """Single-flight access-token cache + refresh loop for a sentinel.

    Instance per (sentinel_name). Cache lives in-process; DB is source of
    truth. On cache miss OR expires_at < now+SKEW: refresh, update DB, fire
    oauth_credential_changed NOTIFY, return new token. LISTEN on the NOTIFY
    channel invalidates the in-process cache when another process (twaky-api
    or a peer sentinel worker) rewrites the row.
    """

    def __init__(self, sentinel_name: str) -> None: ...

    async def get_access_token(self) -> str:
        """Return a currently-valid access token. May block on refresh."""

    async def force_refresh(self) -> str:
        """Refresh now, bypass cache. Used by CRUD 'force refresh' + on 401 retry."""

    def invalidate(self) -> None:
        """Drop cache; next get_access_token re-reads DB. Called by NOTIFY listener."""
```

**Refresh mechanics** (single flight — an `asyncio.Lock` per instance prevents concurrent refresh):

1. `SELECT * FROM oauth_credential WHERE sentinel_name = %s FOR UPDATE`.
2. Decrypt `refresh_token_enc`.
3. `httpx.AsyncClient.post(token_endpoint, data={grant_type: "refresh_token", refresh_token, client_id, client_secret})` — Basic auth OR post-body creds depending on client config; MVP is post-body.
4. Response: `{access_token, expires_in, refresh_token?, token_type}`. If server rotates refresh_token, use the new one; else keep the old.
5. Encrypt both, `UPDATE oauth_credential SET access_token_enc=…, refresh_token_enc=…, access_token_expires_at=now()+expires_in, last_refresh_at=now(), last_refresh_error=NULL WHERE sentinel_name=%s`.
6. Commit → trigger fires → NOTIFY → other listeners invalidate.
7. Return new access_token.

**Error handling**:
- HTTP 400 `invalid_grant` (refresh_token expired/revoked): store `last_refresh_error='invalid_grant'`, don't clear the row (the operator can see it), raise `RefreshFailed` to caller.
- HTTP 5xx / network error: raise `RefreshFailed`, don't touch DB. Caller decides (JMAP source will sleep + retry next poll).
- Any success wipes `last_refresh_error`.

**Cache TTL**: 30 s (avoids DB round-trip on every JMAP request). Cache invalidated on NOTIFY OR on 401 from JMAP (via `invalidate()` + `force_refresh()`).

`src/twaky/oauth/models.py`, `src/twaky/oauth/repository.py`: standard dataclass + psycopg CRUD, mirroring `sentinels/repository.py`.

## 8. OAuth code flow endpoints

`src/twaky/api/routers/oauth_jmap.py` — mimics the existing `oauth.py` (owner login), using authlib.

`GET /oauth/jmap/login?return_to=<safe>`:
- Requires `require_owner` dep — only the authenticated owner can start the JMAP flow.
- Generates PKCE `code_verifier`, `code_challenge`.
- Generates `state` (random), stores `{return_to, code_verifier, sentinel_name: "mail"}` under `state` in a signed short-lived cookie (`twaky_jmap_state`, TTL 10 min).
- 302 to `settings.jmap_oauth_issuer + "/oauth2/authorize?..."` with `client_id`, `redirect_uri`, `response_type=code`, `scope`, `state`, `code_challenge`, `code_challenge_method=S256`.

`GET /oauth/jmap/callback?code=…&state=…`:
- Requires `require_owner`.
- Reads + verifies `twaky_jmap_state` cookie against `state` param.
- POST `settings.jmap_oauth_issuer + "/oauth2/token"` with `grant_type=authorization_code`, `code`, `code_verifier`, `client_id`, `client_secret`, `redirect_uri`.
- Fetches userinfo (`GET issuer/oauth2/userinfo` with Bearer access_token) → captures `email` and `name`.
- Fetches JMAP session (`GET session_url` with Bearer access_token) → confirms token is accepted by James; captures `apiUrl` implicitly (not stored, derived at call time).
- INSERT/UPDATE `oauth_credential`:
  - `sentinel_name='mail'`, `provider='linagora_lemonldap'`, `client_id=settings.jmap_oauth_client_id`, `token_endpoint`, `session_url=settings.jmap_session_url`, `scope=settings.jmap_oauth_scope`, `refresh_token_enc`, `access_token_enc`, `access_token_expires_at`, `account_email`, `account_name`, `last_refresh_at=now()`, `last_refresh_error=NULL`.
- Delete the state cookie, 302 to `return_to || "/sentinels/mail?tab=auth&status=connected"`.

Error paths (state mismatch, code exchange 4xx, session URL rejects token) → 302 to `return_to || "/sentinels/mail?tab=auth&status=error&reason=<code>"`.

## 9. CRUD API

`src/twaky/api/routers/mail_sentinel_auth.py` — 4 endpoints, all `require_owner`.

- `GET /mail-sentinel/auth` → `AuthStatus`:
  ```json
  {
    "connected": true,
    "provider": "linagora_lemonldap",
    "account_email": "mmaudet@twake-dev.maudet.cloud",
    "account_name": "Michel Maudet",
    "session_url": "https://jmap.twake-dev.maudet.cloud/jmap/session",
    "access_token_expires_at": "2026-08-10T15:03:00Z",
    "last_refresh_at": "2026-08-10T14:03:00Z",
    "last_refresh_error": null
  }
  ```
  `connected=false` + null everything if no row.

- `POST /mail-sentinel/auth/refresh` → forces `RefreshManager.force_refresh()`, returns the new status. 409 if no credential.

- `DELETE /mail-sentinel/auth` → deletes the credential row (trigger fires NOTIFY → sentinel invalidates + stops polling until reconnected). 204.

The FE links directly to `/api/oauth/jmap/login?return_to=…` (no dedicated login-url endpoint — one less thing to maintain).

Error codes: `oauth_credential_not_found`, `refresh_failed`, `validation_failed` (bad return_to), following SP6 envelope convention.

## 10. Frontend Auth tab

`frontend/src/app/sentinels/mail/page.tsx` — add 5th tab `Auth` after Runs.

New file `frontend/src/app/sentinels/mail/auth-tab.tsx` (client component, imported by the tabbed page):

**States**:
- **Loading** — hook `useMailSentinelAuth()` fetching status.
- **Not connected** — big card: "The mail sentinel needs access to your JMAP mailbox." Button **Connect JMAP account** → `window.location.href = '/api/oauth/jmap/login?return_to=/sentinels/mail?tab=auth'`.
- **Connected** — status card:
  - Green dot + `account_email`.
  - "Access token refreshes in N minutes" (computed from `access_token_expires_at`).
  - "Last refresh: <relative time>" or `last_refresh_error` badge in red.
  - Buttons: **Force refresh** (calls `POST /mail-sentinel/auth/refresh`), **Reconnect** (same link as Connect), **Disconnect** (confirmation dialog → `DELETE`).
- **Error state** (URL query `?status=error&reason=…`) — red banner with reason, retry button.

New hooks in `frontend/src/hooks/use-mail-sentinel-auth.ts`:
- `useMailSentinelAuth()` — GET status, 30 s stale.
- `useForceRefresh()` — mutation.
- `useDisconnect()` — mutation.

Reuse the existing shadcn `Alert`, `Button`, `Card`, `AlertDialog` primitives.

## 11. Refactor of JMAP source + adapter

`src/twaky/sentinels/sources/jmap_poll.py`:
- Constructor loses `bearer_token`, gains `refresh_manager: RefreshManager` (or takes `sentinel_name` and instantiates internally).
- `_discover_session`, `_seed_state`, `_fetch_changes`, `_fetch_emails`: replace `Authorization: Bearer <static>` with `Authorization: Bearer <await refresh_manager.get_access_token()>` computed just before each HTTP request.
- 401 path: `await refresh_manager.force_refresh()`, retry once; second 401 → log error, mark `last_refresh_error='401_after_refresh'`, sleep poll interval (unchanged behavior).

`src/twaky/sentinels/mail/adapter.py`:
- `JmapMailAdapter.__init__` loses `bearer_token`; gains a callable `token_provider: Callable[[], str]` (sync wrapper around `RefreshManager.get_access_token()` running in an event loop or a sync equivalent). Alternative: keep the adapter sync but rebuild the `httpx.Client` headers on every `_call`. Choose the latter — simpler + no event-loop-from-thread awkwardness.
- Every `_call(method, args)` fetches a fresh access_token via `token_provider()` before setting the `Authorization` header.

`src/twaky/sentinels/mail/sentinel.py`:
- `_build_adapter` reads the accountId + apiUrl from the JMAP session response (unchanged) but sources the bearer via `RefreshManager` for this sentinel.
- Since `process()` runs in a thread pool (per T8 runtime), the token_provider must be thread-safe. The `RefreshManager.get_access_token()` async method is fronted by a `sync_get_access_token()` that runs the coroutine in a fresh event loop (mirrors `Delegation.delegate()` pattern from SP6).

## 12. Environment vars

**Added** to `.env.example`:
```env
# --- SP6b: JMAP OAuth ---
# Fernet symmetric key. Generate ONCE with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Losing this key = losing every encrypted credential.
TWAKY_SECRET_KEY=

# OIDC client for the mail sentinel — see spec §3 (LemonLDAP-NG manager).
JMAP_OAUTH_CLIENT_ID=twaky-mail-sentinel
JMAP_OAUTH_CLIENT_SECRET=
JMAP_OAUTH_ISSUER=https://auth.twake-dev.maudet.cloud
JMAP_OAUTH_SCOPE=openid profile email offline_access

# JMAP session endpoint (must end in /jmap/session per RFC 8620 conventions).
JMAP_SESSION_URL=https://jmap.twake-dev.maudet.cloud/jmap/session
```

**Removed** from `.env.example` (retiring SP6 vars):
```env
JMAP_BEARER_TOKEN=       # replaced by oauth_credential.access_token_enc
JMAP_ACCOUNT_EMAIL=      # captured from OIDC userinfo, stored as account_email
```

`JMAP_POLL_INTERVAL_S` stays.

## 13. Testing strategy

**Unit** (no network, no DB):
- `test_crypto_secrets.py`: encrypt+decrypt roundtrip; missing key → `SecretsUnavailable`; malformed key → `SecretsUnavailable`; tampered ciphertext → `InvalidToken`.
- `test_refresh_manager.py`: `httpx.MockTransport` for the token endpoint; asserts cache hit avoids HTTP, expired triggers refresh, single-flight lock serializes concurrent get, 400 invalid_grant sets last_refresh_error + raises, 5xx doesn't touch DB.
- `test_oauth_jmap_router.py` (with `TestClient` + MSW-style mock via `httpx.MockTransport`): login redirects with correct params, callback fetches token + userinfo + session, INSERT happens with encrypted fields, error paths redirect to error status.
- Frontend: `use-mail-sentinel-auth.test.tsx` (MSW) + `auth-tab.test.tsx` (Vitest + Testing Library).

**Integration** (real Postgres + real LemonLDAP):
- `test_oauth_credential_repository.py`: INSERT + UPDATE + trigger NOTIFY delivered.
- `tests/integration/test_jmap_refresh_live.py` (opt-in via `EVAL_LIVE=1` + full OAuth env vars): full flow — refresh_token → refresh via LemonLDAP → new access_token → validates against James session URL.

**E2E** (Playwright, opt-in):
- `sentinels-mail-auth-connect.spec.ts`: navigate to /sentinels/mail#auth → click Connect → LemonLDAP already logged in (test uses stored state) → callback → status shows Connected.
- `sentinels-mail-auth-disconnect.spec.ts`: from Connected → click Disconnect → confirm → status back to Not connected.

**Manual smoke** (user, post-merge):
1. Owner navigates to `/sentinels/mail#auth`.
2. Clicks Connect → LemonLDAP redirect → callback → Auth tab shows "Connected as mmaudet@twake-dev.maudet.cloud".
3. Sends themselves an email at `mmaudet@twake-dev.maudet.cloud`.
4. Within `JMAP_POLL_INTERVAL_S` seconds a mission appears in `/missions` with the email preview.
5. Waits 1 h. Access token has silently refreshed. Sends another email. Still works.

## 14. Deployment

1. **Prereq (manual, ~5 min)**: owner registers the LemonLDAP client per §3, captures client_secret.
2. **Migration**: `docker exec -i twaky-pg bash /docker-entrypoint-initdb.d/010_init_oauth_credential.sh`.
3. **Env**: generate `TWAKY_SECRET_KEY`, populate `JMAP_OAUTH_CLIENT_SECRET`, remove `JMAP_BEARER_TOKEN` + `JMAP_ACCOUNT_EMAIL`, restart `twaky-api` + `twaky-sentinel` (`docker compose restart twaky-api twaky-sentinel`).
4. **First connect**: owner does the manual smoke test.

If the container was already running with the old `JMAP_BEARER_TOKEN`, the T6b `settings.jmap_bearer_token` code path is retired; the sentinel simply waits (no crash, `last_refresh_error='no_credential'`) until the owner connects via UI.

## 15. Task decomposition preview

The plan (SP6b implementation plan) will decompose into ~12 tasks:

1. `sql/010_init_oauth_credential.sh` + static assertion tests.
2. `src/twaky/config.py` new fields + `.env.example` update.
3. `src/twaky/crypto/secrets.py` + tests.
4. `src/twaky/oauth/{models,repository}.py` + tests.
5. `src/twaky/oauth/refresh_manager.py` + tests (httpx MockTransport).
6. `src/twaky/oauth/config_listener.py` (NOTIFY listener) — mirrors T3 config_listener pattern.
7. `src/twaky/api/routers/oauth_jmap.py` + tests (login/callback endpoints).
8. `src/twaky/api/routers/mail_sentinel_auth.py` + `schemas/oauth.py` + tests.
9. Refactor `JmapPollingEventSource` + `JmapMailAdapter` to consume RefreshManager. Regression tests.
10. Refactor `MailSentinel._build_adapter` to inject RefreshManager. Regression tests.
11. Frontend hooks + Auth tab + tab wiring in mail detail page.
12. E2E specs + docs + `.env.example` cleanup + retire `JMAP_BEARER_TOKEN` references from README.

## 16. Global constraints (verbatim into the plan)

- **Endpoints**: `/oauth/jmap/*` + `/mail-sentinel/auth/*` at API root, no `/api` prefix (frontend rewrites).
- **New table**: `oauth_credential` (singular, unquoted).
- **NOTIFY channel**: `oauth_credential_changed`, always via `pg_notify(channel, payload)` function form.
- **Migration file convention**: `sql/010_init_oauth_credential.sh` matches the SP6 template.
- **Encryption at rest**: every `*_enc TEXT` column stores `Fernet.encrypt(...)` base64 ASCII. Losing `TWAKY_SECRET_KEY` = losing all credentials. Never log decrypted secrets.
- **State cookie**: `twaky_jmap_state`, signed with `settings.api_session_secret` via `itsdangerous.TimestampSigner`, 10-min TTL, HttpOnly + Secure + SameSite=Lax.
- **PKCE**: mandatory (S256), even though the client is confidential — defense in depth against code interception.
- **Refresh cadence**: refresh when `access_token_expires_at < now() + 60s`, single-flight lock, cache 30 s.
- **401 handling in JMAP calls**: exactly one force-refresh + retry; second 401 → surface as `last_refresh_error` and stop the poll loop until the operator reconnects.
- **`return_to` validation**: relative URLs only (must start with `/`, no scheme/host), else default to `/sentinels/mail?tab=auth`. Reuse the SP5 `_safe_return_to` helper.
- **Error envelope**: SP4/SP5 shape with new codes `oauth_credential_not_found`, `refresh_failed`, `oauth_flow_error`.
- **`declared_by` prefix (unchanged)**: sentinel-emitted missions stay `sentinel:mail`.
- **Deletion cascade**: `oauth_credential.sentinel_name` has `ON DELETE CASCADE` → if the sentinel row is ever removed, its credential goes with it. Reverse doesn't cascade — deleting only the credential just disconnects.
- **Mono-user (unchanged)**: `settings.twaky_owner_email` implicit; only the owner can drive the OAuth flow (via `require_owner`).
