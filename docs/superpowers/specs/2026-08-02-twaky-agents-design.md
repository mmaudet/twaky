# Twaky Agent Configuration — Design Spec

> **Sub-project 4 of 7** — Twaky Control Tower vision.
> Prior: sub-projects 1 (Foundations), 2 (Agents + Atlas), 3a (HTTP API), 3b (Frontend).
> Next: sub-project 5 (Skill / Connector Store).

## 1. Goal

Turn the four existing agents (**Atlas**, **Chronos**, **Plume**, **Iris**) from Python-hardcoded modules into Postgres-backed configurable entities. The owner edits `system_prompt`, `model`, and `temperature` via a web form; the daemon live-reloads the changed config on the next sub-agent invocation (see §4.4 for what this means for missions already in flight), without a restart.

This is the first half of the "Agent Studio" vision surfaced during sub-project 3b brainstorming. It removes the current friction — editing Python files and running `docker compose restart twaky-atlas` — that prevents the owner from tuning agent behavior in the flow of daily use.

## 2. Scope

### 2.1 In scope

- Three editable fields per agent: `system_prompt`, `model`, `temperature`.
- One new Postgres table `agent` (1 row per agent, no versioning).
- Daemon-side in-process config cache, invalidated via `LISTEN/NOTIFY` on channel `agent_config_changed`.
- REST API: `GET /api/agents`, `GET /api/agents/{id}`, `PATCH /api/agents/{id}`, `GET /api/agents/{id}/default_prompt`.
- Frontend: `/agents` list page + `/agents/[id]` edit page. One new nav-header link.
- Seed migration: the 4 rows populated at first startup from the current `_SYSTEM` constants + existing env-var models (both stay as fallbacks).

### 2.2 Explicitly out of scope

Deferred to sub-project 5 or later:

- Editing the tool list per agent; creating/deleting custom agents.
- Skill / connector marketplace.
- Config history, versioning, A/B testing, rollback UI.
- Editing atlas orchestrator-specific knobs (`atlas_max_steps`, `atlas_max_tokens`, `atlas_mission_timeout_s`) — stay env-vars.
- Per-mission config snapshotting (missions do not carry a frozen config).
- Prompt template variables, structured output schemas, agent-to-agent handoff configuration.
- Model validation via LiteLLM handshake (no dry-run before save).
- "Test this prompt" preview UI.
- Multi-user config isolation (Twaky is mono-user per instance).

### 2.3 Success criteria

The sub-project is done when:

1. The owner can browse to `https://twaky.${BASE_DOMAIN}/agents`, click Plume, change its `temperature` from `null` to `0.3`, click Save, then declare a mission and observe the new temperature reflected in Langfuse trace metadata — with **no daemon restart**.
2. A malformed edit (empty prompt, temperature `-1`, prompt over 8000 chars) is rejected by the API with a `422` and by the UI with a validation toast.
3. Restarting `twaky-atlas` never resets an edited config: the DB row survives; the daemon boots and reads it.
4. A mission running when the owner saves a new prompt: the mission's next sub-agent invocation picks up the new prompt (documented, accepted trade-off).
5. All prior gates green: pytest, ruff, mypy, npm typecheck, npm lint, npm build, drift check.

## 3. Storage

### 3.1 Schema

New file `sql/003_agent_config.sql`, applied by the existing `twaky-pg` init script mechanism (same slot as `002_langfuse.sql`):

```sql
CREATE TABLE agent (
    id            TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('orchestrator', 'specialist')),
    system_prompt TEXT NOT NULL CHECK (length(system_prompt) BETWEEN 1 AND 8000),
    model         TEXT,
    temperature   REAL CHECK (temperature IS NULL OR temperature BETWEEN 0.0 AND 2.0),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION notify_agent_changed() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('agent_config_changed', NEW.id);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER agent_config_notify
  AFTER UPDATE ON agent
  FOR EACH ROW EXECUTE FUNCTION notify_agent_changed();

CREATE OR REPLACE FUNCTION agent_bump_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER agent_touch_updated_at
  BEFORE UPDATE ON agent
  FOR EACH ROW EXECUTE FUNCTION agent_bump_updated_at();
```

### 3.2 Design notes

- **`id` is TEXT, not UUID.** The four agent identities (`atlas`, `chronos`, `plume`, `iris`) are code-load-bearing — `delegate_to_plume` is imported by name. TEXT PKs match Python module names, avoid a UUID join at every node call.
- **`model` and `temperature` are nullable.** `NULL` preserves the current fallback semantics: `model IS NULL` → daemon uses `settings.model`; `temperature IS NULL` → daemon omits the parameter from `ChatLiteLLM`, so LiteLLM's per-provider default applies. This means the migration seeds all four rows with `model=NULL, temperature=NULL` and the existing environment behavior is unchanged.
- **`role` is denormalized informational.** Lets the UI badge Atlas differently without hardcoding the list. Read-only from the API's perspective (never accepted in a PATCH body).
- **CHECK constraints in both DB and pydantic layer** (defense in depth). The DB catches direct-SQL edits; the API catches malformed request bodies with a friendlier 422.
- **Owner scoping absent from the table.** Mono-user per instance; the single row is the single owner's. The existing `require_owner` FastAPI dependency guards all mutations at the API boundary.
- **NOTIFY fires only on UPDATE**, not INSERT. INSERTs only happen at seed time; there's nothing to invalidate then (daemon boot loads everything cold).

## 4. Daemon reload path

### 4.1 The refactor

Today's shape (`src/twaky/agents/plume/agent.py`, mirrored in the other three):

```python
_SYSTEM = "You are Plume..."           # module-level constant
def _make_llm() -> BaseChatModel:
    return ChatLiteLLM(model=settings.plume_model or settings.model, ...)

def _agent_node(state: AgentState):
    llm = _make_llm().bind_tools(TOOLS)
    messages = state.get("messages", [])
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=_SYSTEM), *messages]
    return {"messages": [llm.invoke(messages)]}
```

New shape:

```python
from twaky.agents.registry import load_agent_config

# _SYSTEM constant removed.

def _make_llm(cfg: AgentConfig) -> BaseChatModel:
    kwargs: dict = {"model": cfg.model or settings.model, "api_base": settings.litellm_api_base}
    if cfg.temperature is not None:
        kwargs["temperature"] = cfg.temperature
    return ChatLiteLLM(**kwargs)

def _agent_node(state: AgentState):
    cfg = load_agent_config("plume")
    llm = _make_llm(cfg).bind_tools(TOOLS)
    messages = state.get("messages", [])
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=cfg.system_prompt), *messages]
    return {"messages": [llm.invoke(messages)]}
```

Key move: **config is loaded inside the node function, not at build time.** The compiled `StateGraph` object remains stable across the daemon's lifetime; only the string plugged into each `SystemMessage` and the kwargs to `ChatLiteLLM` vary. LangGraph is agnostic to this.

Applied identically in `atlas/agent.py`, `chronos/agent.py`, `iris/agent.py`. Atlas has one extra site: its `_atlas_node` composes the SystemMessage the same way.

### 4.2 The registry module

New file `src/twaky/agents/registry.py`:

```python
from dataclasses import dataclass
import threading

@dataclass(frozen=True)
class AgentConfig:
    id: str
    display_name: str
    role: str                 # 'orchestrator' | 'specialist'
    system_prompt: str
    model: str | None
    temperature: float | None
    updated_at: datetime

_cache: dict[str, AgentConfig] = {}
_lock = threading.Lock()

def load_agent_config(agent_id: str) -> AgentConfig:
    with _lock:
        cfg = _cache.get(agent_id)
        if cfg is None:
            cfg = _load_from_db(agent_id)  # SELECT ... WHERE id = ...
            _cache[agent_id] = cfg
        return cfg

def invalidate(agent_id: str) -> None:
    with _lock:
        _cache.pop(agent_id, None)

def _load_from_db(agent_id: str) -> AgentConfig: ...
```

Simple dict + `threading.Lock` (the daemon runs a single asyncio loop but LangGraph node functions execute in a thread pool; the lock is cheap insurance). Cache miss cost is one small indexed SELECT (~1ms on local Postgres). A row keyed by `agent_id` that doesn't exist raises `AgentConfigMissing` — daemon logs + falls back to the DEFAULT_PROMPTS constant so a broken DB never bricks mission execution.

### 4.3 The NOTIFY listener

New file `src/twaky/agents/config_listener.py`:

```python
import asyncio, logging
import psycopg
from twaky.agents import registry
from twaky.config import settings

log = logging.getLogger(__name__)

async def run(cancel_event: asyncio.Event) -> None:
    """Listen on agent_config_changed and invalidate the registry cache."""
    while not cancel_event.is_set():
        try:
            async with await psycopg.AsyncConnection.connect(
                settings.pg_dsn, autocommit=True
            ) as conn:
                await conn.execute("LISTEN agent_config_changed")
                log.info("agent config listener started")
                async for notify in conn.notifies():
                    if cancel_event.is_set():
                        break
                    log.info("agent_config_changed payload=%s", notify.payload)
                    registry.invalidate(notify.payload)
        except Exception:
            log.exception("agent config listener crashed; reconnecting in 5s")
            await asyncio.sleep(5)
```

Wired into `daemon.py` as an asyncio task started alongside the mission loop. Reuses the psycopg-async pattern already established in the sub-project 2 broker module.

### 4.4 In-flight mission semantics

If a mission is mid-execution when the owner saves a new Plume prompt, the mission's *next* Plume sub-agent invocation loads the fresh config. Missions in flight for other agents are unaffected. There is no locking, no per-mission snapshot, no attempt to freeze a config-for-mission binding.

**Accepted trade-off:** in return for zero schema complexity and zero snapshot table, mid-mission edits can (rarely) cause a config swap partway through. In practice missions terminate in <2 minutes and the owner is not racing edits against their own missions. Documented in the API spec and README.

## 5. API surface

Mirrors sub-project 3a's conventions: cookie-session auth via `require_owner`, uniform error envelope, endpoints mounted under `/api/`.

### 5.1 Endpoints

```
GET    /api/agents                        → 200 [AgentSummary]      (owner-only)
GET    /api/agents/{id}                   → 200 Agent | 404          (owner-only)
GET    /api/agents/{id}/default_prompt    → 200 { system_prompt }    (owner-only)
PATCH  /api/agents/{id}                   → 200 Agent | 404 | 422    (owner-only)
```

`/default_prompt` is served from `src/twaky/agents/defaults.py` (see §6), not from the DB, so the reset text is guaranteed to match the module the daemon actually uses.

### 5.2 OpenAPI schemas

Added to `docs/api/openapi.yaml`:

```yaml
Agent:
  type: object
  required: [id, display_name, role, system_prompt, effective_model, updated_at]
  properties:
    id:              { type: string, example: "plume" }
    display_name:    { type: string, example: "Plume" }
    role:            { type: string, enum: [orchestrator, specialist] }
    system_prompt:   { type: string, minLength: 1, maxLength: 8000 }
    model:           { type: string, nullable: true, description: "null → daemon uses TWAKY_MODEL" }
    temperature:     { type: number, format: float, minimum: 0.0, maximum: 2.0, nullable: true }
    effective_model: { type: string, description: "Read-only: `model` if set, else settings.model." }
    updated_at:      { type: string, format: date-time }

AgentSummary:
  # Same as Agent minus system_prompt (shorter payload for the list endpoint).
  type: object
  required: [id, display_name, role, effective_model, updated_at]
  properties: { ... }

AgentUpdate:
  type: object
  properties:
    system_prompt: { type: string, minLength: 1, maxLength: 8000 }
    model:         { type: string, nullable: true }
    temperature:   { type: number, minimum: 0.0, maximum: 2.0, nullable: true }
  # All fields optional — partial update. Empty body → 422.
```

### 5.3 Validation

Enforced in the pydantic `AgentUpdate` model on the API side:

- `system_prompt`: stripped; 1-8000 chars after strip. Empty → 422 `validation_failed`.
- `temperature`: 0.0-2.0 inclusive, or null. Out of range → 422.
- `model`: non-empty string when present, or null. No format check — LiteLLM accepts many forms.
- PATCH body with no fields set → 422 `validation_failed` with message "at least one field required".

Postgres CHECK constraints repeat these bounds as defense in depth (also protects against direct SQL edits, e.g., in a `psql` console).

### 5.4 Error envelope

Same shape as 3a: `{"error": {"code": "...", "message": "..."}}`.

New codes:
- `agent_not_found` — 404 when `id` is not in `('atlas', 'chronos', 'plume', 'iris')` or, more precisely, when the SELECT returns no row.
- `validation_failed` — 422 for any of the rules in §5.3. Message includes the offending field.

Existing codes (`unauthorized`, `forbidden`) apply unchanged.

### 5.5 Code layout

- `src/twaky/agents_config/repository.py` — thin CRUD (`list_all()`, `get(id)`, `update(id, patch)`). Note module name is `agents_config` (plural + underscore) to avoid confusion with the existing `src/twaky/agents/` daemon-side package.
- `src/twaky/agents_config/service.py` — business logic (validate before write, compute `effective_model`, load defaults).
- `src/twaky/api/routers/agents.py` — FastAPI router, mounted at `/api/agents` in `src/twaky/api/main.py`.
- `src/twaky/api/schemas/agents.py` — pydantic models `Agent`, `AgentSummary`, `AgentUpdate`.

Matches the `missions_*` split established in 3a.

## 6. Frontend UI

Two new routes inside the existing `frontend/` shell, one new nav-header link.

### 6.1 `/agents` — list page

- Path: `frontend/src/app/agents/page.tsx`.
- Client component; uses `useAgents()` TanStack hook (created new).
- Renders a shadcn `Table`:

  | Name | Role | Model | Temperature | Updated | |
  |------|------|-------|-------------|---------|---|
  | Atlas | `orchestrator` badge | `claude-sonnet-4-5-20250929` *(default)* | *(default)* | 2 min ago | `[Edit]` |
  | Chronos | `specialist` badge | `openai/gpt-4o` | `0.30` | 5 days ago | `[Edit]` |
  | ... |

  - "Model" cell shows `agent.effective_model`; italicized *(default)* suffix when `agent.model === null`.
  - "Temperature" cell shows `agent.temperature ?? "(default)"` — the string literal `"(default)"` italicized.
  - "Updated" uses the existing `RelativeTime` component from 3b.
  - "Role" uses a small shadcn `Badge` — variant `default` for orchestrator, `secondary` for specialist.
- Four rows, no pagination.
- Loading skeleton + error toast on fetch failure, matching the `/missions` page conventions.

### 6.2 `/agents/[id]` — edit page

- Path: `frontend/src/app/agents/[id]/page.tsx`.
- Client component; uses `useAgent(id)`, `useUpdateAgent(id)`, `useDefaultPrompt(id)` hooks.
- Header: `Edit {display_name}` + `role` badge + `updated_at` relative + a link back to `/agents`.
- Form fields (top to bottom):

  1. **System prompt**
     - shadcn `Textarea`, `rows={15}`, monospace font (`font-mono` Tailwind class), `resize-y`.
     - Character counter bottom-right: `{n} / 8000` — turns red when out of range.
     - Trims trailing whitespace before submit.

  2. **Model**
     - Hybrid input: Radix `Select` with:
       - One "Use default (`{settings.model}`)" option → maps to `null` on submit. Selected when `agent.model === null`. Label pulls the effective_model string so the user sees what "default" resolves to.
       - N options from `NEXT_PUBLIC_TWAKY_KNOWN_MODELS` (comma-separated env var baked at build time). Default value if the env is unset: `claude-sonnet-4-5-20250929,openai/gpt-4o,openai/gpt-4o-mini,openrouter/moonshotai/kimi-k2-0905,ollama/llama3`.
       - Trailing "Custom…" option → reveals a text `Input` below the select, focused. Any string is accepted; the user's typed value becomes the submit value.
     - If `agent.model` is set to a value not in the known list, the select is initialized to "Custom…" with the current value in the text input.

  3. **Temperature**
     - Radix `Slider` `min=0.0 max=2.0 step=0.05`, disabled when the checkbox below is checked.
     - Beside the slider: a monospaced numeric readout `0.30`.
     - Below: checkbox "Use LiteLLM default (varies by provider)" — checking it sets the submit value to `null` and greys the slider.

- Buttons (bottom-right):
  - `Cancel` — plain link back to `/agents` (no dialog; the browser's built-in unsaved-changes prompt on nav is unnecessary here because there are no side effects).
  - `Reset to defaults` — opens a shadcn `AlertDialog` "Reset Plume's prompt to the original? Model and temperature will also be reset to (default). This cannot be undone.". Confirming pulls from `useDefaultPrompt(id)`, sets model/temperature to `null` in local form state. Save is still required to persist.
  - `Save` — primary button. Disabled unless the form is dirty AND valid. On click: PATCH via `useUpdateAgent`. On success: sonner toast "Saved. Changes apply to the next mission.", then `router.push('/agents')`. On 422: toast with the error's `message` field.

### 6.3 Hooks

New file `frontend/src/hooks/use-agents.ts`:

```ts
export function useAgents() { ... }              // GET /agents
export function useAgent(id: string) { ... }     // GET /agents/{id}
export function useDefaultPrompt(id: string) { ... }  // GET /agents/{id}/default_prompt (lazy — refetch on demand)
export function useUpdateAgent(id: string) { ... }  // PATCH /agents/{id}, invalidates useAgent(id) + useAgents()
```

Mirrors the `use-missions.ts` shape from 3b. Same `openapi-fetch` client. Same global 401 handling via QueryCache.

### 6.4 Header link

`frontend/src/components/layout/header.tsx` (from 3b) gets one new nav link: **Agents** — positioned between **Missions** and **Stats**. Uses the same active-highlight logic already in place.

### 6.5 No SSE integration

Config saves don't need a push notification: the user is the one making the change; the mutation response has the fresh row. `<SSEProvider>` stays untouched. (A later multi-user variant might want an `agent_config_changed` SSE channel, but sub-project 4 is mono-user.)

## 7. Migration & seed

### 7.1 Initial seed

The four rows are inserted at first startup by `sql/003_agent_config.sql`, using dollar-quoted string literals:

```sql
INSERT INTO agent (id, display_name, role, system_prompt, model, temperature) VALUES
  ('atlas',   'Atlas',   'orchestrator', $ATLAS_PROMPT$   ...verbatim from src/twaky/agents/atlas/agent.py _SYSTEM...   $ATLAS_PROMPT$,   NULL, NULL),
  ('chronos', 'Chronos', 'specialist',   $CHRONOS_PROMPT$ ...verbatim from src/twaky/agents/chronos/agent.py _SYSTEM... $CHRONOS_PROMPT$, NULL, NULL),
  ('plume',   'Plume',   'specialist',   $PLUME_PROMPT$   ...verbatim from src/twaky/agents/plume/agent.py _SYSTEM...   $PLUME_PROMPT$,   NULL, NULL),
  ('iris',    'Iris',    'specialist',   $IRIS_PROMPT$    ...verbatim from src/twaky/agents/iris/agent.py _SYSTEM...    $IRIS_PROMPT$,    NULL, NULL)
ON CONFLICT (id) DO NOTHING;
```

The `ON CONFLICT DO NOTHING` clause makes the migration re-run-safe: repeated boots with the file mounted don't clobber user edits.

The prompt text is copy-pasted **verbatim** from each `_SYSTEM` constant at implementation time (T1 of the plan). Line breaks and quotation marks inside the prompt are handled by dollar-quoting, no escaping needed.

### 7.2 Keeping the seed prompts recoverable

New module `src/twaky/agents/defaults.py`:

```python
"""Original system prompts for the 4 built-in agents.
Source of truth for the /api/agents/{id}/default_prompt endpoint (used by
the frontend Reset-to-defaults button)."""

DEFAULT_PROMPTS: dict[str, str] = {
    "atlas":   "You are Atlas, the orchestrator of a personal assistant. ...",
    "chronos": "You are Chronos, the calendar specialist ...",
    "plume":   "You are Plume, the mail specialist ...",
    "iris":    "You are Iris, the research specialist ...",
}
```

The strings are identical to those in the SQL seed (T1 copies them from each `_SYSTEM` constant to both this module and the SQL file). A test asserts the module dict contains all four keys with non-empty values.

The `_SYSTEM` constants are then **removed** from each `agent.py` file — they're no longer imported anywhere (the node functions now pull from `load_agent_config(...)`).

### 7.3 No mission back-migration

Missions from before this sub-project already ran with whatever `_SYSTEM` was compiled in at their time. Missions are terminal artifacts (see 3a spec §5); they are not re-executed. No data migration is required for the `missions` table.

## 8. Security & validation

### 8.1 Auth

Identical to sub-project 3a:

- Every endpoint depends on `require_owner`, which enforces (a) the presence of a valid `twaky_session` cookie and (b) the session email matches `settings.twaky_owner_email`.
- 401 `unauthorized` if no session; 403 `forbidden` if the session belongs to a non-owner (impossible in mono-user, but the guard stays for API consistency).

### 8.2 Threat model

- **Prompt injection: out of scope.** The owner is trusted. They're the only writer. The prompt they type is the prompt the daemon uses.
- **Prompt-caused mission failure: expected.** A weird prompt can break agent behavior. That's the point of an editing UI — the owner learns by iterating. Missions log to Langfuse; failures surface in the existing dashboard.
- **Prompt length cap (8000 chars).** Prevents accidental paste-bomb (an owner pasting a whole document into the textarea, which would then be sent to the LLM on every mission and rack up token costs). 8000 chars covers realistic system-prompt lengths with generous margin.
- **Model string trust: LiteLLM boundary.** A malicious model string can't escape LiteLLM's HTTP client — it becomes an HTTP request to whatever provider. A bad model string causes mission failures (LiteLLM raises); it does not exfiltrate data. Frontend and backend both validate `non-empty` and nothing further (no allowlist — the owner might want a private local model).
- **No PII in the config table** beyond what the owner types into a prompt themselves. Prompts are backed up as part of the standard Postgres backup that sub-project 1 established.

### 8.3 Defense in depth

- Pydantic validators enforce bounds in the API layer.
- Postgres CHECK constraints enforce the same bounds at the DB layer (catches direct-SQL edits).
- Frontend validators enforce bounds in the form (better UX than a round-trip 422).

## 9. Testing

Matches sub-project 3a and 3b conventions.

### 9.1 Python — unit tests (~15)

- `tests/twaky/agents_config/test_repository.py` — CRUD: list all, get one, update partial, update non-existent → raises.
- `tests/twaky/agents_config/test_service.py` — validation: temperature out of range, prompt empty, prompt too long, model empty string; happy-path patch.
- `tests/twaky/agents_config/test_defaults.py` — asserts `DEFAULT_PROMPTS` has all 4 keys, non-empty, and matches the SQL seed strings (see below).
- `tests/twaky/sql/test_agent_config_migration.py` — parses `sql/003_agent_config.sql`, extracts each dollar-quoted prompt, asserts it equals `DEFAULT_PROMPTS[id]`. Catches drift between the SQL seed and the Python defaults module.
- `tests/twaky/agents/test_registry.py` — cold miss loads from DB, warm hit is cache-served, `invalidate()` clears one key, `_load_from_db` raises `AgentConfigMissing` on unknown id.
- `tests/twaky/agents/test_registry_notify_trigger.py` — writes a row via psycopg, UPDATEs it, asserts the trigger fires (verified by opening a second connection with `LISTEN agent_config_changed` and receiving a notify within 500ms).

### 9.2 Python — API tests (~8)

- `tests/twaky/api/test_agents_router.py` — full matrix:
  - `GET /api/agents` — 401 without session, 200 with owner session returns 4 summaries.
  - `GET /api/agents/{id}` — 200 for each of the 4 known ids, 404 for `zeus`.
  - `GET /api/agents/{id}/default_prompt` — returns the DEFAULT_PROMPTS entry.
  - `PATCH /api/agents/plume` — happy path with `{"temperature": 0.3}` returns 200 with the updated row, DB reflects change, `updated_at` bumped.
  - `PATCH /api/agents/plume` with `{"temperature": 3.0}` → 422 `validation_failed`.
  - `PATCH /api/agents/plume` with `{"system_prompt": ""}` → 422.
  - `PATCH /api/agents/plume` with `{}` → 422 "at least one field required".
  - `PATCH /api/agents/zeus` → 404 `agent_not_found`.

Reuses the `test_client_with_session` fixture from 3a; the tests run against an ephemeral Postgres started by the shared `conftest.py`.

### 9.3 Python — daemon integration test (1)

- `tests/twaky/agents/test_config_listener_integration.py` — spins up the config listener as an asyncio task against a real Postgres. Writes a row, UPDATEs it, asserts the cache entry is cleared within 1s. Uses `pytest-asyncio`.

### 9.4 Frontend — unit tests (~10)

Under `frontend/tests/unit/`:

- `use-agents.test.tsx` — MSW mocks the 4 endpoints; hooks return expected shapes; the mutation invalidates the list.
- `agent-model-input.test.tsx` — hybrid input toggles: selecting "Custom…" reveals the text field; selecting a preset hides it; picking "Use default" submits `null`.
- `agent-temperature-input.test.tsx` — slider + checkbox toggle: checking "Use default" greys the slider and submits `null`.
- `agent-form-validation.test.tsx` — Save is disabled when the prompt is empty or over 8000 chars; disabled when nothing is dirty; enabled after a valid edit.
- `agent-list-defaults-display.test.tsx` — `null` model/temperature render as *(default)*.
- `reset-to-defaults-dialog.test.tsx` — clicking Reset opens the alert; confirming pulls the default_prompt; form becomes dirty until Save.

### 9.5 Frontend — E2E (~2)

Under `frontend/tests/e2e/`:

- `agents-edit.spec.ts` — signed-in user navigates to `/agents`, clicks Plume, changes temperature to 0.3, saves, is redirected to `/agents`, and sees the new value in the table.
- `agents-validation.spec.ts` — signed-in user opens `/agents/plume`, clears the prompt textarea, verifies Save is disabled and the character counter shows red.

Both use the `signedInPage` fixture from 3b, unchanged.

### 9.6 Whole-repo gates

Unchanged from prior sub-projects — every task's implementer runs the local gate:

- `uv run pytest -q`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run mypy src/`
- `cd frontend && npm run typecheck && npm run lint && npm run test:unit && npm run build`
- `cd frontend && make api-types && git diff --exit-code src/lib/api-types.d.ts`

The `frontend-e2e` CI job is opt-in via the `run-e2e` label (established in 3b). New `agents-*.spec.ts` files run inside it automatically.

## 10. Task decomposition preview

Final numbering is `writing-plans`' job. Rough breakdown expected:

- **T1** — `sql/003_agent_config.sql` + `src/twaky/agents/defaults.py`. Copy the 4 `_SYSTEM` strings verbatim into both. Migration re-run test.
- **T2** — `agents_config/repository.py` + unit tests.
- **T3** — `agents_config/service.py` (validation) + unit tests.
- **T4** — `agents/registry.py` (cache + `load_agent_config` + `invalidate`) + unit tests.
- **T5** — `agents/config_listener.py` + trigger integration test.
- **T6** — Wire listener into `daemon.py` startup, graceful shutdown.
- **T7** — Refactor the 4 agent modules: remove `_SYSTEM`, pull from registry per node, pass `temperature` to `ChatLiteLLM`. Existing agent tests updated.
- **T8** — `api/routers/agents.py` — `GET /agents`, `GET /agents/{id}`, `GET /agents/{id}/default_prompt`.
- **T9** — `api/routers/agents.py` — `PATCH /agents/{id}` + validation.
- **T10** — Update `docs/api/openapi.yaml`, regenerate frontend types via `make api-types`.
- **T11** — Frontend hooks: `useAgents`, `useAgent`, `useDefaultPrompt`, `useUpdateAgent` + unit tests.
- **T12** — Frontend `/agents` list page + Nav link.
- **T13** — Frontend `/agents/[id]` edit page (form, hybrid model input, temperature slider + checkbox, character counter).
- **T14** — Frontend `Reset to defaults` button + confirmation alert dialog.
- **T15** — Playwright E2E: happy-path + validation-error scenarios.
- **T16** — README section (`## Agent configuration`) + spec follow-ups + full-repo gate sweep.

**~16 tasks**, comfortably under the 20-25 target. Same shape as 3a (18 tasks). No decomposition into 4a/4b needed.

## 11. Global constraints (for the plan)

Copy verbatim into the plan's Global Constraints block:

- **Session cookie name**: `twaky_session` (matches 3a `SESSION_COOKIE_NAME`; unchanged).
- **Endpoint mount**: `/api/agents/*` — never `/agents/*` at root, never versioned prefix.
- **Table name**: `agent` (singular, unquoted).
- **NOTIFY channel name**: `agent_config_changed` (verbatim).
- **Agent IDs (source-of-truth)**: `atlas`, `chronos`, `plume`, `iris` — exactly these 4, lowercase, no plural.
- **Model fallback rule**: `cfg.model or settings.model` — never invert; a set value always wins over the env var.
- **Temperature fallback rule**: `if cfg.temperature is not None: kwargs['temperature'] = cfg.temperature` — never pass `temperature=None` to `ChatLiteLLM` (LiteLLM's default handling for the sentinel varies by provider; better to omit).
- **Prompt bounds**: 1-8000 chars, enforced in DB CHECK, pydantic, and frontend form — all three layers.
- **Temperature bounds**: 0.0-2.0 inclusive, enforced in DB CHECK, pydantic, and frontend form.
- **New Python package name**: `src/twaky/agents_config/` (with underscore) — NOT `src/twaky/agentsconfig/` and NOT under `src/twaky/agents/` (that path stays reserved for the daemon-side sub-agents).
- **Error envelope**: same shape as 3a — `{"error": {"code": "...", "message": "..."}}`.
- **Frontend nav link**: label `Agents`, positioned between `Missions` and `Stats` in the header.
- **`Reset to defaults`** pulls from `/api/agents/{id}/default_prompt` (server-authoritative), not from a hardcoded frontend copy.
- **No versioning, no history table, no snapshotting** — YAGNI. If a later sub-project needs it, it adds those tables then.

---

**End of design spec.** Ready for implementation planning via `superpowers:writing-plans`.
