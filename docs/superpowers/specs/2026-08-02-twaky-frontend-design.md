# Twaky Frontend — Design (Sub-project 3b of 7)

**Status:** draft, awaiting user review
**Date:** 2026-08-02
**Owner:** mmaudet
**Related:** builds on Foundations (sub-project 1, merged as `fe838f6`), Agents + Atlas (sub-project 2, merged as `dbd2e62` + `3017dac`), and Twaky API (sub-project 3a, merged as `b9a3fff`). Consumes the OpenAPI schema at `docs/api/openapi.yaml` and the `sign_session()` public seam at `src/twaky/api/testing.py`.

**Roadmap update:** during 3b brainstorming, the user surfaced an "Agent Studio" vision (personalisation d'agents via UI, agent store, skill/connector store). Deferred out of 3b MVP scope by explicit user decision. Captured in auto-memory `twaky_agent_studio_vision.md` for future sub-projects 4 (Agent Lifecycle) + 5 (Skill Store). Federation and write-side become sub-projects 6 and 7 as a consequence.

---

## 1. Purpose

Ship `twaky-frontend`, a Next.js 15 container that offers a web UI to the instance owner for three concrete tasks:

1. **Monitor missions live** — dashboard reflecting real-time state transitions via SSE.
2. **Approve or reject drafts** — interactive form on `awaiting_user` missions (currently `approve_draft` from Plume; future `kind`s handled via generic JSON fallback).
3. **Drill down on failed missions** — detail view with state timeline, artifacts, link to Langfuse trace.

The frontend also fronts all inbound traffic: Traefik routes `twaky.${BASE_DOMAIN}` to `twaky-frontend`, which proxies `/api/*` and `/oauth/*` to `twaky-api` over `twake-network`. Single origin — session cookie works transparently, no CORS.

## 2. Non-goals

- No new API endpoints (all in 3a). The `/api/agents` READ-ONLY endpoint originally proposed as a concession was **rejected by the user** during brainstorming.
- No agent lifecycle CRUD (prompt / temperature / model editing) — sub-project 4.
- No skill / connector store — sub-project 5.
- No federation UI — sub-project 6.
- No write-side (draft-and-send mail, calendar create) — sub-project 7.
- No multi-user, no user management, no permissions system.
- No PWA / offline / native mobile — desktop browser MVP.
- No public landing page — root `/` is protected; unauthenticated requests redirect to `/api/oauth/login`.
- No "wow factor" branding — utilitarian focus per user's stated use case (personal daily driver, not a demo surface).

## 3. Architecture

```
twake-network (external, 172.27.0.0/16)

  ┌────────────────────────────────────────────────────────────────────┐
  │                                                                    │
  │   traefik ── labels ──► twaky-frontend  (next start on :3000)      │
  │                             │                                      │
  │   https://twaky.twake-dev.maudet.cloud                             │
  │                             │                                      │
  │                             ▼                                      │
  │   ┌──────────────────────────────────────────────────────┐         │
  │   │  Next.js 15 App Router (twaky-frontend)              │         │
  │   │                                                      │         │
  │   │   Middleware (edge):                                 │         │
  │   │     • auth guard : lit twaky_session cookie,         │         │
  │   │       si absent → 302 /api/oauth/login?return_to=... │         │
  │   │     • laisse passer /api/* et /oauth/* (proxifiés)   │         │
  │   │                                                      │         │
  │   │   Rewrites (next.config.js) :                        │         │
  │   │     /api/:path*   → http://twaky-api:8000/:path*     │         │
  │   │     /oauth/:path* → http://twaky-api:8000/oauth/:path*│        │
  │   │                                                      │         │
  │   │   Routes App Router :                                │         │
  │   │     /                  → dashboard missions live     │         │
  │   │     /missions/[id]     → détail + resume form        │         │
  │   │     /me                → profil + logout             │         │
  │   │     /stats             → 7 compteurs + failures      │         │
  │   │                                                      │         │
  │   │   Global providers (root layout, client-side) :      │         │
  │   │     • QueryClientProvider (TanStack Query)           │         │
  │   │     • SSEProvider (une seule EventSource sur         │         │
  │   │       /api/events, dispatch → queryClient invalidate)│         │
  │   │     • Toaster (shadcn/ui sonner) pour erreurs        │         │
  │   └──────────────┬───────────────────────────────────────┘         │
  │                  │                                                  │
  │                  ▼ (Next.js internal HTTP, twake-network)          │
  │   ┌──────────────────────────────────────────────────────┐         │
  │   │  twaky-api (from sub-project 3a — unchanged)         │         │
  │   │    - /oauth/{login,callback,logout}                  │         │
  │   │    - /missions/*                                     │         │
  │   │    - /events (SSE)                                   │         │
  │   │    - /me                                             │         │
  │   │    - /healthz                                        │         │
  │   └──────────────────────────────────────────────────────┘         │
  └────────────────────────────────────────────────────────────────────┘
```

**Container `twaky-frontend`** (new compose service):

- Image: multistage build (`node:22-alpine` builder → `node:22-alpine` runtime with `output: "standalone"`), ~50MB.
- `command: ["node", "server.js"]` (file emitted by `output: standalone`).
- Port 3000 internal; no host port published.
- Healthcheck: `wget -qO- http://localhost:3000/api/healthz` — proxies to `twaky-api /healthz`, so healthy only when both are up (correct signal for the entry container).
- `depends_on: twaky-api { condition: service_healthy }`.
- Traefik labels: `Host(\`twaky.${BASE_DOMAIN}\`)` — transferred from `twaky-api` (which loses its external labels).

**Traefik migration** — in the same compose PR, remove `traefik.enable=true` and `Host(...)` labels from `twaky-api` (or set `traefik.enable=false`). `twaky-api` remains reachable internally at `http://twaky-api:8000` for Next.js rewrites. The Atlas daemon never talked to the API externally, so no impact.

**Two new env vars on `twaky-frontend`:**

| Env var | Purpose |
|---|---|
| `API_INTERNAL_URL` | `http://twaky-api:8000` — used by Next.js rewrites. |
| `NEXT_PUBLIC_APP_NAME` | `Twaky` — displayed in the header. `NEXT_PUBLIC_` prefix means it's inlined at build time and available in the browser. |

## 4. Auth flow

**Principle:** no login page in the frontend. All protected routes accessed without a session cookie return a 302 to `/api/oauth/login`, which is proxied to the API from 3a which handles the full OIDC dance. The frontend never sees the cookie value (HttpOnly).

**First-visit flow:**

```
Browser → GET /
Next.js middleware:
    ├─ no `twaky_session` cookie
    └─ 302 → /api/oauth/login?return_to=/

Browser → GET /api/oauth/login?return_to=/
Next.js rewrites → twaky-api /oauth/login
twaky-api → 302 → LemonLDAP-NG /oauth2/authorize?...

Browser → LemonLDAP authenticates user

LemonLDAP → 302 → https://twaky.${BASE_DOMAIN}/oauth/callback?code=...
Next.js rewrites → twaky-api /oauth/callback
twaky-api:
    ├─ verifies code + state + id_token
    ├─ if email != twaky_owner_email → 403
    ├─ Set-Cookie: twaky_session=... (HttpOnly, Secure, SameSite=Lax, 8h)
    └─ 302 → / (sanitized return_to)

Browser → GET / (with cookie)
Next.js middleware:
    ├─ cookie present → pass
    └─ dashboard renders
```

**Middleware (`middleware.ts`), ~20 LOC:**

```typescript
import { NextRequest, NextResponse } from 'next/server'

export function middleware(req: NextRequest) {
    const { pathname } = req.nextUrl
    if (pathname.startsWith('/api/') || pathname.startsWith('/oauth/')) {
        return NextResponse.next()
    }
    if (req.cookies.has('twaky_session')) {
        return NextResponse.next()
    }
    const returnTo = pathname.startsWith('/') && !pathname.startsWith('//')
        ? pathname : '/'
    const loginUrl = new URL(
        `/api/oauth/login?return_to=${encodeURIComponent(returnTo)}`,
        req.url,
    )
    return NextResponse.redirect(loginUrl)
}

export const config = {
    matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
}
```

**Two-layer defense against stale cookies:**

1. **Middleware** — presence check only (99% of requests, fast path). Cookie absent → login redirect.
2. **Client-side** — `GET /api/me` returns 401 on invalid/expired cookie → global `onError` handler in TanStack Query does `window.location.href = /api/oauth/login?return_to=...` and shows a "Session expired" toast.

Without the second layer, a client with an expired cookie server-side but still present browser-side would see 401s on every subsequent call with no useful UX.

**Logout:** header dropdown "Sign out" → `POST /api/oauth/logout`. API clears cookie + 302 to LemonLDAP end-session. Browser follows, returns to `/`, middleware sees no cookie → login redirect.

**Failure cases:**

| Case | Where caught | UX |
|---|---|---|
| No cookie | Middleware | Immediate 302 to `/oauth/login` |
| Cookie expired | `GET /api/me` → 401 | "Session expired" toast + redirect |
| Cookie valid, email ≠ owner | `GET /api/me` → 403 | Full-page error "Not the instance owner" (fatal, no retry) |
| LemonLDAP `error=access_denied` | 3a API returns 400 (spec §5.9 drift — deferred minor) | Generic 500 error page in frontend (post-MVP: pretty error page) |
| `twaky-api` down | Next.js rewrite → 502 | "API unavailable, retrying..." toast + auto-retry via TanStack Query |

## 5. Data flow

**End-to-end type chain:**

```
docs/api/openapi.yaml (source of truth, versioned in the repo)
        │
        │ npx openapi-typescript docs/api/openapi.yaml -o src/lib/api-types.d.ts
        │ (Makefile target `make api-types`, run at build + in CI)
        ▼
src/lib/api-types.d.ts (strict TypeScript types derived from OpenAPI)
        │
        │ import { paths } from '@/lib/api-types'
        │ const client = createClient<paths>({ baseUrl: '/api' })
        ▼
src/lib/api.ts (typed openapi-fetch singleton client)
        │
        │ wrapped in TanStack Query hooks
        ▼
src/hooks/use-missions.ts   │  src/hooks/use-mission.ts
src/hooks/use-declare.ts    │  src/hooks/use-resume.ts
src/hooks/use-cancel.ts     │  src/hooks/use-me.ts
        │
        │ consumed by React components
        ▼
Pages + components (app/page.tsx, app/missions/[id]/page.tsx, ...)
```

If the API adds a field to `Mission`, the frontend fails to compile until `api-types.d.ts` is regenerated AND the new field is handled. Zero drift possible.

**TanStack Query hooks shape:**

```typescript
// src/hooks/use-missions.ts
export function useMissions(state?: MissionState) {
    return useQuery({
        queryKey: ['missions', { state }],
        queryFn: async () => {
            const { data, error } = await api.GET('/missions', {
                params: { query: { state } },
            })
            if (error) throw new ApiError(error)
            return data
        },
        // No staleTime — SSE-driven invalidation triggers refetches.
    })
}

// src/hooks/use-resume.ts
export function useResumeMission() {
    const qc = useQueryClient()
    return useMutation({
        mutationFn: async ({ id, userResponse }: {
            id: string, userResponse: unknown
        }) => {
            const { data, error } = await api.POST('/missions/{mid}/resume', {
                params: { path: { mid: id } },
                body: { user_response: userResponse },
            })
            if (error) throw new ApiError(error)
            return data
        },
        onSuccess: (_, { id }) => {
            qc.invalidateQueries({ queryKey: ['mission', id] })
            qc.invalidateQueries({ queryKey: ['missions'] })
        },
    })
}
```

**SSE integration — single global EventSource:**

```typescript
// src/components/sse-provider.tsx
export function SSEProvider({ children }: { children: React.ReactNode }) {
    const qc = useQueryClient()

    useEffect(() => {
        const es = new EventSource('/api/events')

        es.addEventListener('mission_changed', (evt) => {
            const payload = JSON.parse(evt.data) as {
                mission_id: string, state: string, at: string
            }
            qc.invalidateQueries({ queryKey: ['mission', payload.mission_id] })
            qc.invalidateQueries({ queryKey: ['missions'] })
        })

        es.onerror = () => {
            // EventSource auto-reconnects. On reconnect, refetch to fill any gap.
            qc.invalidateQueries({ queryKey: ['missions'] })
        }

        return () => es.close()
    }, [qc])

    return <>{children}</>
}
```

One connection per browser session, mounted at the root layout. Pattern: "SSE tells us what changed, we refetch". No optimistic patching, no per-component sockets. Simple, robust, works out-of-the-box with TanStack Query.

**Global error handling:**

```typescript
// src/lib/api-error.ts
export class ApiError extends Error {
    constructor(public envelope: {
        error: { code: string, message: string, detail?: unknown }
    }) {
        super(envelope.error.message)
    }
}

// QueryClient config (root)
const queryClient = new QueryClient({
    defaultOptions: {
        queries: {
            retry: (failureCount, error) => {
                if (error instanceof ApiError) {
                    const code = error.envelope.error.code
                    if (['http_401', 'http_403', 'http_404'].includes(code)) return false
                }
                return failureCount < 2
            },
        },
        mutations: {
            onError: (error) => {
                if (error instanceof ApiError && error.envelope.error.code === 'http_401') {
                    window.location.href = `/api/oauth/login?return_to=${encodeURIComponent(window.location.pathname)}`
                    return
                }
                toast.error(error.message)
            },
        },
    },
})
```

**Type regeneration workflow:**

- API changes → `make openapi` (in the API repo, already in place from 3a) regenerates `docs/api/openapi.yaml`.
- Frontend picks up via `make api-types` → regenerates `src/lib/api-types.d.ts`. Committed.
- CI check on the frontend: `make api-types && git diff --exit-code src/lib/api-types.d.ts` — fails if types are stale.
- Two-sided guard: API drift OR frontend drift blocks CI.

**Not in the MVP:**

- Optimistic updates on mutations. Simple invalidate-and-refetch pattern.
- Prefetch on hover. Nice-to-have.
- Infinite scroll on the mission list. `limit=500` bound from 3a is enough for mono-user.
- Suspense boundaries. Marginal gain for single-page-load.

## 6. Pages

### Layout (`app/layout.tsx`)

```
┌─────────────────────────────────────────────────────────────┐
│  Twaky  ▸ Dashboard  ▸ Stats            [alice@x ▾]  ⚙     │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│                  (page content here)                         │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

- Persistent header (top-nav): logo/name "Twaky" left, breadcrumb middle (always show Dashboard link + Stats), rightmost dropdown `[alice@x ▾]` with two items — "Profile" (→ /me) and "Sign out" (POST /api/oauth/logout).
- Email badge from `useMe()`, cached for the session.
- Icon `⚙` = SSE connection indicator (green = connected, orange = reconnecting, gray = disconnected). Small colored dot, hover-title for detail. Important feedback when live updates stop.
- Providers in this layout, in order: `QueryClientProvider`, `SSEProvider`, `Toaster` (sonner).

### `/` — Dashboard missions

```
┌───────────────────────────────────────────────────────────────┐
│  Missions                                    [+ New mission]   │
├───────────────────────────────────────────────────────────────┤
│  State filter: [ All ] [Live] [Done] [Failed] [Cancelled]     │
├───────────────────────────────────────────────────────────────┤
│  ● running       Résume ma journée de demain    2m ago         │
│  ⚑ awaiting_user Draft a reply to demo-msg-1    5m ago  →      │
│  ● running       Find flights to LON            8m ago         │
│  ✓ done          Weekly summary                 1h ago         │
│  ✗ failed        Broken intent                  2h ago         │
│  (…)                                                            │
└───────────────────────────────────────────────────────────────┘
```

- Main component: `<MissionList missions={data} />`. shadcn `<Table>` with 4 columns: state (badge + icon), intent_text (truncated 60 chars, tooltip on hover), relative timestamp, chevron.
- State filter: `<ToggleGroup>`. Default "Live" (non-terminal states: declared/planning/running/awaiting_user). "Done"/"Failed"/"Cancelled" are exact filters. "All" is no filter (paginated at limit=500).
- `[+ New mission]`: opens a `<Dialog>` with `<Textarea>` for `intent_text` (max 4096 chars matching API bound). Submit → `useDeclareMission()`. SSE invalidates the list; new mission appears at top.
- Highlight awaiting_user rows: pale yellow background + ⚑ icon. Visual signal that action is needed.
- Row click → `router.push(\`/missions/${mission.id}\`)`.
- Hook: `useMissions(stateFilter)` with queryKey `['missions', {state}]`. Refetch on SSE.
- Empty state: "No missions yet. Click + to declare one."

### `/missions/[id]` — Detail

Vertical layout, three stacked sections:

```
┌───────────────────────────────────────────────────────────────┐
│  ← Back to missions                                            │
│                                                                │
│  ⚑ Draft a reply to demo-msg-1                                 │
│  awaiting_user · declared 5m ago · alice@x                     │
│  [Cancel mission]                             [Open in Langfuse]│
├───────────────────────────────────────────────────────────────┤
│  State timeline                                                │
│  ●─●─●─●                                                       │
│  declared  planning  running  awaiting_user                    │
│  10:00     10:00     10:01    10:05                            │
├───────────────────────────────────────────────────────────────┤
│  Artifacts (3)                                                 │
│  ▸ user_response          {"approved": true, ...}    10:07    │
│  ▾ approve_draft          Drafted reply to bob@x     10:05    │
│      To: bob@x.com                                             │
│      Subject: Re: Question about widgets                       │
│      Body:                                                     │
│        Hi Bob — thanks for reaching out! Your question...      │
│  ▸ draft_source           Fetched original email     10:03    │
├───────────────────────────────────────────────────────────────┤
│  ┌─── Action required ────────────────────────────────────┐   │
│  │  Approve draft                                           │   │
│  │  ┌───────────────────────────────────────────────────┐  │   │
│  │  │  Hi Bob — thanks for reaching out!                │  │   │
│  │  │  Your question about widgets is a great one...    │  │   │
│  │  └───────────────────────────────────────────────────┘  │   │
│  │              [ Cancel mission ]  [ Approve → ]           │   │
│  └──────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────┘
```

- **Header**: intent_text (h1), state badge + relative timestamp + declared_by. Right side: `[Cancel mission]` (danger, opens confirm dialog with reason textarea) and `[Open in Langfuse]` (link to `/api/missions/{id}/trace`, `target="_blank"`).
- **State timeline**: 4-7 dots for reached states, timestamps below. Debug aid.
- **Artifacts**: shadcn `<Accordion>`. Header: `kind` badge + one-line summary + timestamp. Content: `<pre>{JSON.stringify(artifact.payload, null, 2)}</pre>` with Shiki syntax highlighting (SSR, zero client JS shipped).
- **Action required** section: visible only when `state === "awaiting_user"`. Component `<ResumeForm mission={mission} />` inspects the last artifact matching a pending_user_input kind and dispatches:

  - **`kind === "approve_draft"`** → `<ApproveDraftForm artifact={artifact}>`:
    - Read-only To / Subject.
    - Editable `<Textarea>` (rows=15) prefilled with `artifact.draft`.
    - `[Cancel mission]` (destructive, confirm dialog) and `[Approve →]` (primary).
    - Approve → `useResumeMission({id, userResponse: {approved: true, draft: editedText}})`. Stay on page — SSE will drive the state change.
    - Cancel mission → `useCancelMission({id, reason: "user_rejected_draft"})`.

  - **Unknown kind** → `<GenericResumeForm artifact={artifact}>`:
    - Info banner: "This mission requires input of type `<kind>`. Advanced: submit a JSON payload."
    - `<Textarea>` with `defaultValue='{"approved": true}'`, client-side JSON validation.
    - `[Cancel mission]` and `[Submit →]`.

- Hook: `useMission(id)`, refetches via SSE. Toast notification when state changes on the page (e.g., "Started planning" → "Running" → "Done").

### `/me` — Profile

```
┌───────────────────────────────────────────────┐
│         Signed in as                           │
│      alice@twake-dev.maudet.cloud              │
│                                                │
│  Session expires in 6h 42m                     │
│  Langfuse: langfuse.twake-dev.maudet.cloud →   │
│                                                │
│              [ Sign out ]                      │
└───────────────────────────────────────────────┘
```

- shadcn `<Card>` centered (~500px wide).
- Email from `useMe()`.
- Session expiry: computed client-side from a login timestamp stored at page load + 8h TTL, or approximated from cookie's Expires attribute if extractable.
- Langfuse link: `me.langfuse_base_url`, external (`target=_blank`).
- `[Sign out]`: `POST /api/oauth/logout`, browser follows 302 to LemonLDAP end-session, returns to `/` cookieless, middleware → login redirect.

### `/stats` — Counters

```
┌───────────────────────────────────────────────────────────────┐
│  Stats                                                          │
├───────────────────────────────────────────────────────────────┤
│  State breakdown                                                │
│  declared: 2   planning: 0   running: 3   awaiting_user: 1     │
│  done: 42      failed: 5     cancelled: 3                       │
│  Total live: 6 · Total terminal: 50                             │
├───────────────────────────────────────────────────────────────┤
│  Recent failures (5)                                            │
│  ✗ failed  Retry after transient error   12m ago  →            │
│  ✗ failed  atlas_crashed: BadRequest     1h ago   →            │
│  ✗ failed  step_limit_exceeded           3h ago   →            │
│  (…)                                                            │
└───────────────────────────────────────────────────────────────┘
```

- Data source: `useMissions()` with `limit=500` (all recent missions). Aggregated client-side.
- **Breakdown**: 7 cells (one per state), flex grid.
- **Total live vs terminal** ratio line below.
- **Recent failures**: 5 most recent `state === "failed"` missions, sorted desc, each row clickable to detail.
- Refetch via SSE; counters and failures recompute automatically.

**Global empty state:** first-run, no missions → all pages show "No missions yet. Declare one from the dashboard." with a button navigating to `/`.

**Responsive:** desktop first. Mobile (< 768px): header collapses to `<Sheet>` (drawer), dashboard table becomes stacked cards. Functional but not a priority.

## 7. Testing strategy

Three levels + a dedicated CI job. Target: ~50-70 total tests, vs. 192 on the Python side.

### Level 1 — Unit (Vitest + React Testing Library)

- **Hooks with logic**: `useResumeMission` (input mapping, invalidation call), `useMissions` (filter param passthrough), global error handler (ApiError envelope → toast/redirect mapping).
- **Components with business logic**:
  - `<ApproveDraftForm>` — textarea prefilled, edit, click Approve calls `useResumeMission` with `{approved: true, draft: <edited>}`.
  - `<GenericResumeForm>` — JSON validation, malformed input → error shown, no submit.
  - `<StateBadge state="running" />` — color + icon per state (7-state parameterised).
  - `<RelativeTime timestamp="..." />` — "5s ago", "2m ago", "1h ago", "yesterday".
- **Utility functions**:
  - `formatSessionExpiry(cookie)` — extract Expires attribute, compute human-readable delta.
  - `sanitizeReturnTo(input)` — client mirror of API's `_safe_return_to`.
  - `computeStateBreakdown(missions)` — reduce list → 7 counters (for `/stats`).

**Not tested at unit level:**
- Pure display components with no state.
- shadcn/ui wrappers.
- Middleware `/api/*` passthrough (trivial, tested end-to-end).
- Styling.

### Level 2 — Component tests with mocked API (Vitest + RTL + MSW)

`msw` intercepts fetch calls in the browser test env. Full page rendering with faked data, no backend stack required:

- **Dashboard**: mock `GET /api/missions` → 5 missions in different states. Assert 5 rows rendered, ToggleGroup filters subset correctly, row click triggers `router.push`.
- **Mission detail**: mock `GET /api/missions/{id}` → `awaiting_user` mission with `approve_draft` artifact. Assert `<ApproveDraftForm>` mounted with correct draft. Mock `POST .../resume` → assert submit fires it.
- **Header dropdown**: mock `GET /api/me` → email displayed, "Sign out" fires POST to `/api/oauth/logout`.

MSW setup in `test-setup.ts`; tests configure per-scenario handlers.

### Level 3 — E2E Playwright (against real stack)

**Setup fixtures:**

```typescript
// playwright.config.ts
export default defineConfig({
    webServer: [
        { command: 'docker compose up twaky-frontend', url: 'http://localhost:3000' },
    ],
    use: { baseURL: 'http://localhost:3000' },
})

// tests/e2e/fixtures.ts
export const test = base.extend<{ signedInPage: Page }>({
    signedInPage: async ({ page, context }, use) => {
        const cookie = execSync(
            'docker compose exec -T twaky-api uv run python scripts/sign-session.py alice@x'
        ).toString().trim()
        await context.addCookies([{
            name: 'twaky_session', value: cookie,
            domain: 'localhost', path: '/',
        }])
        await use(page)
    },
})
```

**MVP scenarios** (each ~30-60s, ~5min budget):

1. `test('unauthenticated user is redirected to login')` — visit `/`, expect 302 to `/api/oauth/login`.
2. `test('signed-in user sees dashboard')` — with `signedInPage`, visit `/`, expect header email + empty state or mission rows.
3. `test('declare → detail → cancel')` — click `[+ New mission]`, type intent, submit, wait for SSE-driven row appearance, click, expect detail, click `[Cancel mission]`, confirm, back to dashboard, mission gone from live filter.
4. `test('approve draft awaiting_user')` — a test-only Python helper `tests/e2e/seed-awaiting-user.py` declares a mission + walks it to `awaiting_user` via `engine.declare` / `start_planning` / `commit_plan` / `request_user_input(kind="approve_draft", artifact={...})`, WITHOUT touching the Atlas daemon (deterministic). Playwright then: navigates to `/missions/{id}`, sees `<ApproveDraftForm>`, edits the draft, clicks Approve, expects toast + state transitions to "running" then "done" (or stays "running" if no daemon completes it — accept either).
5. `test('sign out flow')` — with `signedInPage`, click header dropdown → "Sign out", expect redirect (LemonLDAP end-session URL if reachable in test env, or `/` otherwise), verify next visit triggers login.

**Self-skip patterns:**
- `TWAKY_TEST_STACK_URL` unset → skip all E2E. Mirror of Python's `_reachable()` pattern.
- seed-awaiting-user helper skips if twaky-pg unreachable.

### Level 4 — CI job

Two new jobs in `.github/workflows/ci.yml`:

```yaml
frontend:
    runs-on: ubuntu-latest
    steps:
        - uses: actions/checkout@v4
        - uses: actions/setup-node@v4
          with: { node-version: '22', cache: 'npm', cache-dependency-path: 'frontend/package-lock.json' }
        - run: cd frontend && npm ci
        - run: cd frontend && npm run typecheck
        - run: cd frontend && npm run lint
        - run: cd frontend && npm run test:unit
        - run: cd frontend && npm run build
        - name: Regen check
          run: cd frontend && make api-types && git diff --exit-code src/lib/api-types.d.ts

frontend-e2e:
    runs-on: ubuntu-latest
    needs: [frontend]
    steps:
        - run: docker compose up -d twaky-pg twaky-api twaky-frontend
        - run: cd frontend && npm ci && npx playwright install --with-deps
        - run: cd frontend && TWAKY_TEST_STACK_URL=http://localhost:3000 npm run test:e2e
```

`frontend-e2e` can be labelled `e2e` for on-demand invocation if CI time becomes a concern.

### What we don't test in 3b

- The real OIDC round-trip against LemonLDAP-NG (ops smoke test after client provisioning, README-documented).
- Cross-browser (Chromium only). Desktop Chrome/Firefox users at 95%+.
- Accessibility (no axe/pa11y audit automated). Add when a user with specific needs surfaces.
- Perf / Lighthouse.

### Baseline after 3b

- Python: 192 passed / 32 skipped (unchanged unless a follow-up API tweak is needed).
- Frontend: ~50 unit + ~10 component + ~5 E2E.
- CI `frontend` job < 3min, `frontend-e2e` < 10min.
- End-to-end type safety: impossible to ship a frontend call to a non-existent endpoint or a wrong shape.

## 8. Rollout

1. **`twaky-api` OIDC client provisioning** — prerequisite. Add `twaky-api` OIDC client to `twake_auth/config/lmConf-1.json.ldap.template` in the deploy repo (`~/deploy/kickstart-maudet-cloud`). Same mechanism used for `twaky-langfuse`, `twaky-plume`. Redirect URI `https://twaky.twake-dev.maudet.cloud/oauth/callback`, scopes `openid email profile`, PKCE enabled.
2. **`.env` twaky (already done via 3a rollout, verify):** `API_SESSION_SECRET`, `API_OIDC_CLIENT_ID=twaky-api`, `API_OIDC_CLIENT_SECRET`, `API_OIDC_ISSUER`, `API_BASE_URL=https://twaky.${BASE_DOMAIN}`.
3. **Build:** `docker compose build twaky-frontend` on athena.
4. **Traefik migration** — edit `docker-compose.yml`: remove or comment the Traefik `Host(...)` labels on `twaky-api`, add them on `twaky-frontend`. `docker compose up -d twaky-api twaky-frontend`. Traefik detects the change in ~10s.
5. **Browser smoke test** — open `https://twaky.${BASE_DOMAIN}/`. Expect redirect → `/api/oauth/login` → LemonLDAP → callback → dashboard.
6. **SSE verify** — DevTools → Network → `/api/events` shows `EventStream`. Declare a mission from the CLI (`twaky mission declare "test"`), event arrives within ~1s.

## 9. Rollback

All additive:

```bash
# 1. Restore Traefik on twaky-api (remove frontend labels, restore api labels)
docker compose up -d twaky-api

# 2. Stop the frontend
docker compose stop twaky-frontend && docker compose rm -f twaky-frontend

# 3. Revert the merge
git revert <merge-commit>
```

No schema changes, no API changes. Pure infrastructure + frontend code.

**Incremental fallback:** if `twaky-frontend` has a blocking bug post-swap, revert Traefik labels only (30 seconds). CLI + `curl` with forged cookie remain available (documented in 3a README).

## 10. Open questions

To resolve during implementation:

1. **LemonLDAP client `twaky-api` still not provisioned** in the deploy repo (blocker for real MVP). Options: (a) ops-only PR against `deploy/kickstart-maudet-cloud` before merging 3b — recommended, matches the pattern for `twaky-plume` and `twaky-langfuse` from prior sub-projects; (b) fold the change into T1 of the 3b plan — feasible but bloats the plan and requires cross-repo commit. Choose (a).

2. **`docs/api/openapi.yaml` /events endpoint accuracy** — FastAPI often mis-represents SSE in OpenAPI (defaults content-type to `application/json` — already noted as a deferred minor from 3a's final review). At the first `npx openapi-typescript` run, either annotate the FastAPI route with `responses={200: {"content": {"text/event-stream": {}}}}` (fixes both the OpenAPI and the frontend's understanding), or hand-write the SSE event type in `src/lib/sse-types.d.ts`. Prefer the FastAPI annotation fix (small 3a-follow-up commit) — cleaner.

3. **Artifact JSON display** — `<pre>{JSON.stringify(x, null, 2)}</pre>` + Shiki (server-side syntax highlighting, zero client JS) for MVP. If artifacts grow large or deeply nested, migrate to `react-json-view` post-MVP.

4. **Font stack + design polish** — utilitarian but not ugly. Recommendation: Inter for text, JetBrains Mono for JSON artifacts. Dark mode via `prefers-color-scheme` (no manual toggle in MVP).

5. **EventSource limitation** — no custom headers. Fine for MVP (cookie session works). If future auth changes to bearer tokens, migrate to `@microsoft/fetch-event-source`. No change needed for 3b.

6. **`state_reason` field display** — visible in mission detail as a "Terminal reason" section, only when `state` is terminal and `state_reason` is non-null. Trivial addition.

## 11. Handoff artifacts for sub-project 4 (Agent Lifecycle)

What 3b establishes that 4 will consume without redesign:

- **`app/layout.tsx`** — shared shell with header + providers. Sub-project 4 adds routes; no shell change needed.
- **`src/lib/api.ts`** + `make api-types` — typed openapi-fetch client. Sub-project 4 regenerates when it adds `/api/agents/*` endpoints.
- **TanStack Query hooks pattern** — sub-project 4 copies-and-adapts for `useAgents()`, `useAgent(id)`, `useUpdateAgent()`.
- **`<SSEProvider>`** — sub-project 4 reuses; may extend with an `agent_config_changed` event handler if live-reload becomes a requirement.
- **Playwright `signedInPage` fixture** — sub-project 4 reuses unchanged.
- **shadcn/ui theme + component defaults** — sub-project 4 gets a design language for free.

## 12. Rollout of the roadmap (updated)

Consigned in auto-memory `twaky_agent_studio_vision.md`:

- **Sub-project 4 = Agent Lifecycle Management** — prompt / temperature / model editing per agent via UI, backend refactor (config as data). ~25-30 tasks.
- **Sub-project 5 = Skill / Connector Store** — user-defined skills, marketplace, MCP integration. ~30-40 tasks. Biggest security surface.
- **Sub-project 6 = Federation** (ex-4) — deferred.
- **Sub-project 7 = Write-side** (ex-5) — deferred.
