# Twaky Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `twaky-frontend`, a Next.js 15 container that exposes a Web UI to the instance owner for 3 use cases: monitor missions live via SSE, approve/reject drafts on `awaiting_user` missions, and drill down on failed missions.

**Architecture:** New `frontend/` subdirectory at the repo root — a standalone Next.js 15 App Router application. Traefik routes `twaky.${BASE_DOMAIN}` to `twaky-frontend`; Next.js `rewrites()` proxy `/api/*` and `/oauth/*` to `twaky-api` over `twake-network`. Single origin → cookie session works without CORS. Auth via edge middleware presence-check + client-side 401 fallback. Data via `openapi-fetch` (generated from `docs/api/openapi.yaml`) wrapped in TanStack Query hooks. Live updates via a single global `EventSource` at the root layout, invalidating relevant queries on `mission_changed` events. Tests: Vitest+RTL+MSW at unit/component level, Playwright at E2E level using the `sign_session()` seam from 3a.

**Tech Stack:** Node 22, npm, TypeScript 5, Next.js 15 App Router, React 19, Tailwind CSS 4, shadcn/ui components (copied into the repo — not a runtime dep), `@tanstack/react-query` 5, `openapi-fetch` 0.13 + `openapi-typescript` 7 (dev only, for type generation), `sonner` (toasts, via shadcn), `shiki` (SSR JSON syntax highlighting). Tests: Vitest 2 + `@testing-library/react` + `msw` 2 + Playwright 1. All MIT except Playwright (Apache 2.0). No GPL/AGPL deps.

## Global Constraints

- **`frontend/` subdirectory at the repo root**, NOT a separate repo. All frontend files live under `frontend/`. Python code stays under `src/twaky/`.
- **Session cookie name is exactly `twaky_session`** (matches 3a's `SESSION_COOKIE_NAME` constant — locked, load-bearing).
- **Middleware does presence-check only** on the cookie. Do NOT try to validate the signature client-side (the cookie value is HttpOnly-inaccessible, and validation is the API's job). Signature failures surface as 401 on `GET /api/me` and other calls; the client-side global handler catches that and redirects.
- **Single SSE connection per browser session**, mounted at the root layout via `<SSEProvider>`. Do NOT create per-page or per-component `EventSource` instances.
- **`openapi-fetch`** for the runtime client (types + tiny ~4KB runtime). NOT `openapi-typescript-codegen` (deprecated maintainer + GPL concerns).
- **shadcn/ui components live under `frontend/src/components/ui/`** — copied into the repo via `npx shadcn add`, NOT installed as an npm dep.
- **Middleware matcher** excludes `_next/static`, `_next/image`, `favicon.ico`. It INCLUDES `/api/*` and `/oauth/*` in its scope but short-circuits with `NextResponse.next()` for those prefixes so they're passed through to the rewrites. See spec §4 middleware code.
- **Frontend NEVER touches the cookie value.** Only checks presence via `req.cookies.has('twaky_session')`.
- **Rewrites target `http://twaky-api:8000`** (twake-network internal DNS). Configured via env var `API_INTERNAL_URL` for testability.
- **OpenAPI schema is the source of truth.** Every commit runs `make api-types` and diffs `frontend/src/lib/api-types.d.ts` in CI — stale types block the merge.
- **Every commit passes:** `npm run typecheck`, `npm run lint`, `npm run test:unit`, `npm run build` (frontend); plus `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src/` (Python side unchanged). No frontend commit may break the Python gates.
- **Atomic commit per task, imperative ≤72 chars.**
- **The LemonLDAP `twaky-api` OIDC client** still needs provisioning in the deploy repo before the browser flow works end-to-end. Documented in T3's rollout notes. Playwright tests bypass OIDC via the `sign_session()` seam and are unaffected.
- **Deferred minors from 3a's final review** (documented in the 3a spec §10 + progress ledger) are NOT in scope here. If a 3a bug surfaces during 3b work, a 3a-follow-up commit fixes it in a separate PR, not folded in.

---

## File Structure

**New `frontend/` subdirectory** — everything below is under `/home/mmaudet/work/twaky/frontend/`:

| Path | Responsibility |
|---|---|
| `package.json` | Deps + scripts (`dev`, `build`, `start`, `lint`, `typecheck`, `test:unit`, `test:e2e`). |
| `package-lock.json` | npm lockfile, committed. |
| `tsconfig.json` | TypeScript config with `@/*` path alias to `src/*`. |
| `next.config.js` | `output: 'standalone'` + rewrites for `/api/*` and `/oauth/*`. |
| `tailwind.config.ts` | Tailwind 4 config with content globs pointing at `src/**/*.{ts,tsx}`. |
| `postcss.config.mjs` | Tailwind PostCSS plugin. |
| `eslint.config.mjs` | Flat ESLint config, extends `eslint-config-next`. |
| `.gitignore` | Ignores `node_modules`, `.next`, `.turbo`, `coverage`, `playwright-report`, `test-results`. |
| `Dockerfile` | Multistage: `node:22-alpine` deps → builder → runner. `output: standalone` copy pattern. |
| `Makefile` | `api-types` target (regenerates `src/lib/api-types.d.ts` from `../docs/api/openapi.yaml`). |
| `.env.example` | `API_INTERNAL_URL`, `NEXT_PUBLIC_APP_NAME`. |
| `components.json` | shadcn/ui config (style, base color, path aliases). |
| `middleware.ts` | Edge middleware, auth guard. |
| `vitest.config.ts` | Vitest config with jsdom + React plugin. |
| `vitest.setup.ts` | Global test setup: `@testing-library/jest-dom` matchers + MSW server. |
| `playwright.config.ts` | Playwright config with `signedInPage` fixture wired. |

**Source layout under `frontend/src/`:**

| Path | Responsibility |
|---|---|
| `app/globals.css` | Tailwind directives + shadcn CSS variables (colors, radius). |
| `app/layout.tsx` | Root layout with `<html>`, `<body>`, providers stack, `<Header />`. |
| `app/page.tsx` | `/` dashboard page. |
| `app/missions/[id]/page.tsx` | `/missions/[id]` detail page. |
| `app/me/page.tsx` | `/me` profile page. |
| `app/stats/page.tsx` | `/stats` counters page. |
| `lib/api-types.d.ts` | Generated by `openapi-typescript`. Committed. |
| `lib/api.ts` | `openapi-fetch` client singleton. Exports `api`. |
| `lib/api-error.ts` | `ApiError` class wrapping the uniform error envelope. |
| `lib/query-client.ts` | `createQueryClient()` factory with retry rules + `onError` global handler. |
| `lib/format-session-expiry.ts` | Helper: input = login timestamp OR cookie's Expires; output = "6h 42m". |
| `lib/sanitize-return-to.ts` | Client mirror of API's `_safe_return_to`. |
| `lib/compute-state-breakdown.ts` | `Mission[]` → `Record<MissionState, number>`. |
| `hooks/use-me.ts` | `useMe()` → `useQuery` around `GET /me`. |
| `hooks/use-missions.ts` | `useMissions(state?)` → `useQuery` around `GET /missions`. |
| `hooks/use-mission.ts` | `useMission(id)` → `useQuery` around `GET /missions/{mid}`. |
| `hooks/use-declare-mission.ts` | `useDeclareMission()` → `useMutation` around `POST /missions`. |
| `hooks/use-resume-mission.ts` | `useResumeMission()` → `useMutation` around `POST /missions/{mid}/resume`. |
| `hooks/use-cancel-mission.ts` | `useCancelMission()` → `useMutation` around `POST /missions/{mid}/cancel`. |
| `components/providers/query-provider.tsx` | `<QueryProvider>` wrapping `QueryClientProvider`. |
| `components/providers/sse-provider.tsx` | `<SSEProvider>` with global EventSource + invalidation. |
| `components/layout/header.tsx` | Top-nav shell. |
| `components/layout/user-dropdown.tsx` | Header user avatar + Profile/Sign out menu. |
| `components/layout/sse-indicator.tsx` | Green/orange/gray dot for connection state. |
| `components/missions/mission-list.tsx` | Dashboard table. |
| `components/missions/state-badge.tsx` | Color/icon badge per `MissionState`. |
| `components/missions/relative-time.tsx` | "2m ago" formatter. |
| `components/missions/state-timeline.tsx` | Horizontal frise for detail page. |
| `components/missions/artifact-accordion.tsx` | Detail page artifacts with Shiki JSON. |
| `components/missions/new-mission-dialog.tsx` | Dashboard [+ New mission] modal. |
| `components/missions/cancel-mission-dialog.tsx` | Confirm dialog with reason textarea. |
| `components/missions/resume-form.tsx` | Dispatcher on artifact `kind`. |
| `components/missions/approve-draft-form.tsx` | Specialised `kind === "approve_draft"` UI. |
| `components/missions/generic-resume-form.tsx` | Fallback JSON editor UI. |
| `components/ui/*` | shadcn/ui components (copied by `npx shadcn add`). |
| `test/mocks/handlers.ts` | Default MSW handlers for `/api/me`, `/api/missions`, etc. |
| `test/mocks/server.ts` | MSW node server for Vitest. |

**Tests colocated:** each `foo.ts` has a `foo.test.ts` sibling when unit-testable. E2E tests live under `frontend/tests/e2e/`.

**E2E setup:**

| Path | Responsibility |
|---|---|
| `tests/e2e/fixtures.ts` | Playwright `test` with `signedInPage` fixture (forges cookie via `sign_session()` seam). |
| `tests/e2e/seed-awaiting-user.py` | Python one-shot: declare + walk to `awaiting_user` for T20's scenario. |
| `tests/e2e/auth.spec.ts` | 2 tests: unauth redirect, signed-in dashboard. |
| `tests/e2e/missions.spec.ts` | Declare → detail → cancel. |
| `tests/e2e/awaiting-user.spec.ts` | Approve draft flow (uses seed helper). |
| `tests/e2e/signout.spec.ts` | Sign-out redirect. |

**Files modified outside `frontend/`:**

| Path | Change |
|---|---|
| `docker-compose.yml` | Add `twaky-frontend` service block; move Traefik labels from `twaky-api` to `twaky-frontend`. |
| `.github/workflows/ci.yml` | Add `frontend` and `frontend-e2e` jobs (parallel to existing Python jobs). |
| `README.md` | New "Twaky Frontend (sub-project 3b)" section describing local dev + deploy. |
| `.gitignore` (repo root) | Add `frontend/node_modules/`, `frontend/.next/`, `frontend/coverage/`, `frontend/playwright-report/`, `frontend/test-results/`. |

---

## Task 1: Scaffold Next.js 15 project

**Files:**
- Create: `frontend/package.json`, `frontend/tsconfig.json`, `frontend/next.config.js`, `frontend/tailwind.config.ts`, `frontend/postcss.config.mjs`, `frontend/eslint.config.mjs`, `frontend/.gitignore`, `frontend/src/app/{layout,page,globals.css}`
- Create: `frontend/.env.example`
- Modify: `.gitignore` (repo root)

**Interfaces:**
- Produces: a working Next.js 15 App Router app that responds to `http://localhost:3000` in dev mode; `npm run build` produces a `.next/` output; `npm run typecheck` passes.

- [ ] **Step 1: Bootstrap with `create-next-app`**

```bash
cd /home/mmaudet/work/twaky
npx create-next-app@latest frontend \
    --typescript \
    --tailwind \
    --eslint \
    --app \
    --src-dir \
    --import-alias '@/*' \
    --use-npm \
    --no-turbo
```

The scaffold creates `frontend/` with the file structure listed in the plan header. `create-next-app` pins compatible versions of Next 15, React 19, Tailwind, TypeScript, ESLint.

- [ ] **Step 2: Verify build + typecheck + lint work out of the box**

```bash
cd frontend
npm run build
npm run lint
npx tsc --noEmit
```
All three MUST succeed. If any fails, fix the scaffold output before continuing.

- [ ] **Step 3: Add `typecheck` script + set Node engine**

Edit `frontend/package.json`:
```json
{
  "engines": { "node": ">=22" },
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "next lint",
    "typecheck": "tsc --noEmit"
  }
}
```

- [ ] **Step 4: Add `output: 'standalone'` to `next.config.js`**

Replace `frontend/next.config.js` (or `.ts`) with:
```javascript
/** @type {import('next').NextConfig} */
const nextConfig = {
    output: 'standalone',
    // Rewrites added in T5.
}
module.exports = nextConfig
```

Re-run `npm run build` to confirm it produces `frontend/.next/standalone/` output.

- [ ] **Step 5: Create `.env.example`**

Create `frontend/.env.example`:
```
# Where the browser-side frontend reaches the backend.
# Empty in dev (browser to same-origin via next dev proxy or direct).
NEXT_PUBLIC_APP_NAME=Twaky

# Where the Next.js server-side rewrites proxy /api and /oauth.
# Set to http://twaky-api:8000 in Docker, http://localhost:8000 in local dev.
API_INTERNAL_URL=http://twaky-api:8000
```

- [ ] **Step 6: Update repo `.gitignore`**

Add to `/home/mmaudet/work/twaky/.gitignore` (top-level):
```
# Frontend build artifacts
frontend/node_modules/
frontend/.next/
frontend/.turbo/
frontend/coverage/
frontend/playwright-report/
frontend/test-results/
frontend/.env
```

- [ ] **Step 7: Commit**

```bash
cd /home/mmaudet/work/twaky
git add frontend/ .gitignore
git commit -m "feat(frontend): scaffold Next.js 15 App Router project"
```

---

## Task 2: shadcn/ui init + install core components

**Files:**
- Create: `frontend/components.json`, `frontend/src/components/ui/*`, `frontend/src/lib/utils.ts`

**Interfaces:**
- Produces: shadcn/ui set up with 10 core components under `src/components/ui/`, all typed and importable via `@/components/ui/<name>`.

- [ ] **Step 1: Initialize shadcn/ui**

```bash
cd frontend
npx shadcn@latest init
```

At the prompts:
- Style: **New York**
- Base color: **Slate**
- CSS variables: **Yes**

This creates `components.json` and updates `src/app/globals.css` with the shadcn CSS variables + Tailwind directives.

- [ ] **Step 2: Add the 10 components we need in the plan**

```bash
cd frontend
npx shadcn@latest add \
    button card dialog dropdown-menu table textarea \
    toggle-group badge accordion sonner
```

This creates the components under `frontend/src/components/ui/`. Also installs Radix primitives + `sonner` as dependencies.

- [ ] **Step 3: Verify build still works**

```bash
npm run build
npm run typecheck
npm run lint
```
All three green.

- [ ] **Step 4: Commit**

```bash
cd /home/mmaudet/work/twaky
git add frontend/components.json frontend/src/components/ui/ \
        frontend/src/lib/utils.ts frontend/src/app/globals.css \
        frontend/package.json frontend/package-lock.json
git commit -m "feat(frontend): shadcn/ui init + 10 core components"
```

---

## Task 3: Dockerfile + docker-compose service + Traefik migration

**Files:**
- Create: `frontend/Dockerfile`
- Create: `frontend/.dockerignore`
- Modify: `docker-compose.yml`

**Interfaces:**
- Produces: `docker compose up -d twaky-frontend` boots the container to healthy state; `curl -sf https://twaky.${BASE_DOMAIN}/` (via Traefik) reaches the Next.js app. Traefik labels moved from `twaky-api` to `twaky-frontend`.

- [ ] **Step 1: Write the Dockerfile**

Create `frontend/Dockerfile`:
```dockerfile
# syntax=docker/dockerfile:1.6
# Multistage build for a Next.js 15 app with output: standalone.

# --- Stage 1: dependencies ---
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# --- Stage 2: builder ---
FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# --- Stage 3: runner ---
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# Non-root user
RUN addgroup --system --gid 1001 nodejs \
 && adduser --system --uid 1001 nextjs

# Copy the standalone build (server.js + node_modules + package.json)
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
```

- [ ] **Step 2: Write `.dockerignore`**

Create `frontend/.dockerignore`:
```
node_modules
.next
.turbo
coverage
playwright-report
test-results
.env
.env.local
Dockerfile
.dockerignore
tests/
```

- [ ] **Step 3: Test the build locally**

```bash
cd frontend
docker build -t twaky-frontend:local .
```
Expected: build succeeds, image size ~150-200MB.

- [ ] **Step 4: Add the compose service and migrate Traefik labels**

Edit `/home/mmaudet/work/twaky/docker-compose.yml`. Find the `twaky-api` service block. REMOVE (or comment) the `labels:` section that has `traefik.enable=true` + the `Host(...)` rule + related Traefik labels. Leave `traefik.docker.network=twake-network` if that's fine — but the router labels move to the new service.

Add a new service block after `twaky-api`:
```yaml
  twaky-frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    image: twaky-frontend:local
    container_name: twaky-frontend
    restart: unless-stopped
    networks:
      - twake-network
    environment:
      - NODE_ENV=production
      - API_INTERNAL_URL=http://twaky-api:8000
      - NEXT_PUBLIC_APP_NAME=Twaky
    depends_on:
      twaky-api: { condition: service_healthy }
    healthcheck:
      # Proxies to twaky-api /healthz — passes when both are up (the correct signal for the entry container).
      test: ["CMD-SHELL", "wget -qO- http://localhost:3000/api/healthz >/dev/null || exit 1"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s
    labels:
      - "traefik.enable=true"
      - "traefik.docker.network=twake-network"
      - "traefik.http.routers.twaky-frontend.rule=Host(`twaky.${BASE_DOMAIN}`)"
      - "traefik.http.routers.twaky-frontend.entrypoints=websecure"
      - "traefik.http.routers.twaky-frontend.tls=true"
      - "traefik.http.routers.twaky-frontend.tls.certresolver=letsencrypt"
      - "traefik.http.services.twaky-frontend.loadbalancer.server.port=3000"
```

- [ ] **Step 5: Build + start + probe**

```bash
docker compose build twaky-frontend
docker compose up -d twaky-frontend
docker compose logs -f twaky-frontend | head -30
```
Expected: log shows Next.js ready in ~2s, then a few 502 errors as the healthcheck can't yet reach `/api/healthz` if API isn't up. If twaky-api is up, healthcheck passes within 30s.

Test the healthcheck manually inside the container:
```bash
docker exec twaky-frontend wget -qO- http://localhost:3000/api/healthz
```
Expected: no output if the API rewrites aren't yet in place (T5 adds them). For now, the healthcheck will fail — that's OK, T5 will fix. Mark this container's healthcheck as an accepted "will pass after T5" gap in the T3 report.

- [ ] **Step 6: Verify Traefik routing**

`curl -kI https://twaky.${BASE_DOMAIN}/` should return `HTTP/2 200` (or a redirect from middleware, added in T7 — for now, Next.js renders the default `/` page).

If Traefik hasn't picked up the new labels, `docker restart traefik` or wait ~30s.

- [ ] **Step 7: Commit**

```bash
git add frontend/Dockerfile frontend/.dockerignore docker-compose.yml
git commit -m "feat(compose): twaky-frontend service + Traefik migration"
```

**Report note:** the healthcheck will only fully pass after T5 wires the `/api/*` rewrites. Document this in the T3 report — reviewer should not flag it as a blocker.

---

## Task 4: OpenAPI type generation + drift CI check

**Files:**
- Create: `frontend/Makefile`
- Create: `frontend/src/lib/api-types.d.ts` (generated)
- Modify: `frontend/package.json` (add `openapi-typescript` dev dep)

**Interfaces:**
- Produces: `frontend/src/lib/api-types.d.ts` — full TypeScript type of `docs/api/openapi.yaml`. `make api-types` in `frontend/` regenerates it. CI check: `make api-types && git diff --exit-code src/lib/api-types.d.ts` blocks stale types.

- [ ] **Step 1: Install `openapi-typescript` as a dev dep**

```bash
cd frontend
npm install --save-dev openapi-typescript
```

- [ ] **Step 2: Write the Makefile**

Create `frontend/Makefile`:
```makefile
.PHONY: api-types

api-types: ## Regenerate src/lib/api-types.d.ts from ../docs/api/openapi.yaml
	@npx openapi-typescript ../docs/api/openapi.yaml -o src/lib/api-types.d.ts
	@echo "wrote frontend/src/lib/api-types.d.ts"
```

- [ ] **Step 3: Generate + commit the initial types**

```bash
cd frontend
make api-types
```

Verify the file exists and starts with a proper TypeScript module declaration (something like `export type paths = {...}`).

- [ ] **Step 4: Add a smoke test for the generated types**

Create `frontend/src/lib/api-types.test.ts`:
```typescript
import { describe, it, expectTypeOf } from 'vitest'
import type { paths, components } from './api-types'

describe('api-types.d.ts', () => {
    it('exposes the /missions path', () => {
        expectTypeOf<paths['/missions']>().toBeObject()
    })

    it('exposes the Mission schema', () => {
        expectTypeOf<components['schemas']['Mission']>().toBeObject()
    })

    it('exposes the MissionState enum', () => {
        // MissionState is a string enum; the type should include "declared"
        type S = components['schemas']['MissionState']
        expectTypeOf<'declared'>().toMatchTypeOf<S>()
        expectTypeOf<'awaiting_user'>().toMatchTypeOf<S>()
        expectTypeOf<'done'>().toMatchTypeOf<S>()
    })
})
```

Vitest isn't set up yet (T18), so this test can't run today — but adding the file now means T18 will pick it up automatically. Add a comment at the top: `// This test file requires Vitest setup from T18.`

- [ ] **Step 5: Commit**

```bash
cd /home/mmaudet/work/twaky
git add frontend/Makefile frontend/src/lib/api-types.d.ts \
        frontend/src/lib/api-types.test.ts \
        frontend/package.json frontend/package-lock.json
git commit -m "feat(frontend): OpenAPI TypeScript types + make api-types"
```

CI drift check is added in T22 as part of the frontend CI job.

---

## Task 5: openapi-fetch client + rewrites + error envelope

**Files:**
- Create: `frontend/src/lib/api.ts`
- Create: `frontend/src/lib/api-error.ts`
- Modify: `frontend/next.config.js` (add rewrites)
- Modify: `frontend/package.json` (add `openapi-fetch` runtime dep)

**Interfaces:**
- Produces:
  - `api` — singleton `openapi-fetch` client with typed `GET/POST/PUT/DELETE` methods.
  - `ApiError` class wrapping `{error: {code, message, detail?}}`.
  - Rewrites: `/api/:path*` and `/oauth/:path*` → `${API_INTERNAL_URL}/:path*`.

- [ ] **Step 1: Install `openapi-fetch`**

```bash
cd frontend
npm install openapi-fetch
```

- [ ] **Step 2: Create `api.ts`**

Create `frontend/src/lib/api.ts`:
```typescript
import createClient from 'openapi-fetch'
import type { paths } from './api-types'

/**
 * openapi-fetch client, typed against the generated OpenAPI schema.
 *
 * baseUrl is '/api' — relative, meaning requests go to the same origin
 * (the Next.js server), which rewrites them to twaky-api (see next.config.js).
 */
export const api = createClient<paths>({ baseUrl: '/api' })
```

- [ ] **Step 3: Create `api-error.ts`**

Create `frontend/src/lib/api-error.ts`:
```typescript
/**
 * Wraps the API's uniform error envelope in a throwable Error.
 *
 * Every non-2xx from twaky-api returns:
 *     {"error": {"code": "http_401", "message": "...", "detail": {...}}}
 * (See spec §4.5.)
 */
export interface ErrorEnvelope {
    error: {
        code: string
        message: string
        detail?: unknown
    }
}

export class ApiError extends Error {
    constructor(public envelope: ErrorEnvelope) {
        super(envelope.error.message)
        this.name = 'ApiError'
    }

    get code(): string {
        return this.envelope.error.code
    }
}

/**
 * Type guard for the envelope shape (some errors may not follow the contract,
 * e.g. 502 Bad Gateway from Traefik).
 */
export function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
    if (typeof value !== 'object' || value === null) return false
    const v = value as { error?: unknown }
    if (typeof v.error !== 'object' || v.error === null) return false
    const e = v.error as { code?: unknown; message?: unknown }
    return typeof e.code === 'string' && typeof e.message === 'string'
}
```

- [ ] **Step 4: Wire rewrites into `next.config.js`**

Replace `frontend/next.config.js`:
```javascript
/** @type {import('next').NextConfig} */
const nextConfig = {
    output: 'standalone',
    async rewrites() {
        const target = process.env.API_INTERNAL_URL || 'http://twaky-api:8000'
        return [
            { source: '/api/:path*', destination: `${target}/:path*` },
            { source: '/oauth/:path*', destination: `${target}/oauth/:path*` },
        ]
    },
}
module.exports = nextConfig
```

- [ ] **Step 5: Verify build**

```bash
cd frontend
npm run build
npm run typecheck
```

- [ ] **Step 6: Commit**

```bash
cd /home/mmaudet/work/twaky
git add frontend/src/lib/api.ts frontend/src/lib/api-error.ts \
        frontend/next.config.js \
        frontend/package.json frontend/package-lock.json
git commit -m "feat(frontend): openapi-fetch client + rewrites + ApiError"
```

**Post-commit sanity check** — rebuild the container and verify the healthcheck now passes:
```bash
docker compose build twaky-frontend
docker compose up -d twaky-frontend
docker compose ps twaky-frontend  # should show "healthy" within ~30s
```

---

## Task 6: TanStack Query setup + 6 typed hooks

**Files:**
- Create: `frontend/src/lib/query-client.ts`
- Create: `frontend/src/hooks/use-me.ts`, `use-missions.ts`, `use-mission.ts`, `use-declare-mission.ts`, `use-resume-mission.ts`, `use-cancel-mission.ts`
- Create: `frontend/src/components/providers/query-provider.tsx`
- Modify: `frontend/package.json` (add `@tanstack/react-query`)

**Interfaces:**
- Consumes: `api` (T5), `ApiError` (T5), generated types (T4).
- Produces:
  - `createQueryClient()` — factory returning a configured `QueryClient`.
  - `<QueryProvider>` — wraps `QueryClientProvider` with the factory-built client.
  - 6 hooks with typed signatures — see individual step files.

- [ ] **Step 1: Install `@tanstack/react-query`**

```bash
cd frontend
npm install @tanstack/react-query
```

- [ ] **Step 2: Create the QueryClient factory**

Create `frontend/src/lib/query-client.ts`:
```typescript
import { QueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { ApiError } from './api-error'
import { sanitizeReturnTo } from './sanitize-return-to'

/**
 * Handles a 401 from any authenticated call: redirect to /oauth/login with
 * the current path as return_to. Called from both query and mutation error paths.
 */
function handleUnauthorized(): void {
    if (typeof window === 'undefined') return
    const returnTo = sanitizeReturnTo(window.location.pathname + window.location.search)
    window.location.href = `/api/oauth/login?return_to=${encodeURIComponent(returnTo)}`
}

export function createQueryClient(): QueryClient {
    return new QueryClient({
        defaultOptions: {
            queries: {
                retry: (failureCount, error) => {
                    if (error instanceof ApiError) {
                        // Don't retry client errors — retry only server/network.
                        const code = error.code
                        if (['http_401', 'http_403', 'http_404', 'http_409', 'http_422']
                                .includes(code)) return false
                    }
                    return failureCount < 2
                },
                staleTime: 0,  // SSE drives invalidation; no need for staleTime cache.
            },
            mutations: {
                onError: (error) => {
                    if (error instanceof ApiError && error.code === 'http_401') {
                        toast.error('Session expired, redirecting...')
                        handleUnauthorized()
                        return
                    }
                    toast.error(error instanceof Error ? error.message : String(error))
                },
            },
        },
    })
}
```

- [ ] **Step 3: Add `sanitize-return-to.ts` helper (called by the query client)**

Create `frontend/src/lib/sanitize-return-to.ts`:
```typescript
/**
 * Client mirror of the API's _safe_return_to (see 3a's oauth router).
 *
 * Only allow local paths starting with '/' but not '//' (protocol-relative)
 * and not containing '\'. Anything else falls back to '/'.
 */
export function sanitizeReturnTo(input: string): string {
    if (!input.startsWith('/')) return '/'
    if (input.startsWith('//')) return '/'
    if (input.includes('\\')) return '/'
    return input
}
```

- [ ] **Step 4: Create the QueryProvider component**

Create `frontend/src/components/providers/query-provider.tsx`:
```typescript
'use client'

import { QueryClientProvider } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { createQueryClient } from '@/lib/query-client'

export function QueryProvider({ children }: { children: ReactNode }) {
    // Use useState to guarantee a stable QueryClient across re-renders,
    // and one QueryClient per browser session (Next.js SSR/CSR boundary).
    const [client] = useState(() => createQueryClient())
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}
```

- [ ] **Step 5: Write the 6 hooks**

Create `frontend/src/hooks/use-me.ts`:
```typescript
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ApiError, isErrorEnvelope } from '@/lib/api-error'

export function useMe() {
    return useQuery({
        queryKey: ['me'],
        queryFn: async () => {
            const { data, error } = await api.GET('/me')
            if (error) throw new ApiError(isErrorEnvelope(error) ? error : {
                error: { code: 'unknown', message: 'Unknown API error' }
            })
            return data
        },
    })
}
```

Create `frontend/src/hooks/use-missions.ts`:
```typescript
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ApiError, isErrorEnvelope } from '@/lib/api-error'
import type { components } from '@/lib/api-types'

export type MissionState = components['schemas']['MissionState']

export function useMissions(state?: MissionState) {
    return useQuery({
        queryKey: ['missions', { state }],
        queryFn: async () => {
            const { data, error } = await api.GET('/missions', {
                params: { query: state ? { state } : {} },
            })
            if (error) throw new ApiError(isErrorEnvelope(error) ? error : {
                error: { code: 'unknown', message: 'Unknown API error' }
            })
            return data
        },
    })
}
```

Create `frontend/src/hooks/use-mission.ts`:
```typescript
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ApiError, isErrorEnvelope } from '@/lib/api-error'

export function useMission(id: string) {
    return useQuery({
        queryKey: ['mission', id],
        queryFn: async () => {
            const { data, error } = await api.GET('/missions/{mid}', {
                params: { path: { mid: id } },
            })
            if (error) throw new ApiError(isErrorEnvelope(error) ? error : {
                error: { code: 'unknown', message: 'Unknown API error' }
            })
            return data
        },
        enabled: !!id,
    })
}
```

Create `frontend/src/hooks/use-declare-mission.ts`:
```typescript
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ApiError, isErrorEnvelope } from '@/lib/api-error'

export function useDeclareMission() {
    const qc = useQueryClient()
    return useMutation({
        mutationFn: async (intentText: string) => {
            const { data, error } = await api.POST('/missions', {
                body: { intent_text: intentText },
            })
            if (error) throw new ApiError(isErrorEnvelope(error) ? error : {
                error: { code: 'unknown', message: 'Unknown API error' }
            })
            return data
        },
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['missions'] })
        },
    })
}
```

Create `frontend/src/hooks/use-resume-mission.ts`:
```typescript
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ApiError, isErrorEnvelope } from '@/lib/api-error'

interface ResumeArgs {
    id: string
    userResponse: unknown
}

export function useResumeMission() {
    const qc = useQueryClient()
    return useMutation({
        mutationFn: async ({ id, userResponse }: ResumeArgs) => {
            const { data, error } = await api.POST('/missions/{mid}/resume', {
                params: { path: { mid: id } },
                body: { user_response: userResponse },
            })
            if (error) throw new ApiError(isErrorEnvelope(error) ? error : {
                error: { code: 'unknown', message: 'Unknown API error' }
            })
            return data
        },
        onSuccess: (_, { id }) => {
            qc.invalidateQueries({ queryKey: ['mission', id] })
            qc.invalidateQueries({ queryKey: ['missions'] })
        },
    })
}
```

Create `frontend/src/hooks/use-cancel-mission.ts`:
```typescript
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ApiError, isErrorEnvelope } from '@/lib/api-error'

interface CancelArgs {
    id: string
    reason: string
}

export function useCancelMission() {
    const qc = useQueryClient()
    return useMutation({
        mutationFn: async ({ id, reason }: CancelArgs) => {
            const { data, error } = await api.POST('/missions/{mid}/cancel', {
                params: { path: { mid: id } },
                body: { reason },
            })
            if (error) throw new ApiError(isErrorEnvelope(error) ? error : {
                error: { code: 'unknown', message: 'Unknown API error' }
            })
            return data
        },
        onSuccess: (_, { id }) => {
            qc.invalidateQueries({ queryKey: ['mission', id] })
            qc.invalidateQueries({ queryKey: ['missions'] })
        },
    })
}
```

- [ ] **Step 6: Verify build**

```bash
cd frontend
npm run build
npm run typecheck
```

Both green. Type errors here mean the OpenAPI schema doesn't have an endpoint we're using or the field shape mismatched.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/query-client.ts frontend/src/lib/sanitize-return-to.ts \
        frontend/src/hooks/ frontend/src/components/providers/query-provider.tsx \
        frontend/package.json frontend/package-lock.json
git commit -m "feat(frontend): TanStack Query + 6 typed hooks (me/missions/mutations)"
```

Unit tests for the hooks land in T18 alongside the Vitest setup.

---

## Task 7: Middleware (auth guard)

**Files:**
- Create: `frontend/middleware.ts`

**Interfaces:**
- Consumes: `sanitizeReturnTo` (T6).
- Produces: edge middleware that redirects unauthenticated requests to `/api/oauth/login?return_to=<sanitized>`, while passing through `/api/*`, `/oauth/*`, and Next static asset paths.

- [ ] **Step 1: Write the middleware**

Create `frontend/middleware.ts`:
```typescript
import { NextResponse, type NextRequest } from 'next/server'
import { sanitizeReturnTo } from '@/lib/sanitize-return-to'

const SESSION_COOKIE_NAME = 'twaky_session'

export function middleware(req: NextRequest) {
    const { pathname } = req.nextUrl

    // /api/* and /oauth/* are proxied to twaky-api by next.config.js rewrites.
    // Don't intercept them.
    if (pathname.startsWith('/api/') || pathname.startsWith('/oauth/')) {
        return NextResponse.next()
    }

    // Presence check only — signature is validated server-side by twaky-api.
    // We deliberately don't decode the cookie value (it's HttpOnly).
    if (req.cookies.has(SESSION_COOKIE_NAME)) {
        return NextResponse.next()
    }

    // No session — redirect to OIDC login with the current path as return_to.
    const returnTo = sanitizeReturnTo(pathname + req.nextUrl.search)
    const loginUrl = new URL(
        `/api/oauth/login?return_to=${encodeURIComponent(returnTo)}`,
        req.url,
    )
    return NextResponse.redirect(loginUrl)
}

export const config = {
    matcher: [
        // Match everything except Next.js internal + favicon.
        '/((?!_next/static|_next/image|favicon.ico|robots.txt).*)',
    ],
}
```

- [ ] **Step 2: Verify build**

```bash
cd frontend
npm run build
```

If Next reports a middleware error (e.g., importing a client module), fix by ensuring `sanitize-return-to.ts` has no client-only code. It's pure functions — should be fine.

- [ ] **Step 3: Manual smoke test**

```bash
cd frontend
npm run build && npm run start &
sleep 3
curl -sI http://localhost:3000/ | head -3
```
Expected: `HTTP/1.1 307 Temporary Redirect` + `Location: /api/oauth/login?return_to=%2F`.

Then:
```bash
curl -sI --cookie 'twaky_session=fake-value' http://localhost:3000/ | head -3
```
Expected: `HTTP/1.1 200 OK` (middleware passes through with any cookie value; the actual validation happens at the API layer).

Kill the local server: `pkill -f 'next start'`.

- [ ] **Step 4: Commit**

```bash
git add frontend/middleware.ts
git commit -m "feat(frontend): auth middleware — presence check + login redirect"
```

Middleware unit tests land in T18 (they require `@edge-runtime/vm`).

---

## Task 8: Root layout + SSE provider

**Files:**
- Create: `frontend/src/components/providers/sse-provider.tsx`
- Modify: `frontend/src/app/layout.tsx`

**Interfaces:**
- Consumes: `QueryProvider` (T6).
- Produces: `<SSEProvider>` — mounts a single `EventSource` on `/api/events` and invalidates TanStack queries on each `mission_changed` event. Root layout mounts the providers stack.

- [ ] **Step 1: Write the SSEProvider**

Create `frontend/src/components/providers/sse-provider.tsx`:
```typescript
'use client'

import { useEffect, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { components } from '@/lib/api-types'

type MissionState = components['schemas']['MissionState']

interface MissionChangedPayload {
    mission_id: string
    state: MissionState
    at: string
}

export type SSEStatus = 'connected' | 'reconnecting' | 'disconnected'

export function SSEProvider({ children }: { children: ReactNode }) {
    const qc = useQueryClient()
    const [status, setStatus] = useState<SSEStatus>('disconnected')

    useEffect(() => {
        const es = new EventSource('/api/events')

        es.onopen = () => setStatus('connected')

        es.addEventListener('mission_changed', (evt) => {
            try {
                const payload = JSON.parse(
                    (evt as MessageEvent).data
                ) as MissionChangedPayload
                qc.invalidateQueries({ queryKey: ['mission', payload.mission_id] })
                qc.invalidateQueries({ queryKey: ['missions'] })
            } catch {
                // Malformed payload — ignore (server contract violation, logged elsewhere).
            }
        })

        es.onerror = () => {
            // Browser EventSource auto-reconnects. On error we invalidate to force
            // a refetch once the connection is back — any missed events surface via
            // the fresh data.
            setStatus('reconnecting')
            qc.invalidateQueries({ queryKey: ['missions'] })
        }

        // Expose the status to child components (via context) — for now, we use
        // a global ref pattern via useState + a listener. Simplest solution:
        // context added in T9's SSE indicator wiring if needed.

        return () => {
            es.close()
            setStatus('disconnected')
        }
    }, [qc])

    // We use SSEStatusContext (defined below) to expose status downward.
    return (
        <SSEStatusContext.Provider value={status}>
            {children}
        </SSEStatusContext.Provider>
    )
}

import { createContext, useContext } from 'react'

const SSEStatusContext = createContext<SSEStatus>('disconnected')

export function useSSEStatus(): SSEStatus {
    return useContext(SSEStatusContext)
}
```

- [ ] **Step 2: Update root layout**

Replace `frontend/src/app/layout.tsx`:
```typescript
import type { Metadata } from 'next'
import { Toaster } from '@/components/ui/sonner'
import { QueryProvider } from '@/components/providers/query-provider'
import { SSEProvider } from '@/components/providers/sse-provider'
import './globals.css'

export const metadata: Metadata = {
    title: 'Twaky',
    description: 'Twaky Control Tower',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
    return (
        <html lang="en" suppressHydrationWarning>
            <body>
                <QueryProvider>
                    <SSEProvider>
                        {children}
                        <Toaster />
                    </SSEProvider>
                </QueryProvider>
            </body>
        </html>
    )
}
```

- [ ] **Step 3: Verify build**

```bash
cd frontend
npm run build
npm run typecheck
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/providers/sse-provider.tsx frontend/src/app/layout.tsx
git commit -m "feat(frontend): root layout with QueryProvider + SSEProvider + Toaster"
```

SSEProvider unit tests land in T18 (mock EventSource).

---

## Task 9: Header + user dropdown + SSE indicator

**Files:**
- Create: `frontend/src/components/layout/header.tsx`
- Create: `frontend/src/components/layout/user-dropdown.tsx`
- Create: `frontend/src/components/layout/sse-indicator.tsx`
- Modify: `frontend/src/app/layout.tsx` (mount `<Header />` in the body)

**Interfaces:**
- Consumes: `useMe()` (T6), `useSSEStatus()` (T8), shadcn `<DropdownMenu>`, `<Button>`.
- Produces: sticky top-nav visible on all pages.

- [ ] **Step 1: Write the SSE indicator**

Create `frontend/src/components/layout/sse-indicator.tsx`:
```typescript
'use client'

import { useSSEStatus } from '@/components/providers/sse-provider'
import { cn } from '@/lib/utils'

const COLORS = {
    connected: 'bg-green-500',
    reconnecting: 'bg-orange-500',
    disconnected: 'bg-gray-400',
} as const

const LABELS = {
    connected: 'Live updates connected',
    reconnecting: 'Reconnecting…',
    disconnected: 'Disconnected',
} as const

export function SSEIndicator() {
    const status = useSSEStatus()
    return (
        <div
            role="status"
            aria-label={LABELS[status]}
            title={LABELS[status]}
            className={cn(
                'h-2.5 w-2.5 rounded-full transition-colors',
                COLORS[status],
            )}
        />
    )
}
```

- [ ] **Step 2: Write the user dropdown**

Create `frontend/src/components/layout/user-dropdown.tsx`:
```typescript
'use client'

import { useRouter } from 'next/navigation'
import { toast } from 'sonner'
import { useMe } from '@/hooks/use-me'
import { Button } from '@/components/ui/button'
import {
    DropdownMenu, DropdownMenuContent, DropdownMenuItem,
    DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

export function UserDropdown() {
    const router = useRouter()
    const { data: me, isLoading } = useMe()

    async function handleSignOut() {
        try {
            // POST /oauth/logout — the API clears the cookie and 302s to LemonLDAP.
            // Use full navigation (not fetch) so the browser follows the 302 chain.
            const form = document.createElement('form')
            form.method = 'POST'
            form.action = '/api/oauth/logout'
            document.body.appendChild(form)
            form.submit()
        } catch (err) {
            toast.error('Sign out failed — please try again')
        }
    }

    return (
        <DropdownMenu>
            <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="sm" disabled={isLoading}>
                    {me?.owner_email ?? '…'} ▾
                </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => router.push('/me')}>
                    Profile
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={handleSignOut}>
                    Sign out
                </DropdownMenuItem>
            </DropdownMenuContent>
        </DropdownMenu>
    )
}
```

- [ ] **Step 3: Write the header**

Create `frontend/src/components/layout/header.tsx`:
```typescript
import Link from 'next/link'
import { SSEIndicator } from './sse-indicator'
import { UserDropdown } from './user-dropdown'

export function Header() {
    return (
        <header className="border-b sticky top-0 z-10 bg-background">
            <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4">
                <div className="flex items-center gap-4">
                    <Link href="/" className="font-semibold">
                        Twaky
                    </Link>
                    <nav className="flex items-center gap-3 text-sm text-muted-foreground">
                        <Link href="/" className="hover:text-foreground">Dashboard</Link>
                        <span>·</span>
                        <Link href="/stats" className="hover:text-foreground">Stats</Link>
                    </nav>
                </div>
                <div className="flex items-center gap-3">
                    <UserDropdown />
                    <SSEIndicator />
                </div>
            </div>
        </header>
    )
}
```

- [ ] **Step 4: Mount the header in the root layout**

Edit `frontend/src/app/layout.tsx` — insert `<Header />` between `<SSEProvider>` and `{children}`:
```typescript
<SSEProvider>
    <Header />
    <main className="mx-auto max-w-6xl px-4 py-6">
        {children}
    </main>
    <Toaster />
</SSEProvider>
```

Add the import: `import { Header } from '@/components/layout/header'`.

- [ ] **Step 5: Verify build**

```bash
cd frontend
npm run build
npm run typecheck
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/layout/ frontend/src/app/layout.tsx
git commit -m "feat(frontend): header with user dropdown + SSE indicator"
```

---

## Task 10: Dashboard page (`/`)

**Files:**
- Create: `frontend/src/components/missions/state-badge.tsx`
- Create: `frontend/src/components/missions/relative-time.tsx`
- Create: `frontend/src/components/missions/mission-list.tsx`
- Create: `frontend/src/components/missions/new-mission-dialog.tsx`
- Modify: `frontend/src/app/page.tsx` (replace default with dashboard)

**Interfaces:**
- Consumes: `useMissions()` (T6), `useDeclareMission()` (T6), shadcn `<Table>`, `<ToggleGroup>`, `<Dialog>`, `<Textarea>`, `<Button>`, `<Badge>`.
- Produces: `/` route rendering the mission list with state filter + new-mission button.

- [ ] **Step 1: State badge component**

Create `frontend/src/components/missions/state-badge.tsx`:
```typescript
import { Badge } from '@/components/ui/badge'
import type { MissionState } from '@/hooks/use-missions'
import { cn } from '@/lib/utils'

const STYLE: Record<MissionState, string> = {
    declared:       'bg-slate-100 text-slate-700 border-slate-300',
    planning:       'bg-blue-100 text-blue-700 border-blue-300',
    running:        'bg-blue-200 text-blue-900 border-blue-400',
    awaiting_user:  'bg-yellow-100 text-yellow-800 border-yellow-400',
    done:           'bg-green-100 text-green-800 border-green-300',
    failed:         'bg-red-100 text-red-800 border-red-300',
    cancelled:      'bg-gray-100 text-gray-600 border-gray-300',
}

const ICON: Record<MissionState, string> = {
    declared: '○', planning: '◐', running: '●',
    awaiting_user: '⚑', done: '✓', failed: '✗', cancelled: '⊘',
}

export function StateBadge({ state }: { state: MissionState }) {
    return (
        <Badge variant="outline" className={cn('gap-1 font-mono', STYLE[state])}>
            <span aria-hidden>{ICON[state]}</span>
            <span>{state}</span>
        </Badge>
    )
}
```

- [ ] **Step 2: Relative time component**

Create `frontend/src/components/missions/relative-time.tsx`:
```typescript
'use client'

import { useEffect, useState } from 'react'

function humanize(ms: number): string {
    const sec = Math.floor(ms / 1000)
    if (sec < 60) return `${sec}s ago`
    const min = Math.floor(sec / 60)
    if (min < 60) return `${min}m ago`
    const hr = Math.floor(min / 60)
    if (hr < 24) return `${hr}h ago`
    const day = Math.floor(hr / 24)
    return `${day}d ago`
}

export function RelativeTime({ timestamp }: { timestamp: string }) {
    const [now, setNow] = useState(() => Date.now())

    useEffect(() => {
        const interval = setInterval(() => setNow(Date.now()), 30_000)
        return () => clearInterval(interval)
    }, [])

    const parsed = new Date(timestamp).getTime()
    if (isNaN(parsed)) return <span>—</span>

    return <span title={timestamp}>{humanize(now - parsed)}</span>
}
```

- [ ] **Step 3: New mission dialog**

Create `frontend/src/components/missions/new-mission-dialog.tsx`:
```typescript
'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import {
    Dialog, DialogClose, DialogContent, DialogFooter,
    DialogHeader, DialogTitle, DialogTrigger,
} from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import { useDeclareMission } from '@/hooks/use-declare-mission'

export function NewMissionDialog() {
    const router = useRouter()
    const [open, setOpen] = useState(false)
    const [intent, setIntent] = useState('')
    const declare = useDeclareMission()

    async function handleSubmit() {
        const trimmed = intent.trim()
        if (!trimmed) return
        try {
            const mission = await declare.mutateAsync(trimmed)
            toast.success('Mission declared')
            setIntent('')
            setOpen(false)
            router.push(`/missions/${mission.id}`)
        } catch {
            // Error toast handled by global mutation onError.
        }
    }

    return (
        <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
                <Button>+ New mission</Button>
            </DialogTrigger>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>Declare a new mission</DialogTitle>
                </DialogHeader>
                <Textarea
                    value={intent}
                    onChange={(e) => setIntent(e.target.value)}
                    placeholder="What should the assistant do?"
                    rows={6}
                    maxLength={4096}
                    autoFocus
                />
                <DialogFooter>
                    <DialogClose asChild><Button variant="ghost">Cancel</Button></DialogClose>
                    <Button
                        onClick={handleSubmit}
                        disabled={declare.isPending || !intent.trim()}
                    >
                        {declare.isPending ? 'Declaring…' : 'Declare'}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}
```

- [ ] **Step 4: Mission list component**

Create `frontend/src/components/missions/mission-list.tsx`:
```typescript
'use client'

import Link from 'next/link'
import type { components } from '@/lib/api-types'
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'
import { StateBadge } from './state-badge'
import { RelativeTime } from './relative-time'

type Mission = components['schemas']['Mission']

export function MissionList({ missions }: { missions: Mission[] }) {
    if (missions.length === 0) {
        return (
            <div className="rounded-lg border p-8 text-center text-muted-foreground">
                No missions yet. Click <em>+ New mission</em> to declare one.
            </div>
        )
    }

    return (
        <Table>
            <TableHeader>
                <TableRow>
                    <TableHead className="w-40">State</TableHead>
                    <TableHead>Intent</TableHead>
                    <TableHead className="w-32">Declared</TableHead>
                    <TableHead className="w-10"></TableHead>
                </TableRow>
            </TableHeader>
            <TableBody>
                {missions.map((m) => (
                    <TableRow
                        key={m.id}
                        className={m.state === 'awaiting_user'
                            ? 'bg-yellow-50 hover:bg-yellow-100'
                            : 'hover:bg-muted/50'}
                    >
                        <TableCell><StateBadge state={m.state} /></TableCell>
                        <TableCell className="truncate max-w-md">
                            <Link href={`/missions/${m.id}`} className="hover:underline">
                                {m.intent_text}
                            </Link>
                        </TableCell>
                        <TableCell><RelativeTime timestamp={m.declared_at} /></TableCell>
                        <TableCell>
                            <Link href={`/missions/${m.id}`}>→</Link>
                        </TableCell>
                    </TableRow>
                ))}
            </TableBody>
        </Table>
    )
}
```

- [ ] **Step 5: Dashboard page**

Replace `frontend/src/app/page.tsx`:
```typescript
'use client'

import { useState } from 'react'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { MissionList } from '@/components/missions/mission-list'
import { NewMissionDialog } from '@/components/missions/new-mission-dialog'
import { useMissions, type MissionState } from '@/hooks/use-missions'

type Filter = 'all' | 'live' | MissionState

const LIVE_STATES = new Set<MissionState>([
    'declared', 'planning', 'running', 'awaiting_user',
])

export default function DashboardPage() {
    const [filter, setFilter] = useState<Filter>('live')

    const queryState: MissionState | undefined =
        filter === 'all' || filter === 'live' ? undefined : filter
    const { data, isLoading, error } = useMissions(queryState)

    const missions = (data ?? []).filter((m) => {
        if (filter === 'all') return true
        if (filter === 'live') return LIVE_STATES.has(m.state)
        return m.state === filter
    })

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <h1 className="text-2xl font-semibold">Missions</h1>
                <NewMissionDialog />
            </div>

            <ToggleGroup
                type="single"
                value={filter}
                onValueChange={(v) => v && setFilter(v as Filter)}
            >
                <ToggleGroupItem value="all">All</ToggleGroupItem>
                <ToggleGroupItem value="live">Live</ToggleGroupItem>
                <ToggleGroupItem value="done">Done</ToggleGroupItem>
                <ToggleGroupItem value="failed">Failed</ToggleGroupItem>
                <ToggleGroupItem value="cancelled">Cancelled</ToggleGroupItem>
            </ToggleGroup>

            {isLoading && <p className="text-muted-foreground">Loading…</p>}
            {error && (
                <p className="text-red-600">Failed to load missions: {error.message}</p>
            )}
            {data && <MissionList missions={missions} />}
        </div>
    )
}
```

- [ ] **Step 6: Verify build**

```bash
cd frontend
npm run build
npm run typecheck
```

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/missions/ frontend/src/app/page.tsx
git commit -m "feat(frontend): dashboard page with mission list + filter + new dialog"
```

---

## Task 11: Mission detail page (header + timeline + artifacts)

**Files:**
- Create: `frontend/src/components/missions/state-timeline.tsx`
- Create: `frontend/src/components/missions/artifact-accordion.tsx`
- Create: `frontend/src/components/missions/cancel-mission-dialog.tsx`
- Create: `frontend/src/app/missions/[id]/page.tsx`
- Modify: `frontend/package.json` (add `shiki`)

**Interfaces:**
- Consumes: `useMission()` (T6), `useCancelMission()` (T6), shadcn `<Accordion>`, `<Card>`.
- Produces: `/missions/[id]` route rendering the mission header + state timeline + artifacts accordion + (conditional) cancel dialog. The resume form is added in T12.

- [ ] **Step 1: Install `shiki`**

```bash
cd frontend
npm install shiki
```

- [ ] **Step 2: State timeline component**

Create `frontend/src/components/missions/state-timeline.tsx`:
```typescript
import type { components } from '@/lib/api-types'
import { cn } from '@/lib/utils'

type MissionState = components['schemas']['MissionState']

/**
 * Renders a horizontal timeline of states.
 * Reached states are filled; unreached are outlined.
 *
 * MVP: we don't have per-state timestamps in the API; we display timestamps
 * for `declared_at`, `started_at`, `terminated_at` when present.
 */
export function StateTimeline({
    currentState,
    declaredAt,
    startedAt,
    terminatedAt,
}: {
    currentState: MissionState
    declaredAt: string
    startedAt?: string | null
    terminatedAt?: string | null
}) {
    const order: MissionState[] = [
        'declared', 'planning', 'running',
        'awaiting_user', 'done',
    ]
    const currentIdx = order.indexOf(currentState)

    return (
        <ol className="flex items-center gap-2 text-xs">
            {order.map((s, i) => {
                const reached = i <= currentIdx && currentIdx >= 0
                return (
                    <li key={s} className="flex items-center gap-2">
                        <div className={cn(
                            'h-3 w-3 rounded-full border',
                            reached ? 'bg-primary border-primary' : 'bg-background border-muted-foreground/50',
                        )} />
                        <span className={reached ? 'font-medium' : 'text-muted-foreground'}>
                            {s}
                        </span>
                        {i < order.length - 1 && <span className="text-muted-foreground">─</span>}
                    </li>
                )
            })}
        </ol>
    )
}
```

- [ ] **Step 3: Artifact accordion component**

Create `frontend/src/components/missions/artifact-accordion.tsx`:
```typescript
'use client'

import { useEffect, useState } from 'react'
import {
    Accordion, AccordionContent, AccordionItem, AccordionTrigger,
} from '@/components/ui/accordion'
import { Badge } from '@/components/ui/badge'
import { RelativeTime } from './relative-time'

interface Artifact {
    kind?: string
    at?: string
    [key: string]: unknown
}

async function highlightJson(json: string): Promise<string> {
    const { codeToHtml } = await import('shiki')
    return codeToHtml(json, {
        lang: 'json',
        theme: 'github-light',
    })
}

function ArtifactBody({ artifact }: { artifact: Artifact }) {
    const [html, setHtml] = useState<string>('')
    const json = JSON.stringify(artifact, null, 2)

    useEffect(() => {
        let cancelled = false
        highlightJson(json).then((h) => { if (!cancelled) setHtml(h) })
        return () => { cancelled = true }
    }, [json])

    if (!html) {
        return <pre className="text-xs overflow-x-auto"><code>{json}</code></pre>
    }
    return <div className="text-xs overflow-x-auto" dangerouslySetInnerHTML={{ __html: html }} />
}

export function ArtifactAccordion({ artifacts }: { artifacts: Artifact[] }) {
    if (artifacts.length === 0) {
        return <p className="text-sm text-muted-foreground">No artifacts yet.</p>
    }

    const defaultOpen = artifacts.slice(-2).map((_, i) =>
        `item-${artifacts.length - 2 + i}`,
    ).filter((k) => k.startsWith('item-') && !k.includes('-')  === false)

    return (
        <Accordion type="multiple" defaultValue={defaultOpen}>
            {artifacts.map((a, idx) => (
                <AccordionItem key={idx} value={`item-${idx}`}>
                    <AccordionTrigger>
                        <div className="flex items-center gap-2 text-sm">
                            <Badge variant="outline">{a.kind ?? 'artifact'}</Badge>
                            <span className="text-muted-foreground">
                                {a.at ? <RelativeTime timestamp={a.at} /> : ''}
                            </span>
                        </div>
                    </AccordionTrigger>
                    <AccordionContent>
                        <ArtifactBody artifact={a} />
                    </AccordionContent>
                </AccordionItem>
            ))}
        </Accordion>
    )
}
```

- [ ] **Step 4: Cancel mission dialog**

Create `frontend/src/components/missions/cancel-mission-dialog.tsx`:
```typescript
'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import {
    Dialog, DialogClose, DialogContent, DialogFooter,
    DialogHeader, DialogTitle, DialogTrigger,
} from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import { useCancelMission } from '@/hooks/use-cancel-mission'

export function CancelMissionDialog({ missionId }: { missionId: string }) {
    const router = useRouter()
    const [open, setOpen] = useState(false)
    const [reason, setReason] = useState('user_requested')
    const cancel = useCancelMission()

    async function handleConfirm() {
        try {
            await cancel.mutateAsync({ id: missionId, reason: reason.trim() || 'user_requested' })
            toast.success('Mission cancelled')
            setOpen(false)
            router.push('/')
        } catch { /* handled globally */ }
    }

    return (
        <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
                <Button variant="destructive" size="sm">Cancel mission</Button>
            </DialogTrigger>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>Cancel this mission?</DialogTitle>
                </DialogHeader>
                <p className="text-sm text-muted-foreground">
                    The mission will move to state <code>cancelled</code>. Optionally add a reason.
                </p>
                <Textarea
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    rows={2}
                    maxLength={256}
                />
                <DialogFooter>
                    <DialogClose asChild><Button variant="ghost">Keep it</Button></DialogClose>
                    <Button
                        variant="destructive"
                        onClick={handleConfirm}
                        disabled={cancel.isPending}
                    >
                        {cancel.isPending ? 'Cancelling…' : 'Cancel mission'}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}
```

- [ ] **Step 5: Mission detail page**

Create `frontend/src/app/missions/[id]/page.tsx`:
```typescript
'use client'

import Link from 'next/link'
import { use } from 'react'
import { StateBadge } from '@/components/missions/state-badge'
import { RelativeTime } from '@/components/missions/relative-time'
import { StateTimeline } from '@/components/missions/state-timeline'
import { ArtifactAccordion } from '@/components/missions/artifact-accordion'
import { CancelMissionDialog } from '@/components/missions/cancel-mission-dialog'
import { Button } from '@/components/ui/button'
import { useMission } from '@/hooks/use-mission'

export default function MissionDetailPage({
    params,
}: { params: Promise<{ id: string }> }) {
    const { id } = use(params)
    const { data: mission, isLoading, error } = useMission(id)

    if (isLoading) return <p className="text-muted-foreground">Loading…</p>
    if (error) return <p className="text-red-600">Error: {error.message}</p>
    if (!mission) return <p>Not found.</p>

    const terminal = ['done', 'failed', 'cancelled'].includes(mission.state)

    return (
        <div className="space-y-6">
            <div>
                <Link href="/" className="text-sm text-muted-foreground hover:underline">
                    ← Back to missions
                </Link>
            </div>

            <div className="space-y-2">
                <h1 className="text-2xl font-semibold">{mission.intent_text}</h1>
                <div className="flex items-center gap-3 text-sm text-muted-foreground">
                    <StateBadge state={mission.state} />
                    <span>·</span>
                    <span>declared <RelativeTime timestamp={mission.declared_at} /></span>
                    <span>·</span>
                    <span>{mission.declared_by}</span>
                </div>
                <div className="flex items-center gap-2 pt-2">
                    {!terminal && <CancelMissionDialog missionId={mission.id} />}
                    <a href={`/api/missions/${mission.id}/trace`} target="_blank" rel="noreferrer">
                        <Button variant="outline" size="sm">Open in Langfuse ↗</Button>
                    </a>
                </div>
                {terminal && mission.state_reason && (
                    <p className="pt-2 text-sm">
                        Terminal reason: <code>{mission.state_reason}</code>
                    </p>
                )}
            </div>

            <section>
                <h2 className="text-sm font-semibold mb-2">State timeline</h2>
                <StateTimeline
                    currentState={mission.state}
                    declaredAt={mission.declared_at}
                />
            </section>

            <section>
                <h2 className="text-sm font-semibold mb-2">
                    Artifacts ({mission.artifacts?.length ?? 0})
                </h2>
                <ArtifactAccordion artifacts={mission.artifacts ?? []} />
            </section>

            {/* Resume form mounted here in T12 when state === 'awaiting_user'. */}
        </div>
    )
}
```

- [ ] **Step 6: Verify build**

```bash
cd frontend
npm run build
npm run typecheck
```

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/missions/ frontend/src/app/missions/ \
        frontend/package.json frontend/package-lock.json
git commit -m "feat(frontend): mission detail with timeline + artifacts + cancel"
```

---

## Task 12: Resume-with-approval form (hybrid)

**Files:**
- Create: `frontend/src/components/missions/resume-form.tsx`
- Create: `frontend/src/components/missions/approve-draft-form.tsx`
- Create: `frontend/src/components/missions/generic-resume-form.tsx`
- Modify: `frontend/src/app/missions/[id]/page.tsx` (mount `<ResumeForm />` when awaiting_user)

**Interfaces:**
- Consumes: `useResumeMission()` (T6), `useCancelMission()` (T6), shadcn components.
- Produces: conditional resume form on the detail page that dispatches by artifact `kind`.

- [ ] **Step 1: ApproveDraftForm — specialised UI**

Create `frontend/src/components/missions/approve-draft-form.tsx`:
```typescript
'use client'

import { useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Textarea } from '@/components/ui/textarea'
import { useResumeMission } from '@/hooks/use-resume-mission'
import { useCancelMission } from '@/hooks/use-cancel-mission'

interface DraftArtifact {
    kind: 'approve_draft'
    draft: string
    to?: string
    subject?: string
}

export function ApproveDraftForm({
    missionId,
    artifact,
}: {
    missionId: string
    artifact: DraftArtifact
}) {
    const [draft, setDraft] = useState(artifact.draft)
    const resume = useResumeMission()
    const cancel = useCancelMission()

    async function handleApprove() {
        try {
            await resume.mutateAsync({
                id: missionId,
                userResponse: { approved: true, draft },
            })
            toast.success('Draft approved')
        } catch { /* global handler */ }
    }

    async function handleReject() {
        try {
            await cancel.mutateAsync({
                id: missionId,
                reason: 'user_rejected_draft',
            })
            toast.success('Mission cancelled')
        } catch { /* global handler */ }
    }

    return (
        <Card>
            <CardHeader>
                <CardTitle>Approve draft</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
                {artifact.to && (
                    <div className="text-sm">
                        <span className="text-muted-foreground">To:</span> {artifact.to}
                    </div>
                )}
                {artifact.subject && (
                    <div className="text-sm">
                        <span className="text-muted-foreground">Subject:</span> {artifact.subject}
                    </div>
                )}
                <Textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    rows={15}
                    className="font-mono text-sm"
                />
                <div className="flex justify-end gap-2">
                    <Button
                        variant="destructive"
                        onClick={handleReject}
                        disabled={resume.isPending || cancel.isPending}
                    >
                        Cancel mission
                    </Button>
                    <Button
                        onClick={handleApprove}
                        disabled={resume.isPending || cancel.isPending || !draft.trim()}
                    >
                        {resume.isPending ? 'Approving…' : 'Approve →'}
                    </Button>
                </div>
            </CardContent>
        </Card>
    )
}
```

- [ ] **Step 2: GenericResumeForm — JSON fallback**

Create `frontend/src/components/missions/generic-resume-form.tsx`:
```typescript
'use client'

import { useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Textarea } from '@/components/ui/textarea'
import { useResumeMission } from '@/hooks/use-resume-mission'
import { useCancelMission } from '@/hooks/use-cancel-mission'

export function GenericResumeForm({
    missionId,
    kind,
}: {
    missionId: string
    kind: string
}) {
    const [json, setJson] = useState('{"approved": true}')
    const [jsonError, setJsonError] = useState<string | null>(null)
    const resume = useResumeMission()
    const cancel = useCancelMission()

    async function handleSubmit() {
        let parsed: unknown
        try {
            parsed = JSON.parse(json)
        } catch (e) {
            setJsonError((e as Error).message)
            return
        }
        setJsonError(null)
        try {
            await resume.mutateAsync({ id: missionId, userResponse: parsed })
            toast.success('Response submitted')
        } catch { /* global handler */ }
    }

    async function handleCancel() {
        try {
            await cancel.mutateAsync({ id: missionId, reason: 'user_cancelled_generic' })
            toast.success('Mission cancelled')
        } catch { /* global handler */ }
    }

    return (
        <Card>
            <CardHeader>
                <CardTitle>Action required (kind: {kind})</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
                <p className="text-sm text-muted-foreground">
                    This mission requires input of type <code>{kind}</code>.
                    Submit a JSON payload matching the agent&apos;s expected schema.
                </p>
                <Textarea
                    value={json}
                    onChange={(e) => { setJson(e.target.value); setJsonError(null) }}
                    rows={8}
                    className="font-mono text-xs"
                    spellCheck={false}
                />
                {jsonError && <p className="text-sm text-red-600">JSON error: {jsonError}</p>}
                <div className="flex justify-end gap-2">
                    <Button
                        variant="destructive"
                        onClick={handleCancel}
                        disabled={resume.isPending || cancel.isPending}
                    >
                        Cancel mission
                    </Button>
                    <Button
                        onClick={handleSubmit}
                        disabled={resume.isPending || cancel.isPending}
                    >
                        {resume.isPending ? 'Submitting…' : 'Submit →'}
                    </Button>
                </div>
            </CardContent>
        </Card>
    )
}
```

- [ ] **Step 3: ResumeForm dispatcher**

Create `frontend/src/components/missions/resume-form.tsx`:
```typescript
'use client'

import { ApproveDraftForm } from './approve-draft-form'
import { GenericResumeForm } from './generic-resume-form'

interface Artifact {
    kind?: string
    [key: string]: unknown
}

/**
 * Looks through the mission's artifacts for the most recent pending_user_input.
 *
 * By convention (see sub-project 2's cooperative pattern), the artifact that
 * triggered the pause has a `kind` matching a known handler:
 *     - "approve_draft" → specialised UI (Plume drafting)
 *     - anything else → generic JSON fallback
 */
function findPending(artifacts: Artifact[]): Artifact | undefined {
    for (let i = artifacts.length - 1; i >= 0; i--) {
        if (artifacts[i]?.kind) return artifacts[i]
    }
    return undefined
}

export function ResumeForm({
    missionId,
    artifacts,
}: {
    missionId: string
    artifacts: Artifact[]
}) {
    const artifact = findPending(artifacts)
    if (!artifact) {
        return <GenericResumeForm missionId={missionId} kind="unknown" />
    }

    if (artifact.kind === 'approve_draft') {
        return <ApproveDraftForm
            missionId={missionId}
            artifact={artifact as never}
        />
    }
    return <GenericResumeForm missionId={missionId} kind={artifact.kind ?? 'unknown'} />
}
```

- [ ] **Step 4: Mount on the detail page**

Edit `frontend/src/app/missions/[id]/page.tsx`. After the `<section>` for artifacts, add:
```typescript
{mission.state === 'awaiting_user' && (
    <section>
        <ResumeForm
            missionId={mission.id}
            artifacts={mission.artifacts ?? []}
        />
    </section>
)}
```
Add the import: `import { ResumeForm } from '@/components/missions/resume-form'`.

- [ ] **Step 5: Verify build**

```bash
cd frontend
npm run build
npm run typecheck
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/missions/ frontend/src/app/missions/[id]/page.tsx
git commit -m "feat(frontend): resume form — approve_draft specialised + JSON fallback"
```

---

## Task 13: `/me` profile page

**Files:**
- Create: `frontend/src/lib/format-session-expiry.ts`
- Create: `frontend/src/app/me/page.tsx`

**Interfaces:**
- Consumes: `useMe()` (T6), shadcn `<Card>`, `<Button>`.
- Produces: `/me` route with owner_email + session expiry indicator + Langfuse link + Sign out button.

- [ ] **Step 1: Session expiry helper**

Create `frontend/src/lib/format-session-expiry.ts`:
```typescript
/**
 * Given a login timestamp (ms since epoch) and a TTL in seconds,
 * return "Xh Ym" or "Xm" or "expired".
 */
export function formatSessionExpiry(loginAt: number, ttlSeconds: number): string {
    const remaining = loginAt + ttlSeconds * 1000 - Date.now()
    if (remaining <= 0) return 'expired'
    const min = Math.floor(remaining / 60_000)
    if (min < 60) return `${min}m`
    const hr = Math.floor(min / 60)
    const rem = min % 60
    return rem > 0 ? `${hr}h ${rem}m` : `${hr}h`
}
```

- [ ] **Step 2: `/me` page**

Create `frontend/src/app/me/page.tsx`:
```typescript
'use client'

import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { useMe } from '@/hooks/use-me'
import { formatSessionExpiry } from '@/lib/format-session-expiry'

const SESSION_TTL_SECONDS = 8 * 60 * 60

// A rough approximation: the login timestamp is not available cross-origin
// (the cookie is HttpOnly). We approximate as "current session started at page load".
// A more precise implementation would store `loginAt` in sessionStorage on the OIDC
// callback response — post-MVP polish.
function useSessionExpiry(): string {
    const [loginAt] = useState<number>(() => {
        const stored = typeof window !== 'undefined'
            ? sessionStorage.getItem('twaky_login_at')
            : null
        if (stored) return Number(stored)
        if (typeof window !== 'undefined') {
            sessionStorage.setItem('twaky_login_at', String(Date.now()))
        }
        return Date.now()
    })
    const [display, setDisplay] = useState<string>(() =>
        formatSessionExpiry(loginAt, SESSION_TTL_SECONDS),
    )
    useEffect(() => {
        const iv = setInterval(() => {
            setDisplay(formatSessionExpiry(loginAt, SESSION_TTL_SECONDS))
        }, 60_000)
        return () => clearInterval(iv)
    }, [loginAt])
    return display
}

export default function MePage() {
    const { data: me, isLoading } = useMe()
    const expiry = useSessionExpiry()

    function handleSignOut() {
        const form = document.createElement('form')
        form.method = 'POST'
        form.action = '/api/oauth/logout'
        document.body.appendChild(form)
        form.submit()
    }

    return (
        <div className="mx-auto max-w-md pt-8">
            <Card>
                <CardContent className="space-y-4 py-6 text-center">
                    <p className="text-sm text-muted-foreground">Signed in as</p>
                    <p className="text-xl font-medium">
                        {isLoading ? '…' : (me?.owner_email ?? 'unknown')}
                    </p>
                    <p className="text-sm text-muted-foreground">
                        Session expires in {expiry}
                    </p>
                    {me?.langfuse_base_url && (
                        <p className="text-sm">
                            <a
                                href={me.langfuse_base_url}
                                target="_blank"
                                rel="noreferrer"
                                className="text-primary hover:underline"
                            >
                                Langfuse ↗
                            </a>
                        </p>
                    )}
                    <div className="pt-2">
                        <Button variant="outline" onClick={handleSignOut}>
                            Sign out
                        </Button>
                    </div>
                </CardContent>
            </Card>
        </div>
    )
}
```

- [ ] **Step 3: Verify build + commit**

```bash
cd frontend
npm run build
npm run typecheck
```

```bash
git add frontend/src/lib/format-session-expiry.ts frontend/src/app/me/
git commit -m "feat(frontend): /me profile page with session expiry + sign out"
```

---

## Task 14: `/stats` counters page

**Files:**
- Create: `frontend/src/lib/compute-state-breakdown.ts`
- Create: `frontend/src/app/stats/page.tsx`

**Interfaces:**
- Consumes: `useMissions()` (T6).
- Produces: `/stats` route with state breakdown + total live/terminal + recent failures list.

- [ ] **Step 1: State breakdown helper**

Create `frontend/src/lib/compute-state-breakdown.ts`:
```typescript
import type { components } from './api-types'

type Mission = components['schemas']['Mission']
type MissionState = components['schemas']['MissionState']

const ALL_STATES: MissionState[] = [
    'declared', 'planning', 'running', 'awaiting_user',
    'done', 'failed', 'cancelled',
]

const TERMINAL: Set<MissionState> = new Set(['done', 'failed', 'cancelled'])

export interface StateBreakdown {
    counts: Record<MissionState, number>
    totalLive: number
    totalTerminal: number
}

export function computeStateBreakdown(missions: Mission[]): StateBreakdown {
    const counts = Object.fromEntries(
        ALL_STATES.map((s) => [s, 0]),
    ) as Record<MissionState, number>

    for (const m of missions) {
        counts[m.state] = (counts[m.state] ?? 0) + 1
    }

    const totalLive = missions.filter((m) => !TERMINAL.has(m.state)).length
    const totalTerminal = missions.length - totalLive
    return { counts, totalLive, totalTerminal }
}
```

- [ ] **Step 2: `/stats` page**

Create `frontend/src/app/stats/page.tsx`:
```typescript
'use client'

import Link from 'next/link'
import { StateBadge } from '@/components/missions/state-badge'
import { RelativeTime } from '@/components/missions/relative-time'
import { computeStateBreakdown } from '@/lib/compute-state-breakdown'
import { useMissions } from '@/hooks/use-missions'

export default function StatsPage() {
    const { data, isLoading, error } = useMissions()  // no filter — all live

    if (isLoading) return <p className="text-muted-foreground">Loading…</p>
    if (error) return <p className="text-red-600">Error: {error.message}</p>

    const missions = data ?? []
    const { counts, totalLive, totalTerminal } = computeStateBreakdown(missions)
    const recentFailures = missions
        .filter((m) => m.state === 'failed')
        .sort((a, b) => b.declared_at.localeCompare(a.declared_at))
        .slice(0, 5)

    return (
        <div className="space-y-6">
            <h1 className="text-2xl font-semibold">Stats</h1>

            <section>
                <h2 className="text-sm font-semibold mb-2">State breakdown</h2>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-sm">
                    {Object.entries(counts).map(([s, n]) => (
                        <div key={s} className="rounded border p-2">
                            <div className="text-muted-foreground text-xs">{s}</div>
                            <div className="text-lg font-medium">{n}</div>
                        </div>
                    ))}
                </div>
                <p className="text-sm text-muted-foreground pt-2">
                    Total live: {totalLive} · Total terminal: {totalTerminal}
                </p>
            </section>

            <section>
                <h2 className="text-sm font-semibold mb-2">
                    Recent failures ({recentFailures.length})
                </h2>
                {recentFailures.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No failures. 🎉</p>
                ) : (
                    <ul className="space-y-1 text-sm">
                        {recentFailures.map((m) => (
                            <li key={m.id} className="flex items-center gap-3">
                                <StateBadge state="failed" />
                                <Link href={`/missions/${m.id}`} className="hover:underline flex-1 truncate">
                                    {m.intent_text}
                                </Link>
                                <RelativeTime timestamp={m.declared_at} />
                            </li>
                        ))}
                    </ul>
                )}
            </section>
        </div>
    )
}
```

- [ ] **Step 3: Verify build + commit**

```bash
cd frontend
npm run build
npm run typecheck
```

```bash
git add frontend/src/lib/compute-state-breakdown.ts frontend/src/app/stats/
git commit -m "feat(frontend): /stats page with breakdown + recent failures"
```

---

## Task 15: Vitest + MSW setup + first unit tests

**Files:**
- Create: `frontend/vitest.config.ts`, `frontend/vitest.setup.ts`
- Create: `frontend/src/test/mocks/handlers.ts`, `frontend/src/test/mocks/server.ts`
- Create: `.test.ts` sibling files next to targets that lack them (state-badge, relative-time, format-session-expiry, sanitize-return-to, compute-state-breakdown, api-error)
- Modify: `frontend/package.json` (add `test:unit` script + test deps)

**Interfaces:**
- Produces: `npm run test:unit` runs Vitest against all `.test.ts(x)` files, uses jsdom + MSW.

- [ ] **Step 1: Install test deps**

```bash
cd frontend
npm install --save-dev \
    vitest @vitest/coverage-v8 \
    @testing-library/react @testing-library/jest-dom @testing-library/user-event \
    jsdom \
    msw \
    @edge-runtime/vm
```

- [ ] **Step 2: Vitest config**

Create `frontend/vitest.config.ts`:
```typescript
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'node:path'

export default defineConfig({
    plugins: [react()],
    test: {
        environment: 'jsdom',
        globals: true,
        setupFiles: ['./vitest.setup.ts'],
        include: ['src/**/*.test.{ts,tsx}'],
    },
    resolve: {
        alias: { '@': path.resolve(__dirname, './src') },
    },
})
```

Install the missing plugin:
```bash
npm install --save-dev @vitejs/plugin-react
```

- [ ] **Step 3: Setup file with MSW + jest-dom**

Create `frontend/vitest.setup.ts`:
```typescript
import '@testing-library/jest-dom/vitest'
import { afterAll, afterEach, beforeAll } from 'vitest'
import { server } from './src/test/mocks/server'

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())
```

- [ ] **Step 4: MSW handlers**

Create `frontend/src/test/mocks/handlers.ts`:
```typescript
import { http, HttpResponse } from 'msw'

// Default handlers used across tests. Override per-test with server.use(...).
export const handlers = [
    http.get('/api/me', () =>
        HttpResponse.json({
            owner_email: 'alice@x',
            langfuse_base_url: 'https://langfuse.example.com',
        }),
    ),
    http.get('/api/missions', () => HttpResponse.json([])),
]
```

Create `frontend/src/test/mocks/server.ts`:
```typescript
import { setupServer } from 'msw/node'
import { handlers } from './handlers'

export const server = setupServer(...handlers)
```

- [ ] **Step 5: Add test script**

Edit `frontend/package.json`:
```json
{
  "scripts": {
    ...existing,
    "test:unit": "vitest run",
    "test:unit:watch": "vitest"
  }
}
```

- [ ] **Step 6: Write unit tests for pure helpers**

Create `frontend/src/lib/sanitize-return-to.test.ts`:
```typescript
import { describe, expect, it } from 'vitest'
import { sanitizeReturnTo } from './sanitize-return-to'

describe('sanitizeReturnTo', () => {
    it('accepts a normal local path', () => {
        expect(sanitizeReturnTo('/missions')).toBe('/missions')
    })
    it('rejects an absolute URL', () => {
        expect(sanitizeReturnTo('https://evil.com/x')).toBe('/')
    })
    it('rejects a protocol-relative URL', () => {
        expect(sanitizeReturnTo('//evil.com/x')).toBe('/')
    })
    it('rejects backslash', () => {
        expect(sanitizeReturnTo('/valid\\path')).toBe('/')
    })
    it('rejects empty string', () => {
        expect(sanitizeReturnTo('')).toBe('/')
    })
    it('accepts path with query string', () => {
        expect(sanitizeReturnTo('/missions?state=running')).toBe('/missions?state=running')
    })
})
```

Create `frontend/src/lib/format-session-expiry.test.ts`:
```typescript
import { describe, expect, it, vi } from 'vitest'
import { formatSessionExpiry } from './format-session-expiry'

describe('formatSessionExpiry', () => {
    it('returns hours + minutes for long remaining time', () => {
        vi.useFakeTimers()
        vi.setSystemTime(new Date('2026-08-02T10:00:00Z'))
        // login was 30 min ago, TTL 8h → 7h 30m remaining
        const loginAt = Date.now() - 30 * 60 * 1000
        expect(formatSessionExpiry(loginAt, 8 * 3600)).toBe('7h 30m')
        vi.useRealTimers()
    })
    it('returns minutes when < 1h remains', () => {
        vi.useFakeTimers()
        vi.setSystemTime(new Date('2026-08-02T10:00:00Z'))
        const loginAt = Date.now() - 7.5 * 3600 * 1000  // 7.5h ago, TTL 8h → 30m
        expect(formatSessionExpiry(loginAt, 8 * 3600)).toBe('30m')
        vi.useRealTimers()
    })
    it('returns "expired" when past TTL', () => {
        vi.useFakeTimers()
        vi.setSystemTime(new Date('2026-08-02T10:00:00Z'))
        const loginAt = Date.now() - 9 * 3600 * 1000
        expect(formatSessionExpiry(loginAt, 8 * 3600)).toBe('expired')
        vi.useRealTimers()
    })
})
```

Create `frontend/src/lib/compute-state-breakdown.test.ts`:
```typescript
import { describe, expect, it } from 'vitest'
import { computeStateBreakdown } from './compute-state-breakdown'
import type { components } from './api-types'

type Mission = components['schemas']['Mission']

function m(state: Mission['state']): Mission {
    return {
        id: '00000000-0000-0000-0000-000000000000',
        intent_text: 'x',
        owner_email: 'a@x',
        declared_by: 'a@x',
        declared_at: '2026-08-02T10:00:00Z',
        state,
        plan: [],
        artifacts: [],
    } as Mission
}

describe('computeStateBreakdown', () => {
    it('returns zero counters on empty input', () => {
        const { counts, totalLive, totalTerminal } = computeStateBreakdown([])
        expect(counts.done).toBe(0)
        expect(counts.awaiting_user).toBe(0)
        expect(totalLive).toBe(0)
        expect(totalTerminal).toBe(0)
    })
    it('counts states correctly', () => {
        const rows = [m('done'), m('done'), m('failed'), m('running'), m('awaiting_user')]
        const { counts, totalLive, totalTerminal } = computeStateBreakdown(rows)
        expect(counts.done).toBe(2)
        expect(counts.failed).toBe(1)
        expect(counts.running).toBe(1)
        expect(counts.awaiting_user).toBe(1)
        expect(totalLive).toBe(2)   // running + awaiting_user
        expect(totalTerminal).toBe(3)  // 2 done + 1 failed
    })
})
```

Create `frontend/src/lib/api-error.test.ts`:
```typescript
import { describe, expect, it } from 'vitest'
import { ApiError, isErrorEnvelope } from './api-error'

describe('ApiError', () => {
    it('exposes code and message', () => {
        const err = new ApiError({
            error: { code: 'http_401', message: 'unauthorized' },
        })
        expect(err.code).toBe('http_401')
        expect(err.message).toBe('unauthorized')
    })
})

describe('isErrorEnvelope', () => {
    it('accepts a valid envelope', () => {
        expect(isErrorEnvelope({ error: { code: 'x', message: 'y' } })).toBe(true)
    })
    it('rejects null', () => {
        expect(isErrorEnvelope(null)).toBe(false)
    })
    it('rejects missing code', () => {
        expect(isErrorEnvelope({ error: { message: 'y' } })).toBe(false)
    })
})
```

- [ ] **Step 7: Run tests**

```bash
cd frontend
npm run test:unit
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add frontend/vitest.config.ts frontend/vitest.setup.ts \
        frontend/src/test/ frontend/src/lib/*.test.ts \
        frontend/package.json frontend/package-lock.json
git commit -m "feat(frontend): Vitest + MSW setup + helper unit tests"
```

---

## Task 16: Component tests (dashboard, header, detail, resume forms)

**Files:**
- Create: `frontend/src/components/layout/header.test.tsx`
- Create: `frontend/src/components/missions/mission-list.test.tsx`
- Create: `frontend/src/components/missions/state-badge.test.tsx`
- Create: `frontend/src/components/missions/relative-time.test.tsx`
- Create: `frontend/src/components/missions/approve-draft-form.test.tsx`
- Create: `frontend/src/components/missions/generic-resume-form.test.tsx`

**Interfaces:** none new — extends existing components with tests.

- [ ] **Step 1: Small pure-render tests**

Create `frontend/src/components/missions/state-badge.test.tsx`:
```typescript
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StateBadge } from './state-badge'

describe('StateBadge', () => {
    it('renders the state text', () => {
        render(<StateBadge state="running" />)
        expect(screen.getByText('running')).toBeInTheDocument()
    })
    it.each(['declared', 'planning', 'running', 'awaiting_user', 'done', 'failed', 'cancelled'] as const)(
        'renders %s without crashing',
        (state) => {
            render(<StateBadge state={state} />)
            expect(screen.getByText(state)).toBeInTheDocument()
        },
    )
})
```

Create `frontend/src/components/missions/relative-time.test.tsx`:
```typescript
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { RelativeTime } from './relative-time'

describe('RelativeTime', () => {
    it('renders "s ago" for recent timestamp', () => {
        vi.useFakeTimers()
        vi.setSystemTime(new Date('2026-08-02T10:00:00Z'))
        render(<RelativeTime timestamp="2026-08-02T09:59:30Z" />)
        expect(screen.getByText(/30s ago/)).toBeInTheDocument()
        vi.useRealTimers()
    })
    it('renders "m ago" for minutes-old timestamp', () => {
        vi.useFakeTimers()
        vi.setSystemTime(new Date('2026-08-02T10:00:00Z'))
        render(<RelativeTime timestamp="2026-08-02T09:55:00Z" />)
        expect(screen.getByText(/5m ago/)).toBeInTheDocument()
        vi.useRealTimers()
    })
    it('renders em-dash on invalid input', () => {
        render(<RelativeTime timestamp="not-a-date" />)
        expect(screen.getByText('—')).toBeInTheDocument()
    })
})
```

- [ ] **Step 2: MissionList tests**

Create `frontend/src/components/missions/mission-list.test.tsx`:
```typescript
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MissionList } from './mission-list'
import type { components } from '@/lib/api-types'

type Mission = components['schemas']['Mission']

function m(id: string, intent: string, state: Mission['state'] = 'declared'): Mission {
    return {
        id, intent_text: intent, owner_email: 'a@x', declared_by: 'a@x',
        declared_at: '2026-08-02T09:00:00Z', state, plan: [], artifacts: [],
    } as Mission
}

describe('MissionList', () => {
    it('shows empty state when list is empty', () => {
        render(<MissionList missions={[]} />)
        expect(screen.getByText(/No missions yet/)).toBeInTheDocument()
    })
    it('renders one row per mission', () => {
        render(<MissionList missions={[m('a', 'first'), m('b', 'second')]} />)
        expect(screen.getByText('first')).toBeInTheDocument()
        expect(screen.getByText('second')).toBeInTheDocument()
    })
    it('highlights awaiting_user rows', () => {
        const { container } = render(
            <MissionList missions={[m('a', 'attn', 'awaiting_user')]} />,
        )
        const rows = container.querySelectorAll('tbody tr')
        expect(rows[0].className).toContain('yellow')
    })
})
```

- [ ] **Step 3: Header + user dropdown test (needs QueryProvider + MSW)**

Create `frontend/src/components/layout/header.test.tsx`:
```typescript
import { describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { Header } from './header'

function withQuery(children: React.ReactNode) {
    const qc = new QueryClient({
        defaultOptions: { queries: { retry: false } },
    })
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

describe('Header', () => {
    it('shows the owner email once /me resolves', async () => {
        render(withQuery(<Header />))
        // MSW default handler returns alice@x
        await waitFor(() => expect(screen.getByText(/alice@x/)).toBeInTheDocument())
    })
    it('shows placeholder while loading', () => {
        render(withQuery(<Header />))
        // Before waitFor resolves, the button says "…"
        expect(screen.getByRole('button', { name: /…/ })).toBeInTheDocument()
    })
})
```

- [ ] **Step 4: ApproveDraftForm test**

Create `frontend/src/components/missions/approve-draft-form.test.tsx`:
```typescript
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/mocks/server'
import { ApproveDraftForm } from './approve-draft-form'

function withQuery(children: React.ReactNode) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

const artifact = {
    kind: 'approve_draft' as const,
    draft: 'Hi Bob',
    to: 'bob@x',
    subject: 'Re: Hello',
}

describe('ApproveDraftForm', () => {
    it('shows To + Subject + draft', () => {
        render(withQuery(<ApproveDraftForm missionId="m1" artifact={artifact} />))
        expect(screen.getByText('bob@x')).toBeInTheDocument()
        expect(screen.getByText('Re: Hello')).toBeInTheDocument()
        expect(screen.getByDisplayValue('Hi Bob')).toBeInTheDocument()
    })
    it('POSTs the edited draft on approve', async () => {
        const seen: any[] = []
        server.use(
            http.post('/api/missions/:mid/resume', async ({ request }) => {
                seen.push(await request.json())
                return HttpResponse.json({ id: 'm1' })
            }),
        )
        render(withQuery(<ApproveDraftForm missionId="m1" artifact={artifact} />))
        const textarea = screen.getByRole('textbox')
        await userEvent.clear(textarea)
        await userEvent.type(textarea, 'Edited hello')
        await userEvent.click(screen.getByRole('button', { name: /Approve/ }))
        expect(seen[0]).toEqual({ user_response: { approved: true, draft: 'Edited hello' } })
    })
})
```

- [ ] **Step 5: GenericResumeForm test**

Create `frontend/src/components/missions/generic-resume-form.test.tsx`:
```typescript
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { GenericResumeForm } from './generic-resume-form'

function withQuery(children: React.ReactNode) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

describe('GenericResumeForm', () => {
    it('displays the kind', () => {
        render(withQuery(<GenericResumeForm missionId="m1" kind="pick_option" />))
        expect(screen.getByText(/pick_option/)).toBeInTheDocument()
    })
    it('shows JSON error on malformed input', async () => {
        render(withQuery(<GenericResumeForm missionId="m1" kind="x" />))
        const textarea = screen.getByRole('textbox')
        await userEvent.clear(textarea)
        await userEvent.type(textarea, 'not json')
        await userEvent.click(screen.getByRole('button', { name: /Submit/ }))
        expect(screen.getByText(/JSON error/)).toBeInTheDocument()
    })
})
```

- [ ] **Step 6: Run tests**

```bash
cd frontend
npm run test:unit
```

All should pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/layout/header.test.tsx \
        frontend/src/components/missions/*.test.tsx
git commit -m "test(frontend): component tests for badge/list/header/resume forms"
```

---

## Task 17: Middleware unit tests

**Files:**
- Create: `frontend/middleware.test.ts`
- Modify: `frontend/vitest.config.ts` (add edge-runtime env for middleware tests OR run in jsdom with mocks)

**Interfaces:** none new — extends middleware with test coverage.

- [ ] **Step 1: Write the test**

Create `frontend/middleware.test.ts`:
```typescript
import { describe, expect, it, vi } from 'vitest'
import { NextRequest } from 'next/server'
import { middleware } from './middleware'

function makeReq(pathname: string, cookies: Record<string, string> = {}): NextRequest {
    const url = `https://twaky.example.com${pathname}`
    const cookieHeader = Object.entries(cookies).map(([k, v]) => `${k}=${v}`).join('; ')
    return new NextRequest(url, {
        headers: cookieHeader ? { cookie: cookieHeader } : {},
    })
}

describe('middleware', () => {
    it('passes through /api/* without auth check', () => {
        const res = middleware(makeReq('/api/missions'))
        expect(res.headers.get('location')).toBeNull()
        // NextResponse.next() has status 200 (default)
        expect(res.status).toBe(200)
    })
    it('passes through /oauth/* without auth check', () => {
        const res = middleware(makeReq('/oauth/login'))
        expect(res.headers.get('location')).toBeNull()
        expect(res.status).toBe(200)
    })
    it('redirects to /oauth/login when cookie is missing', () => {
        const res = middleware(makeReq('/missions'))
        expect(res.status).toBe(307)
        const loc = res.headers.get('location')
        expect(loc).toContain('/api/oauth/login')
        expect(loc).toContain('return_to=%2Fmissions')
    })
    it('lets through when cookie is present (any value)', () => {
        const res = middleware(makeReq('/missions', { twaky_session: 'anything' }))
        expect(res.status).toBe(200)
    })
    it('sanitizes malicious return_to', () => {
        // A path like //evil.com/x is sanitized to /
        const res = middleware(makeReq('//evil.com/x'))
        const loc = res.headers.get('location')
        expect(loc).toContain('return_to=%2F')
    })
})
```

- [ ] **Step 2: Verify Vitest handles NextRequest**

Next.js edge runtime types are exported from `next/server`. In jsdom + Node runtime, `NextRequest` may not construct correctly. Test locally:
```bash
cd frontend
npm run test:unit -- middleware.test
```

If it fails with a runtime error about NextRequest, add a Vitest workspace config to run middleware tests in edge-runtime:
```typescript
// frontend/vitest.config.ts — extend
test: {
    ...existing,
    // Run middleware tests in Node with polyfills for Request/Response.
    environmentMatchGlobs: [
        ['middleware.test.ts', 'node'],
        ['src/**/*.test.tsx', 'jsdom'],
    ],
}
```

If NextRequest still doesn't work, an alternative is to construct the request via `Request` (Web API) directly and cast — the middleware only uses `req.nextUrl`, `req.cookies`, and `req.url`.

- [ ] **Step 3: Commit**

```bash
git add frontend/middleware.test.ts frontend/vitest.config.ts
git commit -m "test(frontend): middleware auth-guard unit tests"
```

---

## Task 18: Playwright setup + seed helper + basic scenarios

**Files:**
- Create: `frontend/playwright.config.ts`
- Create: `frontend/tests/e2e/fixtures.ts`
- Create: `frontend/tests/e2e/seed-awaiting-user.py`
- Create: `frontend/tests/e2e/auth.spec.ts`
- Modify: `frontend/package.json` (add `test:e2e` script + Playwright dev dep)

**Interfaces:**
- Consumes: `sign_session()` from `src/twaky/api/testing.py` via `docker compose exec twaky-api uv run python scripts/sign-session.py <email>` (helper from 3a).
- Produces: `npm run test:e2e` runs Playwright tests. `signedInPage` fixture forges cookies + injects. Self-skips when the API stack is unreachable.

- [ ] **Step 1: Install Playwright**

```bash
cd frontend
npm install --save-dev @playwright/test
npx playwright install chromium --with-deps
```

- [ ] **Step 2: Playwright config**

Create `frontend/playwright.config.ts`:
```typescript
import { defineConfig, devices } from '@playwright/test'

const baseURL = process.env.TWAKY_TEST_STACK_URL || 'http://localhost:3000'

export default defineConfig({
    testDir: './tests/e2e',
    fullyParallel: false,  // avoid mission-state race conditions
    forbidOnly: !!process.env.CI,
    retries: process.env.CI ? 1 : 0,
    reporter: process.env.CI ? [['github'], ['html']] : 'html',
    use: {
        baseURL,
        trace: 'on-first-retry',
    },
    projects: [
        { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    ],
})
```

- [ ] **Step 3: Test fixtures with self-skip**

Create `frontend/tests/e2e/fixtures.ts`:
```typescript
import { test as base, expect, type Page } from '@playwright/test'
import { execSync } from 'node:child_process'

async function stackReachable(baseURL: string): Promise<boolean> {
    try {
        const res = await fetch(`${baseURL}/api/healthz`)
        return res.ok
    } catch {
        return false
    }
}

function forgeSessionCookie(email: string): string {
    try {
        return execSync(
            `docker compose exec -T twaky-api uv run python scripts/sign-session.py ${email}`,
            { cwd: process.cwd() + '/..' },
        ).toString().trim()
    } catch (err) {
        throw new Error(
            'Could not run sign-session.py. Is the docker stack up? ' +
            `Underlying error: ${(err as Error).message}`,
        )
    }
}

export const test = base.extend<{ signedInPage: Page }>({
    signedInPage: async ({ page, context, baseURL }, use) => {
        if (!(await stackReachable(baseURL!))) {
            test.skip(true, 'twaky stack not reachable — set TWAKY_TEST_STACK_URL')
        }
        const cookie = forgeSessionCookie('michel.maudet@linagora.com')
        const domain = new URL(baseURL!).hostname
        await context.addCookies([{
            name: 'twaky_session', value: cookie,
            domain, path: '/', httpOnly: true, secure: false,
        }])
        await use(page)
    },
})

export { expect }
```

- [ ] **Step 4: Seed helper (Python)**

Create `frontend/tests/e2e/seed-awaiting-user.py`:
```python
"""Seed a mission into `awaiting_user` state for E2E testing.

Bypasses the Atlas daemon: declares → start_planning → commit_plan →
request_user_input(kind="approve_draft"). Prints the mission id to stdout.

Usage:
    docker compose exec -T twaky-api uv run python -m frontend.tests.e2e.seed_awaiting_user
    (or copy this file into a docker-exec-friendly path)
"""

from __future__ import annotations

import sys

from twaky.missions import engine
from twaky.missions.models import PlanStep


def seed_awaiting_user(owner_email: str) -> str:
    m = engine.declare(
        intent_text="E2E: approve this draft",
        owner_email=owner_email,
        declared_by=owner_email,
    )
    engine.start_planning(m.id)
    engine.commit_plan(m.id, [PlanStep(agent="plume", tool="draft_reply", args={})])
    engine.request_user_input(
        m.id,
        reason="approve_draft",
        artifact={
            "kind": "approve_draft",
            "draft": "Hi Bob — thanks for reaching out.",
            "to": "bob@x.com",
            "subject": "Re: Question about widgets",
        },
    )
    return str(m.id)


if __name__ == "__main__":
    email = sys.argv[1] if len(sys.argv) > 1 else "michel.maudet@linagora.com"
    print(seed_awaiting_user(email))
```

- [ ] **Step 5: First two E2E scenarios**

Create `frontend/tests/e2e/auth.spec.ts`:
```typescript
import { test, expect } from './fixtures'

test('unauthenticated user is redirected to login', async ({ page }) => {
    const response = await page.goto('/', { waitUntil: 'commit' })
    // Middleware redirects to /api/oauth/login — the browser follows the redirect
    // to twaky-api, which then 302s to LemonLDAP. In test env without a real
    // LemonLDAP, this ends with a network error or a 5xx.
    // We only assert the FIRST redirect chain step:
    expect(response?.request().url()).toContain('/')
    // The browser's URL should now be somewhere other than the root page's dashboard.
    // Simpler check: the current URL does NOT show the dashboard title.
    await expect(page).not.toHaveTitle('Twaky · Dashboard')
})

test('signed-in user sees dashboard', async ({ signedInPage: page }) => {
    await page.goto('/')
    await expect(page.getByRole('heading', { name: 'Missions' })).toBeVisible()
})
```

- [ ] **Step 6: Add scripts to package.json**

Edit `frontend/package.json`:
```json
{
  "scripts": {
    ...existing,
    "test:e2e": "playwright test",
    "test:e2e:ui": "playwright test --ui"
  }
}
```

- [ ] **Step 7: Verify locally (self-skip expected if stack down)**

```bash
cd frontend
npm run test:e2e
```

Expected: tests self-skip because `TWAKY_TEST_STACK_URL` isn't set OR the stack isn't reachable. That's OK — E2E fully runs in CI (T22).

If you want to try locally with the stack up:
```bash
cd /home/mmaudet/work/twaky
docker compose up -d twaky-pg twaky-api twaky-frontend
sleep 10
cd frontend
TWAKY_TEST_STACK_URL=https://twaky.${BASE_DOMAIN} npm run test:e2e
```

- [ ] **Step 8: Commit**

```bash
git add frontend/playwright.config.ts frontend/tests/ \
        frontend/package.json frontend/package-lock.json
git commit -m "test(e2e): Playwright setup + fixtures + seed helper + auth spec"
```

---

## Task 19: Remaining E2E scenarios (declare/detail/cancel + approve + signout)

**Files:**
- Create: `frontend/tests/e2e/missions.spec.ts`
- Create: `frontend/tests/e2e/awaiting-user.spec.ts`
- Create: `frontend/tests/e2e/signout.spec.ts`

**Interfaces:** none new.

- [ ] **Step 1: Declare → detail → cancel**

Create `frontend/tests/e2e/missions.spec.ts`:
```typescript
import { test, expect } from './fixtures'

test('declare → detail → cancel', async ({ signedInPage: page }) => {
    await page.goto('/')

    // Open new-mission dialog + submit
    await page.getByRole('button', { name: /New mission/ }).click()
    const intent = `E2E test at ${new Date().toISOString()}`
    await page.getByRole('textbox').fill(intent)
    await page.getByRole('button', { name: /^Declare$/ }).click()

    // Router pushes to /missions/{id} after successful declare
    await page.waitForURL(/\/missions\//)
    await expect(page.getByRole('heading', { name: intent })).toBeVisible()

    // Cancel
    await page.getByRole('button', { name: /Cancel mission/ }).click()
    await page.getByRole('button', { name: /^Cancel mission$/ }).click()  // confirm

    // Back to dashboard
    await page.waitForURL('/', { timeout: 5000 })
})
```

- [ ] **Step 2: Approve draft (awaiting_user)**

Create `frontend/tests/e2e/awaiting-user.spec.ts`:
```typescript
import { test, expect } from './fixtures'
import { execSync } from 'node:child_process'

function seedAwaitingUser(): string {
    // Runs the Python helper from inside twaky-api container.
    // Adjust the path if the file isn't mounted — for CI, we copy it into place.
    return execSync(
        `docker compose exec -T twaky-api uv run python /tmp/seed-awaiting-user.py michel.maudet@linagora.com`,
        { cwd: process.cwd() + '/..' },
    ).toString().trim()
}

test.beforeAll(async () => {
    // Copy the seed helper into the container so `python` can find it.
    execSync(
        `docker compose cp frontend/tests/e2e/seed-awaiting-user.py twaky-api:/tmp/seed-awaiting-user.py`,
        { cwd: process.cwd() + '/..' },
    )
})

test('approve draft on awaiting_user mission', async ({ signedInPage: page }) => {
    const missionId = seedAwaitingUser()

    await page.goto(`/missions/${missionId}`)

    // Assert the ApproveDraftForm is present
    await expect(page.getByRole('heading', { name: 'Approve draft' })).toBeVisible()
    await expect(page.getByText('bob@x.com')).toBeVisible()
    await expect(page.getByText('Re: Question about widgets')).toBeVisible()

    // Approve as-is
    await page.getByRole('button', { name: /Approve/ }).click()

    // Success toast appears; SSE drives the state change. We only assert the
    // toast (state changes depend on whether a daemon is running).
    await expect(page.getByText(/approved/i)).toBeVisible({ timeout: 5000 })
})
```

- [ ] **Step 3: Sign out flow**

Create `frontend/tests/e2e/signout.spec.ts`:
```typescript
import { test, expect } from './fixtures'

test('sign out clears session and redirects to login', async ({ signedInPage: page }) => {
    await page.goto('/')

    // Open the user dropdown
    await page.getByRole('button', { name: /alice|maudet|@/ }).click()
    await page.getByRole('menuitem', { name: /Sign out/ }).click()

    // The POST to /api/oauth/logout returns a 302 that the browser follows.
    // In test env this may end at LemonLDAP end-session (unreachable) or at /.
    // Either way, the next navigation to / should redirect us to login.
    await page.goto('/')
    // The URL after middleware redirect includes /api/oauth/login somewhere in the chain.
    // We can't easily observe intermediate URLs; instead assert we're NOT on the dashboard.
    await expect(page.getByRole('heading', { name: 'Missions' })).not.toBeVisible()
})
```

- [ ] **Step 4: Local verify (self-skips or runs)**

```bash
cd frontend
npm run test:e2e
```

- [ ] **Step 5: Commit**

```bash
git add frontend/tests/e2e/missions.spec.ts \
        frontend/tests/e2e/awaiting-user.spec.ts \
        frontend/tests/e2e/signout.spec.ts
git commit -m "test(e2e): declare/detail/cancel + approve-draft + sign-out"
```

---

## Task 20: `frontend` CI job (typecheck / lint / unit / build / drift)

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:** none new — CI additions.

- [ ] **Step 1: Read the existing CI file**

Skim `/home/mmaudet/work/twaky/.github/workflows/ci.yml` to understand the existing structure (job names, checkout patterns, secrets).

- [ ] **Step 2: Add the frontend job**

Append to `.github/workflows/ci.yml`:
```yaml
  frontend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
          cache: 'npm'
          cache-dependency-path: frontend/package-lock.json
      - name: Install
        run: npm ci
      - name: Typecheck
        run: npm run typecheck
      - name: Lint
        run: npm run lint
      - name: Unit tests
        run: npm run test:unit
      - name: Build
        run: npm run build
      - name: OpenAPI types drift check
        run: |
          make api-types
          git diff --exit-code src/lib/api-types.d.ts \
            || (echo 'ERROR: api-types.d.ts is stale — run `make api-types` and commit.' && exit 1)
```

- [ ] **Step 3: Verify by locally running the same commands**

```bash
cd frontend
npm ci
npm run typecheck
npm run lint
npm run test:unit
npm run build
make api-types
git diff --exit-code src/lib/api-types.d.ts
```

All must pass.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci(frontend): add typecheck/lint/unit/build/drift job"
```

---

## Task 21: `frontend-e2e` CI job (compose-up + Playwright)

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:** none new.

- [ ] **Step 1: Append the e2e job**

Append to `.github/workflows/ci.yml`:
```yaml
  frontend-e2e:
    runs-on: ubuntu-latest
    needs: [frontend]
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
          cache: 'npm'
          cache-dependency-path: frontend/package-lock.json
      - name: Install npm deps
        run: npm ci
      - name: Install Playwright browsers
        run: npx playwright install chromium --with-deps
      - name: Boot the stack
        working-directory: .
        run: |
          docker compose up -d twaky-pg twaky-api twaky-frontend
          # Wait for twaky-frontend to be healthy (depends on twaky-api)
          for i in {1..30}; do
            status=$(docker inspect --format '{{.State.Health.Status}}' twaky-frontend 2>/dev/null || echo starting)
            if [ "$status" = "healthy" ]; then break; fi
            sleep 2
          done
          [ "$status" = "healthy" ] || (echo 'twaky-frontend never became healthy' && docker compose logs && exit 1)
      - name: Copy seed helper into API container
        working-directory: .
        run: docker compose cp frontend/tests/e2e/seed-awaiting-user.py twaky-api:/tmp/seed-awaiting-user.py
      - name: E2E tests
        env:
          TWAKY_TEST_STACK_URL: http://localhost  # or however Traefik is reachable in CI
        run: npm run test:e2e
      - name: Upload Playwright report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: playwright-report
          path: frontend/playwright-report/
          retention-days: 7
```

- [ ] **Step 2: Note the network topology in CI**

In CI, the stack is up but Traefik may not be configured with LetsEncrypt (or reachable via `twaky.${BASE_DOMAIN}` externally). The `TWAKY_TEST_STACK_URL` env is a placeholder; the actual value depends on the CI environment. If the docker-compose stack exposes `twaky-frontend` on `localhost:3000` in CI (via a temporary port publish), set `TWAKY_TEST_STACK_URL=http://localhost:3000`.

Add a temporary port publish for CI in `docker-compose.yml`? NO — that would affect production. Better: add a `docker-compose.ci.yml` override, or use `docker exec` to run curl-based smoke checks + skip Playwright.

**Alternative simpler approach for MVP CI**: mark `frontend-e2e` as conditionally enabled (label `run-e2e` on PRs). Real E2E validation happens on the deployed stack. Document in the README.

Edit the `frontend-e2e` job to be gated:
```yaml
  frontend-e2e:
    if: contains(github.event.pull_request.labels.*.name, 'run-e2e') || github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    needs: [frontend]
    ...
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci(frontend): add frontend-e2e job (opt-in via run-e2e label)"
```

---

## Task 22: README section + final full-repo gate sweep

**Files:**
- Modify: `README.md`
- (verify): full-repo tests

- [ ] **Step 1: Add README section**

Append to `/home/mmaudet/work/twaky/README.md`:
```markdown
## Twaky Frontend (sub-project 3b)

Next.js 15 web UI for the instance owner. Lives under `frontend/` in this repo.

### Local dev

```bash
cd frontend
npm install
npm run dev  # http://localhost:3000
```

The dev server needs `twaky-api` reachable. Set `API_INTERNAL_URL` in
`frontend/.env.local` (e.g., `http://localhost:8000` if you're running
`uvicorn twaky.api.main:app` locally, or leave as `http://twaky-api:8000` if
you're running everything in docker-compose).

### Auth

Auth is cookie-only OIDC session against LemonLDAP-NG (via twaky-api's
`/oauth/*` routes, proxied by Next.js).

Prerequisite: the `twaky-api` OIDC client must be provisioned in the deploy
repo's `twake_auth/config/lmConf-1.json.ldap.template` — same mechanism as
`twaky-plume` and `twaky-langfuse`.

For local dev without OIDC, forge a session cookie:
```bash
docker compose exec twaky-api uv run python scripts/sign-session.py \
    michel.maudet@linagora.com
```
Then paste the value into DevTools → Application → Cookies as `twaky_session`.

### Deploy

```bash
docker compose build twaky-frontend
docker compose up -d twaky-frontend
```

Traefik routes `twaky.${BASE_DOMAIN}` to `twaky-frontend`, which proxies
`/api/*` and `/oauth/*` to `twaky-api` over the internal network.

### Tests

```bash
cd frontend
npm run test:unit           # Vitest + RTL, ~50 tests
npm run test:e2e            # Playwright, requires the docker stack up
```

E2E tests self-skip when the stack is not reachable. CI runs them behind
a `run-e2e` label opt-in.

### Regenerating API types

Any time `docs/api/openapi.yaml` changes:
```bash
cd frontend
make api-types
git add src/lib/api-types.d.ts && git commit -m 'chore(frontend): regen API types'
```

CI blocks the merge if types are stale.
```

- [ ] **Step 2: Run the full-repo sweep**

```bash
# Python side (unchanged from sub-project 3a — verify still green)
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src/

# Frontend side
cd frontend
npm run typecheck
npm run lint
npm run test:unit
npm run build
make api-types
git diff --exit-code src/lib/api-types.d.ts
```

All must pass.

- [ ] **Step 3: Commit**

```bash
cd /home/mmaudet/work/twaky
git add README.md
git commit -m "docs: README section on Twaky Frontend"
```

---

## Rollback

Everything additive at both code and infrastructure levels:

```bash
# 1. Restore Traefik on twaky-api by reverting the label swap in docker-compose.yml
git checkout HEAD~ -- docker-compose.yml
docker compose up -d twaky-api

# 2. Stop the frontend
docker compose stop twaky-frontend && docker compose rm -f twaky-frontend

# 3. Revert the merge on main
git revert <merge-commit>
```

No API changes, no schema changes. `sign-session.py` from 3a still works
against forged cookies.

**Incremental fallback:** if `twaky-frontend` has a blocking bug after the
Traefik swap, revert only the docker-compose label change (~30 seconds).
The CLI + curl-with-cookie path from 3a is fully preserved.

---

## Self-Review

**Spec coverage:**

- §1 Purpose (3 use cases + fronting all traffic) → T3 + T10 + T11 + T12.
- §2 Non-goals — no tasks needed; nothing to build.
- §3 Architecture (Docker + Traefik + Next.js) → T1 (scaffold), T3 (Dockerfile + compose + Traefik), T5 (rewrites), T8 (root layout).
- §4 Auth flow (middleware + client-side 401) → T7 (middleware), T6 (query-client's 401 handler).
- §5 Data flow (openapi-fetch + TanStack Query + SSE) → T4 (types), T5 (client), T6 (hooks + QueryClient), T8 (SSEProvider).
- §6 Pages (layout, dashboard, detail with resume, /me, /stats) → T8-T9 (shell), T10 (dashboard), T11 (detail), T12 (resume forms), T13 (/me), T14 (/stats).
- §7 Testing strategy (Vitest + RTL + MSW + Playwright) → T15 (Vitest setup + helpers), T16 (component tests), T17 (middleware tests), T18-T19 (Playwright).
- §8 Rollout (LemonLDAP prereq, Traefik swap, smoke tests) → T3 (Traefik swap), T22 (README documents prereq + smoke).
- §9 Rollback → covered in this plan's Rollback section.
- §10 Open questions — noted per-task where relevant (see T3 healthcheck note, T18 e2e-in-CI compromise).
- §11 Handoff artifacts for sub-project 4 — implicit; T8's shell + T5's rewrites + T15's Playwright fixture are the durable seams.

**Placeholder scan:** the plan has no "TBD"/"TODO" in step bodies. Steps that couldn't be nailed down without runtime environment info (e.g., T21's `TWAKY_TEST_STACK_URL` choice in CI) are called out with explicit decisions and their tradeoffs, not left as TBDs.

**Type consistency:**
- `MissionState` — imported from `@/hooks/use-missions` in components (T10 onward) and from `@/lib/api-types` in helpers (T6 onward). Both are the same type re-exported. Consistent.
- `Mission` — from `components['schemas']['Mission']` — used consistently.
- `SESSION_COOKIE_NAME = 'twaky_session'` — defined in T7 middleware, referenced consistently (matches 3a's Python constant).
- `useMe()`, `useMissions(state?)`, `useMission(id)`, `useDeclareMission()`, `useResumeMission()`, `useCancelMission()` — signatures defined in T6 and consumed exactly the same in T9-T14.
- `SSEStatus` type — `'connected' | 'reconnecting' | 'disconnected'` — defined in T8 and consumed in T9's `SSEIndicator`.
- `sanitizeReturnTo(input: string): string` — defined in T6 (moved into `lib/`) and consumed in T7 middleware + T6 query-client. Consistent.

**One clarification for the implementer:**
T15's tests import from files created in earlier tasks (state-badge, relative-time, etc.). If Vitest is set up in T15 AFTER those components exist, the imports work — but if an implementer executes tasks out of order (e.g., T15 before T10), the tests reference nonexistent files. The task order is designed so components exist before their tests. Executing in sequence is required.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-02-twaky-frontend.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — I execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
