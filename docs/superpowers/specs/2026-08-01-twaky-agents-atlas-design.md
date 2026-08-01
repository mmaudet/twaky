# Twaky Agents + Atlas — Design (Sub-project 2 of 5)

**Status:** draft, awaiting user review
**Date:** 2026-08-01
**Owner:** mmaudet
**Related:** builds on Foundations (sub-project 1, merged as `fe838f6` on `main`). The next sub-projects (3 = API + Frontend Control Tower, 4 = Federation, 5 = Write-side) will each get their own design + plan cycle.

---

## 1. Purpose

Bring the Control Tower vision to life with three specialist agents driven by an orchestrator daemon:

1. A **`twaky-atlas`** long-running daemon that watches `mission` state, drives each declared mission through a LangGraph StateGraph, and writes state back via the Foundations engine.
2. Three **specialist sub-agents** — Chronos (calendar), Plume (mail), Iris (research) — each a StateGraph with its own LLM and a narrow toolset. Atlas delegates to them via `delegate_to_<name>(query)` tools (Supervisor pattern).
3. Two **end-to-end demo missions** that exercise the whole pipeline: "Draft a reply to email X" (ends `awaiting_user`) and "Résume ma journée de demain" (ends `done` directly).
4. A CLI to declare, list, inspect, resume, and cancel missions.

Everything sits on top of Foundations: the mission table, engine transitions, checkpointer, recovery, and observability wiring already exist.

## 2. Non-goals

- No HTTP API or frontend — sub-project 3.
- No federation, no `twaky:message:*` wiring — sub-project 4.
- No write-side actions (no CalDAV writes, no mail sending) — sub-project 5.
- No RAG, no vector store — deferred until explicitly needed.
- No JMAP body indexing into the graph — Plume fetches on-the-fly.
- No auto-planning UI or drag-and-drop — CLI only in this sub-project.

## 3. Architecture

```
    ┌──────────────────────────────────────────────────────────────────────┐
    │                    twaky (mono-user instance)                       │
    │                                                                      │
    │  RabbitMQ ──▶ ingest ──▶ event_log ──▶ projector ──▶ AGE graph      │
    │  (Foundations, unchanged)                                            │
    │                                                                      │
    │  ┌──────────────────────────────────────────────────────────┐       │
    │  │ NEW: twaky-atlas daemon container                        │       │
    │  │   loop: claim mission (SELECT FOR UPDATE SKIP LOCKED),   │       │
    │  │   asyncio.Semaphore(TWAKY_ATLAS_MAX_CONCURRENT_MISSIONS) │       │
    │  │   PostgreSQL LISTEN mission_declared for immediate wake  │       │
    │  │                                                          │       │
    │  │   Per-mission task:                                      │       │
    │  │     Atlas StateGraph                                     │       │
    │  │       ├─ delegate_to_chronos ──▶ Chronos StateGraph      │       │
    │  │       ├─ delegate_to_plume   ──▶ Plume StateGraph        │       │
    │  │       └─ delegate_to_iris    ──▶ Iris StateGraph         │       │
    │  │                                                          │       │
    │  │   State written via twaky.missions.engine (Foundations)  │       │
    │  │   Checkpoint written via PostgresSaver (Foundations)     │       │
    │  └──────────────────────────────────────────────────────────┘       │
    │                                                                      │
    │  Sub-agents call:                                                   │
    │    • JMAP (Plume) — OIDC token exchange via LemonLDAP-NG            │
    │    • SearXNG on twake-network (Iris)                                │
    │    • AGE graph (all three, via existing psycopg pool + AGE cypher)  │
    └──────────────────────────────────────────────────────────────────────┘
```

New Python packages:

- `src/twaky/agents/` — new package with `atlas/`, `chronos/`, `plume/`, `iris/` subdirs.
- `src/twaky/tools/` — new package with `graph_qa.py` (refactored from `agent.py`) and any other shared @tool helpers.
- `src/twaky/auth/` — new package with `jmap.py` (OIDC token exchange helper).
- `src/twaky/daemon/` — new package with `atlas_daemon.py` (main loop, signal handling, healthcheck).

New Docker service:

- `twaky-atlas` in `docker-compose.yml`, uses the shared `twaky:local` image, `command: ["twaky", "atlas", "run"]`, `depends_on: twaky-pg (healthy)`, `restart: unless-stopped`.

Refactors:

- `src/twaky/agent.py` → `src/twaky/tools/graph_qa.py`. The CLI subcommand `twaky ask` is removed; NL graph questions now go through `twaky mission declare` when part of a mission, or `twaky tools graph-qa "..."` for standalone debugging.

## 4. Atlas StateGraph (Supervisor)

### 4.1 State shape

```python
class AtlasState(TypedDict):
    mission_id: UUID
    owner_email: str
    intent_text: str
    messages: list[BaseMessage]         # chat history — LangGraph MessageState idiom
    artifacts: list[dict]               # accumulated artifacts, mirrored to mission.artifacts
    step_count: int                     # how many agent delegations so far (safety limit)
    pending_user_input: dict | None     # cooperative signal from a sub-agent
```

### 4.2 Graph

Two node types + conditional edges:

- `atlas_router` — a LangGraph `ToolNode`-wrapped LLM call. Bound tools:
  - `delegate_to_chronos(query: str) -> str`
  - `delegate_to_plume(query: str) -> str`
  - `delegate_to_iris(query: str) -> str`
  - `finish_mission(final_answer: str, outcome: Literal["done","failed"]) -> str` — special tool that terminates the graph
- Three delegation nodes — one per sub-agent. Each invokes the sub-agent's StateGraph with the query, appends the result to state.messages + state.artifacts, and returns to `atlas_router`.

Edges:

- START → `atlas_router`
- `atlas_router` → one of {delegate_to_chronos, delegate_to_plume, delegate_to_iris, finish_mission} (LLM-decided via tool call)
- Each delegate node → `atlas_router` (loop back)
- `finish_mission` → END

### 4.3 Safety limits

- `step_count > 12` (env var `TWAKY_ATLAS_MAX_STEPS`) → auto-finish with outcome=failed, reason=`step_limit_exceeded`.
- Total wall-clock per mission > 5 min (`TWAKY_ATLAS_MISSION_TIMEOUT`) → same.
- Total tokens > 100k (`TWAKY_ATLAS_MAX_TOKENS`) → same.

### 4.4 Cooperative user-input seam

When a sub-agent produces a result that needs user validation (typically Plume with a draft), it returns:

```json
{"answer": "...", "pending_user_input": {"kind": "approve_draft", "artifact": {"draft": "…"}}}
```

`atlas_router` sees the `pending_user_input` key on the returned message. Instead of routing to the next tool, it calls `engine.request_user_input(mission_id, reason=kind, artifact=artifact)` and terminates the graph via `interrupt` — the daemon takes over: mission → `awaiting_user`, task released from the semaphore.

On `twaky mission resume`, the daemon:

1. Reads the user response from the CLI.
2. Calls `engine.resume(mid, user_response)` (transitions awaiting_user → running, appends `user_response` artifact — Foundations already does this).
3. Reloads the checkpoint via `PostgresSaver.get_tuple({"configurable": {"thread_id": str(mid)}})`.
4. Continues the LangGraph with `Command(resume=user_response)` — `atlas_router` picks up state, sees the response, decides next step (usually `finish_mission("done")`).

## 5. Sub-agents

Each sub-agent is a small `StateGraph` (`AgentState = {messages, tools_called}`) with its own LLM + toolset. All follow the same pattern:

```python
def build_<name>_agent() -> CompiledStateGraph:
    llm = ChatLiteLLM(model=settings.<name>_model or settings.model)
    tools = [tool_a, tool_b, tool_c]
    llm_with_tools = llm.bind_tools(tools)
    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node(llm_with_tools))
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=None)   # sub-agents don't checkpoint independently — Atlas does
```

The `delegate_to_<name>` tool at the Atlas level compiles the sub-agent and invokes it with the query.

### 5.1 Chronos — calendar

- Model: `CHRONOS_MODEL` env, fallback `MODEL`
- Tools:
  - `list_events(from_iso: str, to_iso: str) -> list[dict]` — Cypher on AGE graph
  - `get_event(uid: str) -> dict | None` — Cypher lookup
  - `find_conflicts(person_email: str, from_iso: str, to_iso: str) -> list[dict]` — Cypher with WHERE overlap
  - `next_free_slot(participant_emails: list[str], duration_min: int, window_from_iso: str, window_to_iso: str) -> dict | None` — computed
- Data source: the AGE graph populated by Foundations. No direct Sabre/CalDAV access.

### 5.2 Plume — mail

- Model: `PLUME_MODEL` env, fallback `MODEL`
- Tools:
  - `list_recent_emails(limit: int = 20) -> list[dict]` — JMAP Email/query, sorted by receivedAt desc
  - `read_email(message_id: str) -> dict` — JMAP Email/get with body properties
  - `search_emails(query: str, limit: int = 10) -> list[dict]` — JMAP filter
  - `draft_reply(message_id: str, body: str, tone: Literal["formal","casual"]) -> dict` — LLM-generated draft, NO SEND, returns `{"draft": "...", "to": "...", "subject": "Re: …"}`
- JMAP client via `src/twaky/auth/jmap.py` — see §6.

Plume's LLM decides which tool to call (list → read → draft → return with pending_user_input).

### 5.3 Iris — research

- Model: `IRIS_MODEL` env, fallback `MODEL`
- Tools:
  - `web_search(query: str, limit: int = 5) -> list[dict]` — HTTP GET to `http://searxng:8080/search?q=<query>&format=json&categories=general`
  - `read_url(url: str) -> str` — HTTP GET + `trafilatura.extract` (new dep, MIT) for main text
  - `graph_qa(question: str) -> str` — the refactored `agent.py` @tool
- Iris's LLM decides: search first, then read the most promising URL, then cross-reference with the graph if the topic touches a Person/Org.

## 6. JMAP auth (Plume)

**Pattern**: OIDC token exchange, same shape as Twake Visio ↔ Calendar service (existing on the platform).

```
1. Plume boots.
2. From client_id + client_secret in .env (new LemonLDAP-NG client `twaky-plume`),
   Plume gets a client_credentials token.
3. To act on the owner's mailbox, Plume exchanges (RFC 8693) that token for
   an impersonated token: subject = TWAKY_OWNER_EMAIL, actor = twaky-plume.
4. JMAP requests use Authorization: Bearer <impersonated token>.
5. Token cached in-memory with TTL, refresh 60s before expiry.
```

**New LemonLDAP-NG client** (added to `twake_auth/config/lmConf-1.json.ldap.template`, same mechanism as Langfuse and Metabase):

- ClientID: `twaky-plume`
- ClientSecret: generated at setup
- Grants: client_credentials, token exchange
- Scopes: `openid`, `email`, `impersonation`

**New env vars** in `.env`:

- `JMAP_ENDPOINT` (default `http://tmail-backend:8080/jmap`)
- `PLUME_OIDC_CLIENT_ID=twaky-plume`
- `PLUME_OIDC_CLIENT_SECRET=<generated>`
- `PLUME_OIDC_ISSUER=https://auth.${BASE_DOMAIN}/`

**Fallback for MVP**: if token exchange is not available on the platform, fall back to basic auth with a service account (env `JMAP_USERNAME` / `JMAP_PASSWORD`). Sub-project 2 implements both; the runtime picks whichever env has values.

## 7. Daemon (`twaky-atlas`)

### 7.1 Compose service

```yaml
twaky-atlas:
  <<: *python-common
  container_name: twaky-atlas
  depends_on:
    twaky-pg: { condition: service_healthy }
  command: ["twaky", "atlas", "run"]
  healthcheck:
    test: ["CMD-SHELL", "test -f /tmp/atlas.heartbeat && \
      test $$(($$(date +%s) - $$(date +%s -r /tmp/atlas.heartbeat))) -lt 30"]
    interval: 30s
    timeout: 5s
    retries: 3
    start_period: 30s
```

### 7.2 Boot sequence

```python
def atlas_run():
    setup_checkpointer_tables()             # ensures Foundations checkpoint tables exist
    _issue_recovery_report()                # calls recovery.resume_missions_after_restart
    asyncio.run(_main_loop())
```

`_issue_recovery_report` logs each mission's action; recovery already transitions lost ones to FAILED.

### 7.3 Main loop

```python
async def _main_loop():
    sem = asyncio.Semaphore(settings.atlas_max_concurrent)
    tasks: set[asyncio.Task] = set()
    stop = _install_signal_handlers()
    async with _pg_listen("mission_declared") as notify_queue:
        while not stop.is_set():
            mid = await _claim_next()          # SELECT FOR UPDATE SKIP LOCKED
            if mid is None:
                await _wait_notify_or_timeout(notify_queue, 5.0)
                continue
            task = asyncio.create_task(_bounded_run(sem, mid))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            _heartbeat()
    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=25)
```

### 7.4 Per-mission task

```python
async def _bounded_run(sem, mid):
    async with sem:
        try:
            await _run_atlas_state_graph(mid)
        except Exception as exc:
            engine.finish(mid, outcome="failed", artifacts=[],
                          reason=f"atlas_crashed: {type(exc).__name__}")
            log.exception("mission crashed", mission_id=str(mid))
```

### 7.5 NOTIFY

`engine.declare()` gains a small addition: `NOTIFY mission_declared, '<mid>'` after the INSERT commits. This is a one-line change to Foundations, wrapped in a helper.

## 8. CLI

```
twaky mission declare "<intent>" [--wait] [--due <ISO>]
twaky mission list [--state <s>] [--limit N]
twaky mission show <mid>            # state + artifacts + trace URL
twaky mission resume <mid> --input '<json>'
twaky mission cancel <mid> [--reason <r>]
twaky atlas run                     # daemon entry point
twaky atlas health                  # oneshot health probe (matches Docker healthcheck)
twaky tools graph-qa "<question>"   # standalone graph-QA (replaces old `twaky ask`)
```

## 9. Demo missions

### 9.1 Mission A — "Draft a reply to email X"

Ends in `awaiting_user`. See §4.4 flow for the interaction.

**Fixture**: `scripts/seed-demo.sh` publishes 3 synthetic emails from Bob and creates matching `Email` nodes in the graph. Also seeds Bob as a Person with `works_at` an Organization "Acme Corp".

**Success criteria**:
- Mission goes through `declared → planning → running → awaiting_user` within 30 s.
- Artifact contains `{"kind": "approve_draft", "artifact": {"draft": "...", "to": "bob@…", "subject": "Re: …"}}`.
- On `twaky mission resume <mid> --input '{"approved": true}'`, mission reaches `done` within 5 s.
- Langfuse session shows: Atlas router (3-4 calls) + Plume (2-3 calls) + optionally Iris (1 call) + engine transitions.

### 9.2 Mission B — "Résume ma journée de demain"

Ends in `done` directly.

**Fixture**: `scripts/seed-demo.sh` creates 4 calendar events for tomorrow with mixed internal + external attendees.

**Success criteria**:
- Mission goes through `declared → planning → running → done` within 30 s.
- Artifact contains `{"summary": "..."}` mentioning at least 3 of the 4 events.
- Langfuse session shows: Atlas router (2-3 calls) + Chronos (2 calls) + Iris (0-2 calls).

## 10. Error handling

| Case | Behavior |
|---|---|
| Sub-agent raises inside its StateGraph | Caught by delegate node, message returned to Atlas as `{"error": str(e)}`. Atlas retries or delegates elsewhere; two retries total then `finish(failed)`. |
| JMAP timeout / auth failure | `jmap_client` raises; sub-agent bubbles up. Same handling. |
| SearXNG down | Iris tool returns `{"error": "search unavailable"}`. Atlas may finish with a partial answer. |
| Atlas LLM API error | Log + `engine.finish(failed, reason="atlas_router_error")`. No retry to bound cost. |
| Daemon crashes mid-mission | Foundations recovery covers it: RUNNING missions with checkpoint → resumed on next boot; without checkpoint → auto-failed. |
| User cancels awaiting_user mission | `engine.cancel` transitions to `cancelled`. If Atlas ever reloads its checkpoint, it sees the terminal state and exits. |
| Step limit / timeout / token cap hit | `engine.finish(failed, reason="step_limit_exceeded")` etc. All limits env-configurable. |

## 11. Testing

### 11.1 Unit

- `tests/agents/test_atlas_router.py` — scripted LLM returns a tool call sequence, assert delegation order.
- `tests/agents/test_chronos_tools.py` — mocked psycopg, assert Cypher shapes.
- `tests/agents/test_plume_tools.py` — mocked JMAP HTTP client, assert JMAP payloads.
- `tests/agents/test_iris_tools.py` — mocked SearXNG HTTP, assert URLs / query shapes.
- `tests/agents/test_pending_user_input_seam.py` — sub-agent returns the flag, assert `atlas_router` calls engine.request_user_input.
- `tests/daemon/test_main_loop.py` — asyncio + mocked engine + mocked LangGraph, assert claim/gather/shutdown behavior.
- `tests/auth/test_jmap_oidc.py` — mocked LemonLDAP, assert token exchange payloads + cache TTL.

### 11.2 Integration (self-skip when infra absent)

- `tests/integration/test_atlas_mission_a.py` — full Mission A with a stubbed LLM (deterministic responses), real Postgres+AGE, fake JMAP HTTP server, fake SearXNG. Assert mission ends `awaiting_user` with the expected artifact.
- `tests/integration/test_atlas_mission_b.py` — full Mission B, similar setup, assert `done` + summary artifact.
- `tests/integration/test_daemon_recovery.py` — start daemon, declare a mission, SIGKILL the daemon mid-run, restart, assert recovery.

### 11.3 E2E scenarios (bash + live stack)

- `scripts/scenarios-agents.sh`:
  1. `seed-demo.sh` — inbox + calendar fixtures.
  2. Declare Mission B, wait `done`, assert summary artifact non-empty.
  3. Declare Mission A, wait `awaiting_user`, `resume` with fake approval, wait `done`.
  4. Assert Langfuse session groups traces correctly (query API).
  5. Assert total cost < 0.05 USD (query ClickHouse via curl).
  6. Cleanup.

- Manual-only in CI (label `run-e2e`) — costs real LLM tokens.

## 12. Rollout

Additive: new package + new daemon. The mission table + engine + checkpointer are already in place (Foundations).

- New LemonLDAP-NG client `twaky-plume` — add to `twake_auth/config/lmConf-1.json.ldap.template` in the deploy repo (same as Langfuse and Metabase).
- New env vars — documented in `.env.example`.
- New Docker image dep: `trafilatura>=1.12` (MIT), `httpx>=0.28` (BSD), `authlib>=1.4` (BSD) — added via `uv add`.

No data migration needed: mission table already exists.

## 13. Open questions to close before implementation

- **JMAP token-exchange precedent** — locate the actual Twake Visio ↔ Calendar code that does this exchange (probably in `meet_app/` or `calendar_app/` of the deploy repo). Plume's `auth/jmap.py` should mirror it exactly, including endpoint URLs and grant_type strings.
- **Fixture data** — how large should the synthetic inbox / calendar be? Enough to exercise pagination? Enough to hit rate limits? Initial answer: 5 emails, 4 events, tuning during T-tasks.
- **CLI subcommand namespace** — `twaky mission` and `twaky atlas` are new namespaces; verify no collision with existing `twaky ask`, `twaky ingest`, etc.

## 14. Sub-projects that will build on this

- **Sub-project 3 — API + Frontend**: exposes `mission` CRUD and streams a `WS /events` channel that mirrors what the daemon logs. The frontend Control Tower shows the same missions Atlas is driving.
- **Sub-project 4 — Federation**: `delegate_to_<other_owner>` tool becomes a top-level Atlas tool. The daemon consumes `twaky:message:*` from other instances.
- **Sub-project 5 — Write-side**: Plume gains `send_reply` (JMAP write via impersonated token), Chronos gains `create_event` (CalDAV write). The `awaiting_user` → `resume` flow already carries the approval semantics.
