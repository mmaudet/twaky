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

## Custom skills (sub-project 5)

Owner-authored Python skills, editable via the web UI, executed in an
isolated subprocess, hot-reloaded via LISTEN/NOTIFY.

### Concepts

- A **skill** is a Python module with a top-level `def run(**kwargs)` function.
  It is stored in Postgres (`skill` table) and can be bound to any subset
  of the 4 built-in agents (`atlas`, `chronos`, `plume`, `iris`).
- Each LLM tool call = one fresh `multiprocessing.Process`:
  - `RLIMIT_AS` — 256 MB virtual memory.
  - `RLIMIT_CPU` — 60 CPU-seconds.
  - `RLIMIT_NPROC` — 0 (no forking; Linux only).
  - Wall-clock timeout — 30 s.
- Skills whose name collides with a built-in tool (`finish_mission`,
  `delegate_to_*`) are dropped at bind time with a warning log — the
  built-in tools always win.

### Isolation caveats (spec §9.2)

The subprocess boundary is a **safety** boundary, not a **security** one:

- Skills inherit the daemon's env vars (`TWAKY_PG_PASSWORD`, provider
  API keys are readable via `os.environ`).
- Skills inherit the daemon's network stack — they can `httpx.get` any
  internal service.
- A malicious owner-authored skill can `os.kill(os.getppid(), SIGTERM)`
  and take down the daemon.

Trust model: the owner is the same person who has SSH access to the
host. No new attack surface vs. editing Python files on disk.

### First-time setup on an existing volume

The migration only runs on fresh volumes. For an existing `twaky-pg`:

```bash
docker exec -i twaky-pg bash /docker-entrypoint-initdb.d/007_init_skills.sh
```

Then browse to `https://twaky.${BASE_DOMAIN}/skills`.

### Authoring a skill

1. Browse to `/skills`, click "+ New skill".
2. Fill `name` (regex `^[a-z][a-z0-9_]{0,63}$`) and `description` (1-1000 chars).
3. Write the Python in the Monaco editor. Must define a top-level
   `def run(**kwargs)` (async is fine). Any Python from the daemon's image
   is importable (`httpx`, stdlib, etc.).
4. Optionally add a JSON Schema for `config_values` — values you don't
   want the LLM to see (API endpoints, thresholds).
5. Tick which agent(s) can call it.
6. Click **Test** — runs the skill in a subprocess with production limits,
   returns outcome + result.
7. Click **Save**. The next agent invocation on any bound agent picks it up.

### Removing a skill

Click Delete on the list page. Confirmation dialog warns that in-flight
missions using the skill will fail on the next call.

## Sentinels

Sentinels are background autonomous agents. Each sentinel monitors one
event source (JMAP, RabbitMQ) and evaluates a configurable condition on
every event; when the condition fires it dispatches an Atlas mission.
Sentinels are stored in Postgres (`sentinel` table) and hot-reloaded at
runtime via `LISTEN/NOTIFY`.

### First-time setup on an existing volume

The migration (`sql/008_init_sentinels.sh`) only runs on fresh volumes.
For an existing `twaky-pg`:

```bash
docker exec -i twaky-pg bash /docker-entrypoint-initdb.d/008_init_sentinels.sh
```

Then restart `twaky-sentinel` to pick up the new tables.

### Sentinels · Mail — Connect JMAP account

The mail sentinel authenticates against your JMAP server via an OIDC
authorization code flow managed by LemonLDAP-NG. Bearer tokens are refreshed
automatically before every JMAP call — no manual token capture needed.

**1. Register the OIDC client (operator, one-time)**

In LemonLDAP-NG manager (`https://auth.${BASE_DOMAIN}/manager`) create a new
OpenID Connect Relying Party. Full attribute table in
[`docs/superpowers/specs/2026-08-10-sp6b-jmap-oauth-design.md §3`](docs/superpowers/specs/2026-08-10-sp6b-jmap-oauth-design.md).
Key values: `client_id=twaky-mail-sentinel`, `refresh_token=1`,
`additional_audiences=james`, `redirect_uri=https://twaky.${BASE_DOMAIN}/oauth/jmap/callback`.

**2. Generate `TWAKY_SECRET_KEY`**

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Add the output to `.env` as `TWAKY_SECRET_KEY=...`.
**Losing this key = losing all encrypted credentials** — store it alongside your
other deployment secrets.

**3. Set remaining env vars**

In `.env` (see `.env.example` for defaults):

```
JMAP_OAUTH_CLIENT_ID=twaky-mail-sentinel
JMAP_OAUTH_CLIENT_SECRET=<secret from LemonLDAP manager>
JMAP_OAUTH_ISSUER=https://auth.${BASE_DOMAIN}
JMAP_OAUTH_SCOPE=openid profile email offline_access
JMAP_SESSION_URL=https://jmap.${BASE_DOMAIN}/jmap/session
```

**4. Connect via UI**

Navigate to `/sentinels/mail` → **Auth** tab → click **Connect JMAP account**.
Complete the OIDC redirect (LemonLDAP session reused, no login prompt if
already logged in). The tab returns to `?tab=auth&status=connected` and shows
**Connected as `<email>`**.

**5. Token lifecycle**

Access tokens refresh automatically before every JMAP call. If the
refresh_token is revoked (e.g. LemonLDAP session expired after 30 days), the
**Auth** tab shows `last_refresh_error`. Click **Reconnect** to repeat the
OIDC flow and obtain a fresh token pair.

### Enabling / disabling a sentinel

Sentinels ship disabled by default. Toggle one via the API (endpoint
lands in T25):

```bash
# Enable
curl -X PATCH https://twaky.${BASE_DOMAIN}/sentinels/<name> \
     -H "Cookie: twaky_session=$COOKIE" \
     -H "Content-Type: application/json" \
     -d '{"enabled": true}'

# Disable
curl -X PATCH https://twaky.${BASE_DOMAIN}/sentinels/<name> \
     -H "Cookie: twaky_session=$COOKIE" \
     -H "Content-Type: application/json" \
     -d '{"enabled": false}'
```

The `twaky-sentinel` container picks up the change via `LISTEN/NOTIFY`
without a restart.

### Runtime tuning

| Env var | Default | Purpose |
|---|---|---|
| `SENTINEL_TIMEOUT_S` | 60 | Per-event processing timeout |
| `SENTINEL_MAX_CONCURRENT_EVENTS` | 4 | Bounded concurrency |
| `SENTINEL_RUN_RETENTION_DAYS` | 30 | Prune old sentinel_run rows |

### Mail sentinel CLI operations

Two sub-command groups on `twaky mail-sentinel` for operators inspecting
state without touching the UI or writing SQL by hand.

**Rules** — `mail_sentinel_rule` table:

```bash
# List every rule ordered by priority (lower runs first)
uv run twaky mail-sentinel rules list
uv run twaky mail-sentinel rules list --enabled-only

# Flip enabled/disabled — non-destructive, reversible
uv run twaky mail-sentinel rules toggle github_notifications
```

**Spam decisions** — `mail_sentinel_spam_decision` table:

```bash
# 20 most recent decisions (all buckets)
uv run twaky mail-sentinel decisions list

# Filter by bucket
uv run twaky mail-sentinel decisions list --bucket spam --recent 50
uv run twaky mail-sentinel decisions list --bucket phishing-alert

# Per-bucket counts + restore rate over N days
uv run twaky mail-sentinel decisions stats --days 7
```

**Replay** — re-run the pipeline against historical INBOX mails:

```bash
# Dry-run: writes decisions to Postgres, skips JMAP side-effects
uv run twaky mail-sentinel replay --since 3d --limit 20 --dry-run
```

### Rules Propose/Apply (SP6d)

Rules mutations from the UI now go through a two-step flow: an
operator drafts the rule, clicks **Preview matches** to see which
of the last 200 historical decisions would have matched (and
which would have been pre-empted by an earlier-priority rule),
ticks "I have reviewed the matches", then clicks **Apply**.

The same protection is enforced backend-side:

```bash
curl -X POST https://twaky.${BASE_DOMAIN}/mail-sentinel/rules/propose \
     -H "Cookie: twaky_session=$COOKIE" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "test-rule",
       "priority": 50,
       "enabled": true,
       "conditions": [{"field": "from", "operator": "contains", "value": "@newsletter.example"}],
       "combinator": "all",
       "actions": ["archive", "label:newsletter"],
       "window": {"kind": "recent", "count": 200}
     }' | jq
```

Returns `matched_count`, `would_shadow_count`, `matched_examples`
(up to 10), and a `simulation_partial` flag when the rule uses
header or body predicates the simulation cannot fully evaluate.

### Recent Spam tab + Restore

The mail sentinel can optionally short-circuit spam BEFORE the full LLM
pipeline runs. Enable via /sentinels/mail#recent-spam:

1. Toggle "Spam filter" ON.
2. From now on, incoming inbox mails get classified into one of four
   buckets:
   - **spam**: labeled `__spam__`, `$junk` keyword set, and the message
     is atomically moved to the JMAP `junk` role mailbox (Indésirables)
     via `Email/set` with `mailboxIds` patched. On failure the keyword
     is still set so a downstream James filter can move it.
   - **newsletter**: labeled `newsletter` + `nonjunk` keyword set;
     stays in INBOX; you can create rules that match `label:newsletter`.
   - **phishing-alert**: labeled + `$junk` + moved to `junk` role
     mailbox + a mission is emitted for your review under /missions.
   - **none**: pass-through (unchanged pipeline).
3. Review decisions in the Recent Spam tab; click Restore on any row
   to clear the spam keywords (mail reappears clean in INBOX).

**Origin mailbox (SP6d)**: each new decision captures the JMAP
mailbox the mail arrived in (typically `inbox`, but could be
`newsletter` if a rule filed it) plus a small subset of envelope
headers used by the rules Propose simulation. The Recent Spam
tab shows the role as a subdued badge. Pre-SP6d decisions show
"—" for Origin — the column is populated forward-only. Run
`sql/013_add_spam_decision_provenance.sh` in the twaky-pg
container to enable capture (idempotent, non-blocking).

Retention: 30 days for active decisions, 90 days for restored (audit
trail). Owner can tune thresholds via PATCH /sentinels/mail with
`config_values: {spam_llm_confidence_threshold: 0.90, ...}`.

### Evals

Eight YAML fixtures cover the mail pipeline end-to-end using a
deterministic fake LLM (no network calls):

**SP6 pipeline fixtures** (`tests/evals/mail/`):

| Fixture | Email type | Expected outcome |
|---|---|---|
| `spam_archive.yaml` | Newsletter-style email | `archive` action applied |
| `invoice_label.yaml` | Invoice notification | `label:invoice` action applied |
| `meeting_request_draft.yaml` | Meeting request | Draft reply saved |

**SP6c spam-triage fixtures** (`tests/evals/mail/spam/`):

| Fixture | Scenario | Expected outcome |
|---|---|---|
| `phishing_hard_attachment_dkim_none.yaml` | No DKIM + attachment + return-path mismatch | `phishing-alert` bucket, mission emitted |
| `newsletter_list_unsub.yaml` | list-unsubscribe + list-unsubscribe-post headers | `newsletter` bucket, no LLM call |
| `promo_marketing_greylist.yaml` | rspamd greylist verdict | LLM called; bucket varies |
| `personal_reply_thread.yaml` | Thread with `nonjunk` keyword | `none` (pass-through) |
| `ham_edge_invoice.yaml` | Automated invoice with valid DKIM | `none` (FP protection) |

**Run offline (CI default):**

```bash
TWAKY_PG_HOST=172.27.0.33 uv run pytest tests/evals -v
```

**Run against a real LLM** (opt-in, deferred to SP6b when `EVAL_LIVE=1`
support lands):

```bash
EVAL_LIVE=1 TWAKY_PG_HOST=172.27.0.33 uv run pytest tests/evals -v
```

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
