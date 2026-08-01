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
                          ▼
                    projector ──▶ Cypher MERGE ──▶ graph
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

## Quickstart

```bash
cp .env.example .env
# Fill secrets (see the comment at the top of .env.example for openssl commands)

docker compose up -d twaky-pg              # Postgres+AGE, waits for healthy
docker compose up -d twaky-ingest twaky-projector

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
