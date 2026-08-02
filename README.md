# twaky

Graph agentic brick for the Twake platform.

- Captures `sabre:*` and `calendar:*` fanout events from the shared RabbitMQ.
- Appends them to a replayable `event_log` in a **dedicated** Postgres+Apache-AGE instance.
- Projects them into a labeled property graph named `twake` via idempotent Cypher `MERGE`.
- Exposes a `GraphCypherQAChain` over the graph, model-abstracted by **LiteLLM**, traced by **Langfuse**.

Sibling repo — docks onto `twake-dev.maudet.cloud` via `twake-network`, does not modify the platform.

## Design

```
RabbitMQ (rabbitmq:5672)                Postgres+AGE (twaky-pg:5432)
  calendar:event:created ─┐               DB "twaky":
  sabre:contact:created ──┼──▶ ingest ──▶   event_log (jsonb, source of truth)
                          │                  graph "twake" (nodes + relationships)
                          │              DB "langfuse":
                          │                  Langfuse metadata
                          ▼
                  agent.graph.ingest  (durable + DLX/DLQ)
                          │
                          ├──▶ projector ──▶ Cypher MERGE ──▶ graph
                          │
                          └──▶ atlas (mission daemon + LangGraph checkpointer)
                                    ▲
                                    └── agent (GraphCypherQAChain + LiteLLM + Langfuse trace)
```

- **Zero-impact captation**: new queue is prefixed `agent.*`, bound in `fanout` mode without a routing key — RabbitMQ delivers a *copy* to us, existing consumers (`tcalendar:audit`, `tcalendar:event:*:notification`, etc.) keep their traffic.
- **Idempotence**: every projection is a Cypher `MERGE` keyed by a natural id (`email`, `event_uid`); replaying `event_log` from row 1 rebuilds the graph without duplicates.
- **Dedicated Postgres**: AGE is not installed in the platform's shared Postgres.
- **Read-only LLM path**: `GraphCypherQAChain` generates read-only Cypher; writes to the graph come only from the projector.

## Graph schema (initial)

Nodes:
- `Person { email, fn?, tel? }`
- `Organization { name }`
- `CalendarEvent { uid, summary?, start? }`
- `Email { message_id, subject? }` — reserved, no mapper yet
- `Thread { thread_id }` — reserved, no mapper yet

Relationships:
- `(:Person)-[:WORKS_AT]->(:Organization)`
- `(:Person)-[:ATTENDED]->(:CalendarEvent)`
- `(:Person)-[:ORGANIZED]->(:CalendarEvent)`

Extension is a matter of adding a mapper under `src/twaky/mappers/<exchange>.py` and a corresponding exchange to `AGENT_EXCHANGES` in the env.

## Missions (Foundations)

A twaky instance is scoped to a single owner (`TWAKY_OWNER_EMAIL` in `.env`).
Every event that doesn't concern the owner is dropped at ingest — the
`event_log` and graph stay owner-only.

Missions are the unit of orchestration. A Mission is declared by natural
language, planned by Atlas (sub-project 2), and traverses:

    declared → planning → running ⇄ awaiting_user → done | failed | cancelled

State lives in the `mission` Postgres table; the fine-grained per-mission
execution state lives in the LangGraph checkpointer (`checkpoints` table,
same DB). At Atlas boot, `recovery.resume_missions_after_restart()`
reconciles: missions with no checkpoint are marked `failed` with reason
`checkpoint_lost_after_restart`.

Run the end-to-end scenario:

    make scenarios-foundations

Mail metadata is ingested from `mail:message:{received,expunged,flags:updated,moved}` —
body fetching (JMAP) is deferred to sub-project 2.

The `twaky:message:*` federation envelope is documented in
`src/twaky/missions/envelope.py` but not wired — sub-project 4.

## Agents + Atlas (sub-project 2)

`twaky-atlas` is a daemon container that watches the mission table and
drives each `declared` mission through a LangGraph StateGraph — the
Supervisor pattern:

    Atlas (orchestrator LLM)
       ├─ delegate_to_chronos(query)   → Chronos StateGraph (calendar tools)
       ├─ delegate_to_plume(query)     → Plume StateGraph  (JMAP + drafts)
       ├─ delegate_to_iris(query)      → Iris StateGraph   (SearXNG + graph)
       └─ finish_mission(answer, outcome)

Configuration:

- `TWAKY_ATLAS_MAX_CONCURRENT_MISSIONS` (default 4) — bounded parallelism.
- `ATLAS_MODEL / CHRONOS_MODEL / PLUME_MODEL / IRIS_MODEL` — override the
  global `MODEL` per specialist. All fall back to `MODEL` when unset.
- Plume authenticates to JMAP via OIDC token exchange
  (`PLUME_OIDC_CLIENT_ID / _SECRET / _ISSUER`). Requires a `twaky-plume`
  client in LemonLDAP-NG — add it to the deploy repo's
  `twake_auth/config/lmConf-1.json.ldap.template`.
- Iris uses SearXNG on `twake-network` at `SEARXNG_ENDPOINT`.

Demo scenarios:

    bash scripts/seed-demo.sh
    twaky mission declare "Résume ma journée de demain" --wait
    twaky mission declare "Draft a reply to demo-msg-1" --wait
    twaky mission resume <mid> --input '{"approved": true}'

Or all in one:

    make scenarios-agents

Traces group under `mission-<id>` in Langfuse; cost by agent surfaces in
Metabase via ClickHouse tags.

## Consuming twaky-api (sub-project 3a)

`twaky-api` is a FastAPI container that exposes the mission engine over
REST + SSE. Auth is a cookie-only OIDC session against LemonLDAP-NG
(client `twaky-api` provisioned in the deploy repo).

Base URL (dev): `https://twaky.${BASE_DOMAIN}`

### Login flow (browser)

1. Navigate to `/oauth/login`.
2. Authenticate against LemonLDAP-NG.
3. Redirected back with a signed session cookie (HttpOnly, SameSite=Lax, 8h).

### CLI / test usage (bypass OIDC)

On the twaky-api container:

```bash
docker compose exec twaky-api uv run python scripts/sign-session.py \
    michel.maudet@linagora.com
# prints the signed cookie value
```

Then hit the API with `curl`:

```bash
COOKIE=<the cookie value>
curl -H "Cookie: twaky_session=$COOKIE" \
     https://twaky.${BASE_DOMAIN}/missions

curl -H "Cookie: twaky_session=$COOKIE" \
     -H "Content-Type: application/json" \
     -d '{"intent_text":"Résume ma journée de demain"}' \
     -X POST https://twaky.${BASE_DOMAIN}/missions
```

### SSE

```bash
curl -N -H "Cookie: twaky_session=$COOKIE" \
     https://twaky.${BASE_DOMAIN}/events
```

Emits one `mission_changed` event per state transition; keep-alive comment
every 15 s.

### OpenAPI schema

`docs/api/openapi.yaml` is the source of truth for client generation.
Regenerate with `make openapi`. CI enforces no drift.

Sub-project 3b (Frontend Control Tower) consumes this file via `openapi-typescript`:

```bash
# Regenerate typed stubs (run from frontend/)
cd frontend && make api-types

# Or spin a local mock backend for frontend dev
npx @stoplight/prism-cli mock docs/api/openapi.yaml
```

See the [Regenerating API types](#regenerating-api-types) section under "Twaky Frontend" for details.

### End-to-end scenario

```bash
make scenarios-api
```

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

## Agent configuration (sub-project 4)

Every built-in agent (Atlas, Chronos, Plume, Iris) is configurable at
runtime via the web UI at `/agents`. You edit the system prompt, model
string (LiteLLM syntax), and temperature; changes take effect on the
next sub-agent invocation — no `docker compose restart` required.

**Storage:** table `agent` on `twaky-pg`, seeded once from `sql/006_init_agents.sh`.

**Live-reload path:** the API's PATCH handler updates the row, a Postgres
`AFTER UPDATE` trigger fires `NOTIFY agent_config_changed`, and the atlas
daemon's `config_listener` task invalidates its cached `AgentConfig`.
The next call to `load_agent_config(id)` re-reads the fresh row.

**Fallbacks:**
- `agent.model = NULL` → daemon uses `settings.model` (env var `TWAKY_MODEL`).
- `agent.temperature = NULL` → daemon omits the parameter, LiteLLM's per-provider default applies.
- Row missing from the DB → daemon uses `DEFAULT_PROMPTS` from `src/twaky/agents/defaults.py` (belt-and-braces safety).

**In-flight missions:** a mission running during a save may see the new
config on its next sub-agent invocation. No per-mission snapshotting —
accepted trade-off (see design spec §4.4).

**Not in this sub-project (deferred to 5):** creating new agents,
editing tools, skill/connector store.

## Quickstart

```bash
cp .env.example .env
# Fill secrets (see the comment at the top of .env.example for openssl commands)

docker compose up -d twaky-pg              # Postgres+AGE, waits for healthy
docker compose up -d twaky-ingest twaky-projector twaky-atlas

# End-to-end smoke test:
docker compose run --rm twaky-agent twaky verify         # publishes a synthetic event
docker compose run --rm twaky-agent twaky ask "who attended twaky-verify-1?"
```

## Docking onto the dev platform

Add one line to `/home/mmaudet/deploy/kickstart-maudet-cloud/docker-compose.yaml`:

```yaml
include:
  - cozy_stack/docker-compose.yml
  - meet_app/docker-compose.yml
  - linshare_app/docker-compose.yml
  - twake_db/docker-compose.yml
  - twake_auth/docker-compose.yml
  - ../../work/twaky/docker-compose.yml    # ← twaky
```

Twaky's compose reads its own `.env` at `/home/mmaudet/work/twaky/.env`; the deployment-level `.env` is not used. This keeps twaky's secrets isolated.

## Configuration

See `.env.example`. Highlights:

| Var | Purpose |
|---|---|
| `AGENT_EXCHANGES` | comma-separated fanout exchanges the ingest binds to |
| `AGENT_QUEUE` | queue name (must start with `agent.` for hygiene) |
| `TWAKY_PG_*` | dedicated Postgres+AGE credentials |
| `MODEL` | LiteLLM model id (`claude-sonnet-4-5-...`, `hosted_vllm/...`, `ollama/...`) |
| `LITELLM_API_BASE` | override endpoint for self-hosted providers |
| `LANGFUSE_*` | server URL + public/secret keys (obtain from Langfuse UI on first boot) |

## Verify (see also `make verify`)

Once every service is up:

```bash
# T1 · Existing consumers untouched
docker exec rabbitmq rabbitmqctl list_bindings -p / | grep calendar:event:created
# → tcalendar:audit, tcalendar:event:created:notification, ..., agent.graph.ingest

# T2 · Synthetic event → event_log → graph
docker compose run --rm twaky-agent twaky verify
docker exec twaky-pg psql -U twaky -d twaky -c \
  "SELECT count(*) FROM event_log WHERE payload->>'uid'='twaky-verify-1';"

# T3 · Idempotence: re-publish the same event
docker compose run --rm twaky-agent twaky verify
# graph row count for CalendarEvent{uid='twaky-verify-1'} stays at 1

# T5 · Ask the graph
docker compose run --rm twaky-agent twaky ask \
  "who attended twaky-verify-1?"

# T6 · Automated tests
uv run pytest -q

# T7 · Trace visible in Langfuse UI
# The `twaky ask` output prints a Langfuse trace URL — open it in the browser.
```

## Backups

Langfuse v3 keeps state in three independent stores (Postgres, ClickHouse,
SeaweedFS S3). `scripts/backup.sh` dumps all three (plus the twaky graph DB)
into `/home/mmaudet/backups/twaky/YYYY-MM-DD/` and prunes anything older than
14 days. `scripts/restore.sh <DATE>` puts them back.

```bash
make backup                        # one-shot backup of all stores
make backup-dry                    # show what backup.sh would do
make restore DATE=2026-08-01       # restore all three stores from that date
```

Recommended cron (root, or any user in the `docker` group):

```
0 3 * * * /home/mmaudet/work/twaky/scripts/backup.sh >> /var/log/twaky-backup.log 2>&1
```

Full documentation — output layout, restore procedure, systemd-timer
alternative, disk usage estimates, troubleshooting — is in
[`scripts/backup.md`](scripts/backup.md).

## Security

- No secrets in git (`.env` is in `.gitignore`, only `.env.example` is committed).
- `guest/guest` on RabbitMQ is dev-only; before non-lab use, provision a dedicated user with `read` on `sabre:*`/`calendar:*` and no write.
- `twaky-pg` publishes **no host port** — reachable only from `twake-network`.
- Langfuse telemetry to `cloud.langfuse.com` is disabled (`TELEMETRY_ENABLED=false`).
- The QA chain is read-only: `GraphCypherQAChain` generates non-mutating Cypher; writes to the graph come solely from the projector.

## Reversibility

```bash
docker compose down -v      # removes containers + volumes
rm -rf /home/mmaudet/work/twaky
```

Nothing else touched on the host or the platform.

## Licenses

All runtime dependencies are permissive OSS:

- MIT: langchain-core/community, langgraph, litellm, langchain-litellm, langfuse, pydantic, typer
- Apache 2.0: aio-pika, structlog, apache/age image, seaweedfs image, clickhouse-server image
- LGPL: psycopg (client only)
- BSD: python-dotenv, redis image

Forbidden by policy (verified absent): `langgraph-api`, `langgraph.cli` (Elastic 2.0), Neo4j (GPLv3). `langsmith` is present as a transitive dep of `langchain-core` (MIT license, dormant without `LANGSMITH_API_KEY`); no data leaves the platform.
