# Twaky API — Design (Sub-project 3a of 5+)

**Status:** draft, awaiting user review
**Date:** 2026-08-01
**Owner:** mmaudet
**Related:** builds on Foundations (sub-project 1, merged as `fe838f6`) + Agents+Atlas (sub-project 2, merged as `dbd2e62` + SSRF post-merge fix `3017dac`).

**Sub-project 3 decomposition:** the original "sub-project 3 = HTTP API + Frontend" scope was too big for one SDD cycle. Split during brainstorming into **3a (API)** — this document — and **3b (Frontend Control Tower)** — separate spec, brainstormed after 3a's spec is approved. The OpenAPI schema produced by 3a is the seam that lets 3b start in parallel against a mock server.

---

## 1. Purpose

Ship `twaky-api`, a dedicated FastAPI container that exposes the mission engine over HTTP + a Server-Sent Events (SSE) stream. The API is the seam between the Python core of Twaky and any future client — first the Next.js Control Tower (sub-project 3b), later any integrator with a valid OIDC session.

The OpenAPI contract is a first-class deliverable: `docs/api/openapi.yaml` versioned in the repo, so sub-project 3b can generate a typed client + spin a mock server the day after 3a's spec is approved.

## 2. Non-goals

- No frontend (sub-project 3b).
- No federation endpoints (`POST /messages` for peer instances — sub-project 4).
- No write-side (draft-and-send email, CalDAV create — sub-project 5).
- No multi-user. Twaky is mono-owner; the API validates the OIDC session's `email` claim matches `settings.twaky_owner_email` on every request.
- No admin API (creating / deleting instances). Deployment handles that at the compose level.
- No bearer-token auth. Cookie-session-only for MVP; bearer added later if a real M2M use case emerges.
- No refresh-token silent renewal. 8-hour session TTL; users re-login when it expires.

## 3. Architecture

```
twake-network (external, 172.27.0.0/16)

  ┌────────────────────────────────────────────────────────────────────┐
  │                                                                    │
  │   traefik ── labels ──► twaky-api  (uvicorn on :8000, no host port)│
  │                             │                                      │
  │      https://twaky.twake-dev.maudet.cloud                          │
  │                             │                                      │
  │                             ▼                                      │
  │   ┌──────────────────────────────────────────────────────┐         │
  │   │  FastAPI ASGI app                                    │         │
  │   │                                                      │         │
  │   │   Middleware:                                        │         │
  │   │     1. SessionMiddleware (signed cookie, HttpOnly,   │         │
  │   │        SameSite=Lax, 8h TTL)                         │         │
  │   │     2. require_owner (session email == owner_email)  │         │
  │   │     3. structlog request logging                     │         │
  │   │                                                      │         │
  │   │   Routers:                                           │         │
  │   │     • /oauth/{login,callback,logout}                 │         │
  │   │     • /missions/*                                    │         │
  │   │     • /events (SSE)                                  │         │
  │   │     • /me                                            │         │
  │   │     • /healthz                                       │         │
  │   │                                                      │         │
  │   │   ┌─────────────────────────────────────────────┐    │         │
  │   │   │ SSE broker (in-process singleton)           │    │         │
  │   │   │   • dedicated psycopg conn: LISTEN mission_changed        │
  │   │   │   • subscribers: dict[UUID, asyncio.Queue]  │    │         │
  │   │   │   • broadcast(payload) fanouts to all       │    │         │
  │   │   └──────────────┬──────────────────────────────┘    │         │
  │   │                  │                                   │         │
  │   └──────────────────┼───────────────────────────────────┘         │
  │                      │                                              │
  │   direct import (in-process):                                       │
  │     from twaky.missions import engine, repository                   │
  │                      │                                              │
  │                      ▼                                              │
  │   twaky-pg ◄── shared psycopg pool ─── twaky-atlas (daemon)         │
  │      ▲                                       │                     │
  │      └──── engine._notify emits NOTIFY mission_changed on every    │
  │            transition (additive; existing mission_declared/        │
  │            mission_resumed channels retained for the daemon)       │
  │                                                                    │
  └────────────────────────────────────────────────────────────────────┘
```

**Key architectural choices:**

- **Dedicated container** `twaky-api`, sharing the `twaky:local` image with the daemon. `command: ["uvicorn", "twaky.api.main:app", "--host", "0.0.0.0", "--port", "8000"]`. No host port; Traefik terminates HTTPS at `twaky.twake-dev.maudet.cloud`.
- **No inter-service HTTP.** The API imports `twaky.missions.engine` and `twaky.missions.repository` directly. The daemon and API are two independent processes that share only Postgres. Zero coupling in code, cooperative isolation at runtime.
- **In-process SSE broker.** A single background task in the uvicorn process holds `LISTEN mission_changed`. Each `GET /events` connection gets its own `asyncio.Queue()` registered with the broker. One Postgres connection serves N clients.

## 4. API surface

### 4.1 Auth (public routes)

| Route | Description |
|---|---|
| `GET /oauth/login?return_to=/` | 302 → LemonLDAP-NG authorize endpoint with `state` + PKCE `code_challenge` |
| `GET /oauth/callback?code=&state=` | Validates state, exchanges code, verifies `id_token`, sets session cookie, 302 → `return_to` |
| `POST /oauth/logout` | Purges session cookie, 302 → LemonLDAP `end_session_endpoint` |

### 4.2 Mission CRUD (session-protected)

| Route | Body / Query | Response | Notes |
|---|---|---|---|
| `POST /missions` | `{"intent_text": str}` | 201 `Mission` | `declared_by` set from session `email` |
| `GET /missions` | `?state=<state>&limit=50&offset=0` | 200 `[Mission, ...]` | Default = `repository.list_live` (non-terminal) |
| `GET /missions/{mid}` | — | 200 `Mission` \| 404 | Includes `artifacts`, `plan`, timestamps |
| `POST /missions/{mid}/resume` | `{"user_response": dict}` | 200 `Mission` \| 409 | 409 when `state != awaiting_user` |
| `POST /missions/{mid}/cancel` | `{"reason": str}` | 200 `Mission` \| 409 | 409 on terminal state |
| `GET /missions/{mid}/trace` | — | 302 → Langfuse | Direct redirect to `<langfuse>/project/<pid>/sessions/mission-<mid>` |

### 4.3 Live channel

| Route | Description |
|---|---|
| `GET /events` | `text/event-stream` — one event per mission transition, keep-alive comment every 15 s |

### 4.4 User / probe

| Route | Description |
|---|---|
| `GET /me` | `{"owner_email": "...", "langfuse_base_url": "..."}` |
| `GET /healthz` | 200 `{"status": "ok"}` — no auth required (used by Docker healthcheck) |

### 4.5 Error model

Uniform envelope on any non-2xx:

```json
{"error": {"code": "invalid_transition", "message": "...", "detail": {...}}}
```

HTTP codes:

| Code | Meaning |
|---|---|
| 401 | No valid session — frontend redirects to `/oauth/login` |
| 403 | Session valid but `email != settings.twaky_owner_email` — frontend shows "not the instance owner" |
| 404 | Resource not found |
| 409 | Engine `check_transition` guard rejected the request |
| 422 | Pydantic validation error |
| 500 | Server error (logged with structlog + Langfuse) |

### 4.6 Pagination

`?limit=50&offset=0` on `GET /missions`. Expected volume is low (mono-owner). Keyset cursor is a fast follow if volume grows.

### 4.7 OpenAPI-first

- FastAPI generates `/openapi.json` at runtime automatically.
- `make openapi` target dumps a static YAML to `docs/api/openapi.yaml`, versioned in the repo. This is the source of truth for 3b's client generation.
- CI check: `make openapi && git diff --exit-code docs/api/openapi.yaml` — merging a route without regenerating the YAML fails CI.
- 3b consumers can point `openapi-typescript-codegen` at the file, or run `npx @stoplight/prism-cli mock docs/api/openapi.yaml` for a local mock backend.

### 4.8 Mission schema

`twaky.missions.models.Mission` is used directly as the response body Pydantic model. No intermediate DTO. FastAPI serialises fields as-is (`id`, `intent_text`, `state`, `owner_email`, `declared_by`, `declared_at`, `plan`, `artifacts`, terminal timestamps).

## 5. Auth & session

### 5.1 LemonLDAP-NG client to provision

Add to `twake_auth/config/lmConf-1.json.ldap.template` in the deploy repo (same mechanism used for `twaky-langfuse` and `twaky-plume`):

```
Client ID:     twaky-api
Client secret: (generated, injected into twaky-api .env)
Redirect URI:  https://twaky.twake-dev.maudet.cloud/oauth/callback
Grants:        authorization_code (with PKCE)
Scopes:        openid, email, profile
Post-logout:   https://twaky.twake-dev.maudet.cloud/
```

The deploy-repo change is a documented prerequisite in the plan's rollout section.

### 5.2 OIDC library

`authlib.integrations.starlette_client` — `authlib` was added as a dep in sub-project 2 (T5) for Plume's token exchange. Same package, higher-level integration for FastAPI. Roughly 40 LOC for the whole flow.

### 5.3 Flow (Authorization Code + PKCE)

```
1. Browser → GET /oauth/login?return_to=/missions
2. API:
     - Generate `state` + `code_verifier`
     - Store {state, code_verifier, return_to} in short-lived (5min) HttpOnly temp cookie
     - Redirect 302 → <LEMON>/oauth2/authorize?client_id=twaky-api
       &response_type=code&code_challenge=<S256(verifier)>&code_challenge_method=S256
       &state=<state>&scope=openid+email+profile
       &redirect_uri=https://twaky.twake-dev.maudet.cloud/oauth/callback

3. LemonLDAP authenticates → 302 /oauth/callback?code=...&state=...

4. API:
     - Verify state (against temp cookie)
     - Exchange code + code_verifier → id_token + access_token
     - Verify id_token signature (RS256, JWKS fetched from LemonLDAP metadata)
     - Extract claims: email, sub
     - GUARD: reject 403 if email != settings.twaky_owner_email
     - Set session cookie (signed, HttpOnly, SameSite=Lax, 8h TTL)
     - Purge temp cookie
     - Redirect 302 → return_to
```

### 5.4 Cookie configuration

| Cookie | Purpose | Config |
|---|---|---|
| `twaky_session` | Authenticated session | signed (itsdangerous), HttpOnly, Secure, SameSite=Lax, 8h TTL |
| `twaky_oauth` | Short-lived OIDC state | signed, HttpOnly, Secure, SameSite=Lax, 5min TTL |

Both use `SameSite=Lax`; `Strict` would break the top-level GET navigation back from LemonLDAP. Both HttpOnly (JavaScript cannot read them). `Secure` set to `True` because Traefik terminates HTTPS.

Storage: signed cookies via `starlette.middleware.sessions.SessionMiddleware`. No server-side session store (no Redis dep just for sessions).

### 5.5 Owner guard

Every protected route depends on:

```python
async def require_owner(request: Request) -> str:
    session = request.session
    if not session or "email" not in session:
        raise HTTPException(401, "unauthenticated")
    if session["email"] != settings.twaky_owner_email:
        raise HTTPException(403, "not the instance owner")
    return session["email"]
```

Injected as a FastAPI dependency on every non-public endpoint.

### 5.6 CSRF

No explicit CSRF token. Two barriers suffice for a JSON API behind cookie auth:

1. `SameSite=Lax` blocks cross-origin top-level POST navigations.
2. Mutating routes require `Content-Type: application/json`. A cross-origin `<form>` cannot send this header without CORS preflight, which we don't allow.

This is the standard pattern for JSON-only APIs behind cookie auth (Auth.js, Flask-Login, Django REST Framework SessionAuth, Langfuse itself).

### 5.7 Logout

`POST /oauth/logout`:

1. Purge the session cookie.
2. 302 → `<LEMON>/oauth2/logout?post_logout_redirect_uri=https://twaky.twake-dev.maudet.cloud/` — also tears down the LemonLDAP session.

### 5.8 Expiration

- Session cookie TTL = 8 hours. After that, 401 → frontend redirects to `/oauth/login`.
- No refresh-token silent renewal. Users re-authenticate every 8h. Refresh can be added in 3b if UX warrants.

### 5.9 Failure cases

| Case | HTTP | Behaviour |
|---|---|---|
| Missing session cookie | 401 | Frontend redirects to `/oauth/login` |
| Expired session | 401 | Same as missing |
| Valid session, email ≠ owner | 403 | Frontend shows "not the instance owner" |
| LemonLDAP returns `error=access_denied` | 302 → `/?login_error=access_denied` | Frontend shows toast |
| Invalid `id_token` signature | 403 | Log + rejection |
| State mismatch on callback | 400 | Log + generic error page |

### 5.10 Test topology

- Unit tests: `app.dependency_overrides[require_owner] = lambda: "test@x"`.
- Integration tests: a `sign_session(email)` helper forges a valid session cookie without hitting real OIDC. Also exposed under `src/twaky/api/testing.py` for consumption by 3b's Playwright tests.

## 6. SSE event model

### 6.1 Engine extension

Each of the 7 transition functions in `src/twaky/missions/engine.py` emits, in addition to existing NOTIFY channels, a unified `mission_changed` NOTIFY carrying the new state:

```python
# Added after every _transition(...) call:
_notify("mission_changed", json.dumps({
    "mission_id": str(mid),
    "state": new_state.value,
    "at": datetime.now(UTC).isoformat(),
}))
```

Existing channels (`mission_declared`, `mission_resumed`) are retained — the daemon consumes them. `mission_changed` is additive, purely for the frontend.

### 6.2 Wire format

```
event: mission_changed
data: {"mission_id":"a1b2c3","state":"awaiting_user","at":"2026-08-01T18:00:00Z"}

: keep-alive
```

Keep-alive comment every 15 s to defeat proxy idle timeouts.

### 6.3 Broker

```python
class SSEBroker:
    def __init__(self) -> None:
        self.subscribers: dict[UUID, asyncio.Queue[dict]] = {}
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        # spawn _listener() in background
    async def stop(self) -> None:
        # cancel _listener(), drain queues

    def subscribe(self) -> tuple[UUID, asyncio.Queue[dict]]:
        # new Queue(maxsize=100), register, return (uuid, queue)
    def unsubscribe(self, sub_id: UUID) -> None:
        # remove from dict, drop the queue

    async def _listener(self) -> None:
        # dedicated psycopg conn in autocommit
        # LISTEN mission_changed
        # for notify: self._broadcast(json.loads(notify.payload))

    def _broadcast(self, payload: dict) -> None:
        for queue in self.subscribers.values():
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                log.warning("SSE queue full, dropping event", sub_id=...)
```

### 6.4 `/events` handler

```python
@app.get("/events")
async def events(_: str = Depends(require_owner)):
    sub_id, queue = broker.subscribe()
    async def stream():
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"event: mission_changed\ndata: {json.dumps(payload)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            broker.unsubscribe(sub_id)
    return StreamingResponse(stream(), media_type="text/event-stream")
```

### 6.5 Lifecycle

Broker start/stop are wired to the FastAPI app's lifespan:

```python
@app.on_event("startup")
async def _startup() -> None:
    await broker.start()

@app.on_event("shutdown")
async def _shutdown() -> None:
    await broker.stop()
```

### 6.6 Guarantees

1. **Ordered per mission.** NOTIFYs from the same Postgres transaction arrive in order. Two different missions may interleave freely — that's fine.
2. **Best-effort delivery.** A client that disconnects mid-event misses it. A queue overflow (>100 pending events) drops the oldest with a warn log.
3. **No historical replay.** `/events` starts from the moment of connection. The client should call `GET /missions` first for the initial state, then subscribe to `/events` for deltas.
4. **Automatic reconnection.** The browser's `EventSource` reconnects on its own (~3s default). Server may emit `retry: 3000` at the first event.
5. **Post-reconnect resync.** After reconnection, the frontend re-fetches `GET /missions` because states may have changed during the gap. This is standard SSE practice.

We do not add `Last-Event-ID` / durable replay. Postgres NOTIFY is not persistent; adding a `mission_event_log` table + replay endpoint is YAGNI. `GET /missions` is the source of truth.

## 7. Sub-project 2 parked bug (bundled)

**Bug** (from sub-project 2's ledger): `atlas_daemon._run_mission_sync` uses `is_resume = m.state == MissionState.RUNNING`. But `resume_missions_after_restart` returns `("resumed", mid)` for any live mission with a checkpoint, including AWAITING_USER and PLANNING. Those crash into `engine.start_planning(mid)` → `InvalidTransition` → daemon finalises with `reason="atlas_crashed: InvalidTransition"`.

**Why bundle in 3a:**

1. 3a already modifies `engine.py` (adding unified `mission_changed` NOTIFY). Bundling the daemon-side fix keeps the "prep before API" work in one coherent commit set.
2. Once 3b surfaces `awaiting_user` missions in the UI, users will leave them open for hours. Any `docker compose restart twaky-atlas` triggers the bug — it becomes a **user-visible regression**, not just theoretical.
3. The regression tests extend `tests/integration/test_daemon_recovery.py` (created in sub-project 2). Pattern already in place.

**The fix (~20 effective LOC):**

```python
# src/twaky/daemon/atlas_daemon.py
def _run_mission_sync(mid: UUID) -> None:
    m = repository.get(mid)
    ...
    is_resume = m.state in (MissionState.RUNNING, MissionState.AWAITING_USER)

    if m.state == MissionState.PLANNING:
        # A PLANNING mission's checkpoint has no useful LangGraph state
        # (planning hasn't reached graph.invoke yet). Auto-fail with a clear
        # reason rather than start_planning() → InvalidTransition.
        engine.finish(mid, outcome="failed", artifacts=[],
                      reason="checkpoint_lost_during_planning")
        return

    if not is_resume:
        engine.start_planning(mid)
        engine.commit_plan(mid, [PlanStep(...)])
    ...
```

**Regression tests (added to `tests/integration/test_daemon_recovery.py`):**

1. `test_recovery_handles_awaiting_user_mission` — declare + transition to awaiting_user via `engine.request_user_input`. Simulate restart: `_recover_and_schedule` schedules the mission, `_run_mission_sync` takes the `is_resume` branch, mission stays `awaiting_user` (or reaches `done` per scripted LLM).
2. `test_recovery_fails_planning_mission_with_clear_reason` — force a mission to `planning` (via `engine.start_planning` without `commit_plan`). Recovery: mission transitions to `failed` with `reason="checkpoint_lost_during_planning"`, artifacts empty.

**Placement in the plan:** Task 1 of the 3a plan = "engine.py NOTIFY unification + atlas_daemon.py resume-guard broadening + planning-recovery + regression tests". Must run before the SSE broker task since the broker LISTENs on `mission_changed`.

## 8. Testing strategy

### 8.1 Unit (fast, no infra)

- **Route handlers** via FastAPI `TestClient`. `app.dependency_overrides[require_owner] = lambda: "test@x"`. Mock engine + repository calls. Assert HTTP code + response body shape.
- **Owner guard** — missing / expired / wrong-email / correct session paths.
- **SSE broker** — subscribe / broadcast / unsubscribe / queue-full drop with `Queue(maxsize=2)`.
- **OIDC handlers** — mock `authlib` client. Cover: happy path, bad state, email ≠ owner, `id_token` signature failure.
- **OpenAPI schema stability** — `test_openapi_schema_stable.py` compares `app.openapi()` to `docs/api/openapi.yaml`. Fails if drift.

### 8.2 Integration (self-skip when Postgres unreachable)

- **Full stack API + engine + Postgres** — `POST /missions` → 201; `GET /missions` finds it; `POST /missions/{mid}/cancel` → 200 + state `cancelled` in DB; teardown deletes the row.
- **SSE end-to-end** — `asyncio.gather` of an `httpx.AsyncClient` on `/events` with `stream=True` collecting events, and a parallel `POST /missions` + `POST /missions/{mid}/cancel`. Assert at least two `mission_changed` events received (`declared`, `cancelled`) with the correct `mission_id`, in order.
- **Recovery test extension** — the two tests from §7.

### 8.3 E2E scenario (bash)

New script `scripts/scenarios-api.sh` (sibling of `scenarios-foundations.sh` and `scenarios-agents.sh`). Sequence:

1. Wait for `twaky-api` health.
2. Forge a signed session cookie via a Python one-shot helper.
3. `curl -b cookies -X POST /missions -H "Content-Type: application/json" -d '{"intent_text":"..."}'` → capture `mission_id`.
4. `curl -b cookies -N /events > events.log &` in background.
5. `curl -b cookies -X POST /missions/{mid}/cancel -H "Content-Type: application/json" -d '{"reason":"e2e"}'`.
6. Kill the SSE listener.
7. Assert `events.log` contains an event with `state=cancelled` for the created mission.
8. Cleanup the mission.

Wired into `Makefile` as `make scenarios-api`.

### 8.4 Not tested in 3a

- Real LemonLDAP-NG OIDC round-trip — ops-side smoke test, run manually after the deploy client is provisioned.
- Load / perf — mono-owner target, YAGNI.
- Cross-browser SSE — the protocol is standard; `EventSource` works everywhere.

### 8.5 Baseline at end of 3a

- ~150–170 tests total in the repo (up from 132 after sub-project 2).
- ruff / format / mypy still clean.
- CI GitHub Actions green on push + PR.
- New `make scenarios-api` runnable against the live stack.

## 9. Handoff artifacts for sub-project 3b

Prepared during 3a so 3b can start in parallel:

- **`docs/api/openapi.yaml`** — complete OpenAPI 3.1 schema, versioned in the repo. Regenerated by `make openapi`. CI checks for drift. 3b uses it to:
  - Generate a typed TypeScript client (`openapi-typescript-codegen` or `orval`).
  - Spin a local mock server (`npx @stoplight/prism-cli mock docs/api/openapi.yaml`) to develop against a fake backend.
- **README section "Consuming twaky-api"** — documented `curl` examples for `login`, `list`, `declare`, `resume`, `SSE`.
- **`src/twaky/api/testing.py`** — `sign_session(email) -> str` exposed so 3b's Playwright tests can forge a session cookie in CI, bypassing the real OIDC round-trip.
- **`.env.example` updated** with `TWAKY_API_BASE_URL`, `TWAKY_API_OIDC_*`, `TWAKY_API_SESSION_SECRET`. 3b reads `TWAKY_API_BASE_URL` to construct client URLs.

## 10. Rollout

1. **Deploy repo change** — add `twaky-api` OIDC client to `twake_auth/config/lmConf-1.json.ldap.template`. Same mechanism as `twaky-plume` (sub-project 2). Ops: commit + `docker compose up -d twake_auth` on athena, wait ~30 s for LemonLDAP config reload.
2. **`.env` twaky** — generate `TWAKY_API_SESSION_SECRET` (`openssl rand -hex 32`), inject the LemonLDAP `client_secret` into `TWAKY_API_OIDC_CLIENT_SECRET`.
3. **`docker compose up -d twaky-api`** on athena. Wait for healthcheck.
4. **Traefik labels** — added in the compose block: `Host: twaky.twake-dev.maudet.cloud`, TLS via existing cert-resolver.
5. **Browser smoke test** — open `https://twaky.twake-dev.maudet.cloud/oauth/login`, authenticate against LemonLDAP, return to `/me`, verify JSON `{owner_email: "michel.maudet@..."}`.
6. **API smoke test** — extract `twaky_session` cookie from browser DevTools (or generate it on athena via `docker compose exec twaky-api python -c "from twaky.api.testing import sign_session; print(sign_session('michel.maudet@linagora.com'))"`), then `curl -b "twaky_session=<value>" https://twaky.twake-dev.maudet.cloud/missions` → JSON list.
7. **SSE smoke test** — same cookie, `curl -b "twaky_session=<value>" -N https://twaky.twake-dev.maudet.cloud/events` in one terminal, `docker compose exec twaky-atlas twaky mission declare "test 3a"` in another, event must arrive within ~1 s.

## 11. Rollback

All additive. In case of trouble:

```bash
docker compose stop twaky-api && docker compose rm -f twaky-api
```

- The new `NOTIFY mission_changed` in `engine.py` stays — it's fire-and-forget (`_notify` swallows), no consumer means no impact.
- The `is_resume` guard broadening in `atlas_daemon.py` is a strict expansion of accepted states — no regression on existing behaviour.
- If the parked bug fix itself misbehaves, reverting the merge also loses the fix — accepted trade-off given the bundling rationale in §7.
- Deploy repo: remove the `twaky-api` client from LemonLDAP as a last resort.

## 12. Open questions

To resolve during implementation, not now:

1. **Session secret rotation** — invalidates all sessions on rotation. Documented in the README as a rare ops action. No key ring for MVP.
2. **Traefik idle timeout vs SSE keep-alive (15 s)** — likely compatible (Traefik default = 90 s). Verify via `docker exec traefik cat /etc/traefik/traefik.yml | grep idle` before enabling.
3. **Rate limiting `/oauth/login`** — YAGNI for mono-owner. If ever opened to multi-user, add a token bucket 1 rps burst 10 in front of OAuth routes.
4. **Silent refresh via refresh token** — YAGNI MVP. Add in 3b if 8h re-login proves annoying.
5. **`GET /missions/{mid}/trace` payload** — chose 302 direct redirect (browser-friendly). Could add a JSON variant later if a headless client needs it.
6. **PLANNING recovery reason string** — `checkpoint_lost_during_planning` is descriptive but long. Bikeshed during implementation.

## 13. Sub-projects that will build on this

- **Sub-project 3b — Frontend Control Tower** — consumes `docs/api/openapi.yaml`, uses `sign_session()` in tests, builds against `TWAKY_API_BASE_URL`. Three-page MVP: mission list, mission detail, resume-with-approval form.
- **Sub-project 4 — Federation** — adds a `POST /messages` endpoint for peer-instance envelope delivery. Uses the same OIDC session pattern (or bearer M2M — TBD in 4's brainstorm). Adds a `mission_message` channel for federation events.
- **Sub-project 5 — Write-side** — adds mutating routes (`POST /missions/{mid}/artifacts/{aid}/send` for Plume, `POST /missions/{mid}/artifacts/{aid}/create` for Chronos calendar events). Reuses 3a's auth + session model unchanged.
