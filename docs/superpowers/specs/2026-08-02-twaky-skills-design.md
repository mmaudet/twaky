# Twaky Custom Skills — Design Spec

> **Sub-project 5 of 7** — Twaky Control Tower vision.
> Prior: sub-projects 1 (Foundations), 2 (Agents + Atlas), 3a (HTTP API), 3b (Frontend), 4 (Agent Configuration).
> Next: SP5b (Custom Agents), SP6 (Federation), SP7 (Write-side).

## 1. Goal

Let the owner author custom Python skills via the web UI, each running in an isolated subprocess with resource limits, live-reloaded via `LISTEN/NOTIFY`, and bindable to any subset of the 4 built-in agents (Atlas, Chronos, Plume, Iris).

This is the first half of the "Agent Studio" store vision surfaced during SP3b brainstorming. It removes the current friction — editing `src/twaky/agents/<name>/tools.py` and rebuilding the daemon container — that prevents the owner from adding new tools in the flow of daily use.

## 2. Scope

### 2.1 In scope

- One new Postgres table `skill` with 6 editable fields per row: `name`, `description`, `python_source`, `config_schema` (JSON), `config_values` (JSON), `bound_agents` (JSON array), plus `enabled` boolean.
- Subprocess-based skill executor (`multiprocessing.Process` + `resource.setrlimit(RLIMIT_AS, RLIMIT_CPU, RLIMIT_NPROC)` + wall-clock timeout via `Process.join(timeout)` → `.terminate()` → `.kill()`).
- Registry cache (indexed by `agent_id`) + `LISTEN skill_changed` NOTIFY trigger, mirroring SP4's `agent_config_changed` pattern.
- LangChain `StructuredTool` adapter that wraps a `Skill` row into a callable the existing agent nodes can `bind_tools()`.
- Refactor of the 4 agent modules to append skills to their hardcoded `TOOLS` list at node-invocation time.
- REST API: `GET`, `POST`, `PATCH`, `DELETE` under `/skills/*` plus `POST /skills/{id}/test` for dry-run.
- Frontend: `/skills` list page + `/skills/[id]` edit page with Monaco Python editor, JSON schema/values editors, agent-binding checkboxes, "Test" dialog. One new nav-header link.

### 2.2 Explicitly out of scope

Deferred to SP5b or later:

- Custom agents (create/delete new agents beyond the 4 built-in). SP5b.
- Marketplace / remote catalog / "browse & install". Tied to federation (SP6).
- Config-values encryption / secrets vault. `config_values` is plain JSON in the DB row; owner is trusted (mono-user).
- Version history / rollback for skills. YAGNI.
- WebAssembly, gVisor, or kernel-level isolation. Subprocess isolation is the isolation.
- Model Context Protocol (MCP) integration. Would be a different sub-project entirely if the owner wanted to consume MCP servers instead of Python code.
- Skill authoring assistance (LLM-suggested code, template picker). MVP editor is a blank Monaco pane.
- Skill dependencies / requirements.txt per skill. Skills use whatever the daemon's Python image already ships.
- Skill result caching / memoization. Every LLM tool call = one fresh subprocess.

### 2.3 Success criteria

The sub-project is done when:

1. The owner can browse to `https://twaky.${BASE_DOMAIN}/skills`, click "+ New skill", paste `def run(**kwargs) -> str: return f"echo: {kwargs}"`, name it `echo`, bind it to Atlas, click Save, then declare a mission "call the echo tool with foo=bar" and see the returned string in the mission's artifacts — with **no daemon restart**.
2. A malformed edit (invalid Python syntax, missing `run` function, name violating regex, temperature-style out-of-range config) is rejected by the API with a 422 and by the UI with a validation toast.
3. A malicious skill (infinite loop, OOM, crash) triggers the executor's kill path and returns a human-readable error to the LLM — the daemon stays alive.
4. Restarting `twaky-atlas` never resets an edited skill: the DB row survives; the daemon boots, invalidates its cache, and reads fresh on next agent invocation.
5. A mission running when the owner saves a new skill: the mission's next sub-agent invocation picks up the new skill on any bound agent (documented, accepted trade-off — same as SP4 in-flight semantics).
6. All prior gates green: `pytest`, `ruff`, `mypy`, `npm typecheck`, `npm lint`, `npm build`, `make api-types` drift check.

## 3. Storage

### 3.1 Schema

New file `sql/007_init_skills.sh`, applied by the existing `twaky-pg` init-script mechanism (matching `sql/006_init_agents.sh`'s bash+heredoc convention):

```sql
CREATE TABLE skill (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name           TEXT NOT NULL UNIQUE
                   CHECK (name ~ '^[a-z][a-z0-9_]{0,63}$'),
    description    TEXT NOT NULL CHECK (length(description) BETWEEN 1 AND 1000),
    python_source  TEXT NOT NULL CHECK (length(python_source) BETWEEN 1 AND 32000),
    config_schema  JSONB NOT NULL DEFAULT '{}'::jsonb,
    config_values  JSONB NOT NULL DEFAULT '{}'::jsonb,
    bound_agents   JSONB NOT NULL DEFAULT '[]'::jsonb
                   CHECK (jsonb_typeof(bound_agents) = 'array'),
    enabled        BOOLEAN NOT NULL DEFAULT true,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX skill_enabled_idx ON skill (enabled) WHERE enabled;

CREATE OR REPLACE FUNCTION public.notify_skill_changed() RETURNS trigger AS $NOTIFY$
BEGIN
  PERFORM pg_notify('skill_changed',
    COALESCE(NEW.id::text, OLD.id::text, 'ALL'));
  RETURN COALESCE(NEW, OLD);
END;
$NOTIFY$ LANGUAGE plpgsql;

CREATE TRIGGER skill_notify
  AFTER INSERT OR UPDATE OR DELETE ON public.skill
  FOR EACH ROW EXECUTE FUNCTION public.notify_skill_changed();

CREATE OR REPLACE FUNCTION public.skill_bump_updated_at() RETURNS trigger AS $BUMP$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$BUMP$ LANGUAGE plpgsql;

CREATE TRIGGER skill_touch_updated_at
  BEFORE UPDATE ON public.skill
  FOR EACH ROW EXECUTE FUNCTION public.skill_bump_updated_at();
```

### 3.2 Design notes

- **`id` is UUID**, not TEXT (unlike SP4's 4-agent table). Skills are user-created — no code-load-bearing name. `name` remains the LLM-visible callable identifier and is UNIQUE.
- **`name` regex matches LangChain's tool-name convention** (`^[a-z][a-z0-9_]{0,63}$`) — the LLM sees this string as the callable identifier in tool_calls. Rejects `sendEmail`, `Search-Wikipedia`, `1abc`, or names over 64 chars.
- **`bound_agents` is a JSONB array of TEXT** — `["atlas", "plume"]`. The DB CHECK asserts it's an array; the service layer (§5.3) validates each element is in `{atlas, chronos, plume, iris}`. Empty list is allowed (skill exists but no agent can currently call it — legitimate for staged rollout).
- **`config_schema` is a JSON Schema document** — service layer validates it via `jsonschema.Draft202012Validator.check_schema(...)` on write. `config_values` is validated against this schema on write too.
- **No seed data** — table starts empty. Owner creates their first skill via the UI.
- **NOTIFY fires on INSERT + UPDATE + DELETE** — so the daemon invalidates cache for enable/disable, creation, deletion, and edits. Payload is the skill id (or `OLD.id::text` on DELETE, `'ALL'` as a defensive fallback).
- **`created_at` is retained** (unlike SP4's `agent` table which only has `updated_at`) — skills are user-created and their creation timestamp is meaningful. `updated_at` still auto-bumps via BEFORE UPDATE trigger.

## 4. Subprocess executor

### 4.1 Invocation flow

When an agent's LLM decides to call a skill during a LangGraph node execution:

```
LLM tool_call: {"name": "search_wikipedia", "args": {"query": "Twake"}}
      │
      ▼
StructuredTool adapter (§5.2) receives the call
      │
      ▼
executor.run_skill(
    python_source=skill.python_source,
    args={"query": "Twake"},
    config=skill.config_values,
    timeout_s=30,
    memory_limit_mb=256,
) -> Any
      │
      ▼
Fork multiprocessing.Process, target=_worker
      │
      ├── _worker (child process):
      │     1. resource.setrlimit(RLIMIT_AS, (256*MB, 256*MB))
      │     2. resource.setrlimit(RLIMIT_CPU, (60, 60))
      │     3. resource.setrlimit(RLIMIT_NPROC, (0, 0))
      │     4. namespace = {}
      │     5. exec(python_source, namespace)
      │     6. run_fn = namespace["run"]
      │     7. try: result = run_fn(**args, **config)
      │        except Exception as e:
      │            pipe.send(("error", f"{type(e).__name__}: {e}"))
      │            sys.exit(1)
      │     8. pipe.send(("ok", result))  # pickled
      │     9. sys.exit(0)
      │
      ├── Parent: proc.join(timeout=30)
      │
      ├── If proc.is_alive() after join:
      │     proc.terminate(); proc.join(3); proc.kill()
      │     → raise SkillTimeout("skill timed out after 30s")
      │
      ├── If pipe empty AND proc.exitcode != 0:
      │     → raise SkillCrashed(f"skill exited with code {proc.exitcode}")
      │
      ├── If pipe delivered ("error", msg):
      │     → raise SkillError(msg)
      │
      └── If pipe delivered ("ok", result):
            → return result
```

### 4.2 Resource limits (Linux, via `resource` stdlib)

| Limit | Value (default) | Env override | Purpose |
|---|---|---|---|
| `RLIMIT_AS` | 256 MB | `TWAKY_SKILL_MEMORY_MB` | Cap virtual address space per skill invocation |
| `RLIMIT_CPU` | 60 s | `TWAKY_SKILL_CPU_S` | Cap CPU-seconds (kills tight loops before wall-clock timeout) |
| `RLIMIT_NPROC` | 0 | (none) | Prevent skill from forking further subprocesses |
| Wall-clock timeout | 30 s | `TWAKY_SKILL_TIMEOUT_S` | Belt-and-suspenders: parent `Process.join(timeout=30)` |

Env-var overrides are read at daemon startup, not per-invocation. Non-Linux systems (macOS dev) silently omit `RLIMIT_NPROC` (setting it to 0 kills the parent too on Darwin) — documented gap.

### 4.3 What subprocess isolation does NOT prevent

Documented explicitly in code comments AND the README, so no owner is misled:

- **Network egress.** The subprocess inherits the daemon's network stack. A skill can `httpx.get("http://internal-service.twake-network/")` — same reach as the daemon itself. Mitigation is only container-level (deploy the daemon in a network namespace that lacks the sensitive routes).
- **Filesystem writes.** The subprocess sees the daemon's filesystem. `RLIMIT_FSIZE` optionally caps single-file writes to 10 MB, but doesn't prevent them. Skills writing to `/tmp` are fine; skills writing to `/etc/passwd` on a wildly misconfigured deploy are not.
- **Env-var leakage.** The subprocess inherits the parent's env at fork time. `TWAKY_PG_PASSWORD`, `API_SESSION_SECRET`, all provider keys are readable via `os.environ`. **Real risk** — mitigation is documented (see §7.2) but not enforced in MVP.
- **Signal injection.** The subprocess can `os.kill(os.getppid(), signal.SIGTERM)` and take down the daemon. Rare in practice; not defended against.

The subprocess boundary is a **safety** boundary (catches accidents), not a **security** boundary (against a hostile author). For mono-user + trusted-owner, this is the right trade-off.

### 4.4 IPC

`multiprocessing.Pipe(duplex=False)` between parent and child. Payload is pickled tuples:
- `("ok", result)` — happy path. `result` must be pickle-serializable (LangChain tools typically return strings, dicts, or lists — all fine).
- `("error", str)` — user code raised an exception. `str` is `f"{type(exc).__name__}: {exc}"`.

Pickle is safe here because both endpoints are Twaky-controlled processes. Never used with untrusted data.

If the pipe write fails (broken pipe from a killed child), the parent's `pipe.recv()` raises `EOFError` — mapped to `SkillCrashed`.

### 4.5 Cost per invocation

Fork on Linux is ~10 ms. Setting rlimits is nanoseconds. Executing user code + pickling the result dominates for real workloads. Total overhead ~15 ms per call — acceptable given LLM tool calls typically take 100 ms - 10 s of I/O anyway.

Not pooled. Each LLM tool call = one fresh fork. Simpler, no state leakage between calls, no worker-crash-taints-pool scenarios.

## 5. Registry, tool adapter, agent integration

### 5.1 Registry cache

New file `src/twaky/skills/registry.py`. Same pattern as SP4's `agents/registry.py` (dict + lock + `_repository_get` indirection for test monkey-patching):

```python
import threading
from twaky.skills_config.models import Skill
from twaky.skills_config import repository

_cache: dict[str, list[Skill]] = {}  # keyed by agent_id
_lock = threading.Lock()


def _repository_get_bound(agent_id: str) -> list[Skill]:
    """Indirection kept for test monkeypatching."""
    return repository.list_bound_and_enabled(agent_id)


def load_skills_for_agent(agent_id: str) -> list[Skill]:
    """All enabled skills where agent_id in skill.bound_agents. Cache-first."""
    with _lock:
        cached = _cache.get(agent_id)
        if cached is not None:
            return cached
    fresh = _repository_get_bound(agent_id)
    with _lock:
        _cache[agent_id] = fresh
    return fresh


def invalidate_all() -> None:
    with _lock:
        _cache.clear()


__all__ = ["invalidate_all", "load_skills_for_agent"]
```

**Coarse invalidation on any change.** When a skill's `bound_agents` list changes, we don't know which agents to selectively invalidate. Flushing all 4 caches costs 4 tiny DB queries on next agent invocation — not worth per-agent tracking logic.

### 5.2 Tool adapter

New file `src/twaky/skills/tool_adapter.py`:

```python
import json
from langchain_core.tools import StructuredTool
from twaky.skills.executor import (
    run_skill, SkillTimeout, SkillCrashed, SkillError,
)
from twaky.skills_config.models import Skill


def skill_to_tool(skill: Skill) -> StructuredTool:
    """Wrap a Skill row into a StructuredTool the LLM can call."""

    def _invoke(**kwargs) -> str:
        try:
            result = run_skill(
                python_source=skill.python_source,
                args=kwargs,
                config=skill.config_values,
                timeout_s=30,
                memory_limit_mb=256,
            )
        except SkillTimeout:
            return f"skill '{skill.name}' timed out after 30s"
        except SkillCrashed as e:
            return f"skill '{skill.name}' crashed: {e}"
        except SkillError as e:
            return f"skill '{skill.name}' raised: {e}"
        if isinstance(result, str):
            return result
        return json.dumps(result, default=str)

    return StructuredTool.from_function(
        name=skill.name,
        description=skill.description,
        func=_invoke,
    )
```

**Args schema:** MVP uses `**kwargs`. The LLM passes any JSON dict; the skill's `def run(**kwargs)` receives it. A later refinement could derive a pydantic model from the skill's `run(...)` signature via `inspect.signature`, but not needed for the first cut — the tool description tells the LLM what args to send.

### 5.3 Agent-node integration

Extend all 4 `src/twaky/agents/*/agent.py` node functions (from SP4's refactor). Current shape:

```python
def _agent_node(state):
    cfg = load_agent_config("plume")
    llm = _make_llm(cfg).bind_tools(TOOLS)
    ...
```

New shape:

```python
from twaky.skills.registry import load_skills_for_agent
from twaky.skills.tool_adapter import skill_to_tool

def _agent_node(state):
    cfg = load_agent_config("plume")
    skills = load_skills_for_agent("plume")
    all_tools = TOOLS + [skill_to_tool(s) for s in skills]
    llm = _make_llm(cfg).bind_tools(all_tools)
    ...
```

Same 2-line delta per agent. The hardcoded `TOOLS` list stays as the built-in floor; skills stack on top.

**Name-collision guard.** `TOOLS + [skill_to_tool(s) for s in skills]` needs one filter step: skills whose `name` collides with a built-in tool are **dropped at bind time with a warning log**, not passed to the LLM. This prevents an owner from accidentally (or deliberately) shadowing `finish_mission`, `delegate_to_plume`, etc. Concretely:

```python
builtin_names = {t.name for t in TOOLS}
safe_skills = [s for s in skills if s.name not in builtin_names]
if len(safe_skills) < len(skills):
    log.warning("dropped %d skills colliding with built-in tool names",
                len(skills) - len(safe_skills))
all_tools = TOOLS + [skill_to_tool(s) for s in safe_skills]
```

**Atlas special case:** the routing logic in `_route` inspects the last message for tool calls. Skill tool calls behave identically — no change to routing. `finish_mission` and `delegate_to_*` stay hardcoded and unshadowable by the guard above.

### 5.4 NOTIFY listener

New file `src/twaky/skills/config_listener.py` — analog of SP4's `agents/config_listener.py`:

```python
import asyncio, logging
from twaky.skills import registry
from twaky.config import settings
from twaky.daemon.notify import listen

log = logging.getLogger("twaky.skills.config_listener")


async def run(stop_event: asyncio.Event) -> None:
    log.info("skill config listener starting")
    try:
        async for ch, payload in listen(["skill_changed"], settings.pg_dsn):
            if stop_event.is_set():
                return
            if ch == "skill_changed":
                log.info("skill changed, invalidating registry cache (payload=%s)", payload)
                registry.invalidate_all()  # coarse; see §5.1
    except asyncio.CancelledError:
        log.info("skill config listener cancelled")
        raise
```

Wired into `atlas_daemon._main_loop` alongside SP4's `config_listener` — one more `asyncio.create_task(...)`, one more `.cancel()` in the shutdown block. Daemon boot calls `registry.invalidate_all()` (via SP4's `registry.invalidate_all()`'s existing precedent).

## 6. API surface

Mirrors SP4 conventions: cookie-session auth via `require_owner`, uniform error envelope `{"error": {"code": ..., "message": ...}}`, router unprefixed on server (`/skills`), frontend rewrites `/api/*` → server.

### 6.1 Endpoints

```
GET    /skills                  → 200 [SkillSummary]        (owner-only)
GET    /skills/{id}             → 200 Skill | 404            (owner-only)
POST   /skills                  → 201 Skill | 422            (owner-only)
PATCH  /skills/{id}             → 200 Skill | 404 | 422      (owner-only)
DELETE /skills/{id}             → 204 | 404                  (owner-only)
POST   /skills/{id}/test        → 200 SkillTestResponse | 404 | 422   (owner-only)
```

### 6.2 Schemas

Added to `docs/api/openapi.yaml` (auto-regenerated via `make openapi` — the file is the FastAPI dump):

```yaml
Skill:
  type: object
  required: [id, name, description, python_source, config_schema, config_values,
             bound_agents, enabled, created_at, updated_at]
  properties:
    id:             { type: string, format: uuid }
    name:           { type: string, pattern: '^[a-z][a-z0-9_]{0,63}$' }
    description:    { type: string, minLength: 1, maxLength: 1000 }
    python_source:  { type: string, minLength: 1, maxLength: 32000 }
    config_schema:  { type: object, additionalProperties: true }
    config_values:  { type: object, additionalProperties: true }
    bound_agents:   { type: array, items: { type: string, enum: [atlas, chronos, plume, iris] } }
    enabled:        { type: boolean }
    created_at:     { type: string, format: date-time }
    updated_at:     { type: string, format: date-time }

SkillSummary:
  # Same as Skill minus python_source, config_schema, config_values.
  # Shorter payload for the list endpoint.

SkillCreate:
  type: object
  # bound_agents is optional (defaults to []) per §5.3 — a skill with no
  # bindings exists but cannot be called until an agent is bound. Kept
  # unrequired to enable staged rollout.
  required: [name, description, python_source]
  properties:
    name:           { type: string, pattern: '^[a-z][a-z0-9_]{0,63}$' }
    description:    { type: string, minLength: 1, maxLength: 1000 }
    python_source:  { type: string, minLength: 1, maxLength: 32000 }
    config_schema:  { type: object, additionalProperties: true, default: {} }
    config_values:  { type: object, additionalProperties: true, default: {} }
    bound_agents:   { type: array, items: { type: string, enum: [atlas, chronos, plume, iris] }, default: [] }
    enabled:        { type: boolean, default: true }

SkillUpdate:
  # All fields optional (partial update). Empty body → 422 (same rule as SP4).
  type: object
  properties:
    # ...same fields as SkillCreate, all optional...

SkillTestRequest:
  type: object
  required: [args]
  properties:
    args: { type: object, description: "kwargs passed to the skill's run() function" }

SkillTestResponse:
  type: object
  required: [outcome]
  properties:
    outcome: { type: string, enum: [ok, timeout, crashed, error] }
    result:  { description: "present when outcome=ok — any JSON-serializable value" }
    message: { type: string, description: "present when outcome != ok — human-readable" }
```

### 6.3 Validation

Enforced in `SkillCreate`/`SkillUpdate` pydantic models AND `skills_config.service.validate_*`:

- **`name`** — regex `^[a-z][a-z0-9_]{0,63}$`. Empty or malformed → 422 `validation_failed` (field=`name`).
- **`description`** — 1-1000 chars trimmed.
- **`python_source`** — 1-32000 chars trimmed. Additionally, service layer runs `ast.parse(source, mode="exec")` — syntax error → 422 (field=`python_source`, message includes `SyntaxError` line/col). Also asserts a top-level `def run(` occurs via `ast.walk` looking for `FunctionDef(name="run")` at module level — missing → 422 (field=`python_source`, message=`"module must define a top-level 'def run(...)' function"`).
- **`bound_agents`** — subset of `{atlas, chronos, plume, iris}`. Any other value → 422 (field=`bound_agents`).
- **`config_schema`** — pass to `jsonschema.Draft202012Validator.check_schema(...)`. Invalid → 422 (field=`config_schema`).
- **`config_values`** — validate against `config_schema` via `jsonschema.validate(...)`. Mismatch → 422 (field=`config_values`).
- **Empty PATCH body** → 422 `validation_failed` with message "at least one field required".
- **Name uniqueness** — DB UNIQUE constraint. Duplicate → repository raises `SkillNameConflict` → API returns 422 (field=`name`, message=`"a skill with this name already exists"`).

### 6.4 `POST /skills/{id}/test` behavior

Purpose: let the owner verify their skill runs before wiring it into an agent flow.

1. Load skill by id (404 if missing).
2. Combine `SkillTestRequest.args` with `skill.config_values`.
3. Call `executor.run_skill(...)` with production limits (256 MB, 30 s wall-clock, 60 CPU-seconds).
4. Return `SkillTestResponse`:
   - `{outcome: "ok", result: <value>}` on success.
   - `{outcome: "timeout"|"crashed"|"error", message: <human-readable>}` on failure.

**Always HTTP 200 unless:** the request body is malformed (422) or the skill is missing (404) or the executor infrastructure itself crashes (500). A skill failing is not a request failure; it's the answer.

The `/test` endpoint uses the SAME executor as production — the test result exactly reflects what an agent invocation would see.

### 6.5 Error envelope

Same shape as SP3a/SP4: `{"error": {"code": "...", "message": "..."}}`.

New codes:
- `skill_not_found` — 404 (id doesn't exist).
- `validation_failed` — 422 (any §6.3 rule).

Existing codes (`unauthorized`, `forbidden`) apply unchanged.

### 6.6 Code layout

- `src/twaky/skills_config/__init__.py`
- `src/twaky/skills_config/models.py` — `Skill` frozen dataclass.
- `src/twaky/skills_config/repository.py` — CRUD (`list_all`, `get`, `list_bound_and_enabled(agent_id)`, `create`, `update`, `delete`) + `SkillNameConflict`, `SkillNotFound`.
- `src/twaky/skills_config/service.py` — validation (name regex, ast.parse, jsonschema check, bound_agents subset) + `ValidationError`.
- `src/twaky/api/routers/skills.py` — 6 FastAPI endpoints.
- `src/twaky/api/schemas/skills.py` — pydantic models.

**Naming convention:** `skills_config/` (underscore, API/service/repo layer) is distinct from `skills/` (daemon-side: executor + registry + tool adapter + listener). This matches SP4's `agents_config/` vs `agents/` split.

## 7. Frontend UI

Two new routes inside the existing shell, one new nav-header link. Reuses SP4's data-flow pattern (openapi-fetch + TanStack Query + hooks).

### 7.1 `/skills` list page

- Path: `frontend/src/app/skills/page.tsx`.
- Uses `useSkills()` TanStack hook.
- Renders a shadcn Table:

  | Name | Description | Bound to | Enabled | Updated | |
  |------|-------------|----------|---------|---------|---|
  | `search_wikipedia` | Search Wikipedia by query | `atlas` `iris` | ● | 2 min ago | `[Edit]` `[Delete]` |
  | `notify_slack` | Post a message to Slack | `plume` | ○ (disabled) | 3 days ago | `[Edit]` `[Delete]` |

- "Name" cell uses `<code>` styling — monospace, subtle background.
- "Bound to" cell: small shadcn Badges (variant `secondary`), one per agent id.
- "Enabled" cell: filled green dot (enabled) / hollow gray dot (disabled).
- "Updated" cell: existing `RelativeTime` component from SP3b.
- Top-right of the header: **"+ New skill"** button → routes to `/skills/new`.
- Empty state (no skills yet): centered card, "No skills yet." + big CTA button "+ Create your first skill".
- **Delete flow**: `[Delete]` opens shadcn `AlertDialog` — "Delete `search_wikipedia`? Missions in flight that use it will fail on next call. This cannot be undone.". Confirmation calls `useDeleteSkill`, invalidates the list, toast on success.

### 7.2 `/skills/[id]` edit page (also serves `/skills/new`)

- Path: `frontend/src/app/skills/[id]/page.tsx`. When `id === "new"`, the form starts blank; save issues `POST`. Otherwise `useSkill(id)` populates fields; save issues `PATCH`.
- Two-column layout:

**Left column (2/3 width)** — Monaco Python editor:
- Library: `@monaco-editor/react` (MIT).
- Lazy-loaded via Next.js `dynamic(() => import(...), { ssr: false })` — Monaco requires `window`, and this keeps the bundle small on non-`/skills` pages.
- Language: `python`.
- Height: ~500 px (~25 rows visible), resizable via CSS if the browser supports it.
- Theme: matches app theme (`vs` for light, `vs-dark` for dark).
- Placeholder on empty (new skill): a starter template:
  ```python
  def run(**kwargs) -> str:
      """One-line description shown to the LLM."""
      # kwargs come from the LLM; config injected via config_values
      return "hello"
  ```
- **No language server, no linting, no autocomplete beyond Monaco default.** Correctness is checked by the backend on save (`ast.parse` + `def run` presence) and by the Test button on execute.

**Right column (1/3 width)** — metadata form:
- `Name` — shadcn `Input`, live-validated against `^[a-z][a-z0-9_]{0,63}$`. Red error message below on invalid.
- `Description` — shadcn `Textarea`, 3 rows, character counter `NNN / 1000`.
- `Bound agents` — 4 shadcn `Checkbox`es labeled Atlas / Chronos / Plume / Iris.
- `Enabled` — shadcn `Switch` (needs to be added via `npx shadcn add switch`).
- `Config schema` — collapsible (shadcn `Collapsible` — needs `npx shadcn add`), contains a small Monaco JSON editor (~150 px tall).
- `Config values` — same, another JSON editor. Live-validates against the schema via jsonschema in the browser (using `ajv` — new small dep, MIT). Red error line below the editor on mismatch.

**Bottom bar** (spans both columns):
- Left: **"Test"** button (opens the Test Dialog — §7.3).
- Right: **"Cancel"** (routes back to `/skills`) + **"Save"** primary (disabled unless form is valid and dirty).

### 7.3 Test dialog

- shadcn `Dialog` (needs `npx shadcn add dialog`).
- On open: JSON input for `args` (placeholder `{}`). If the skill has a signature-hint parsed client-side, pre-fill with `{"query": ""}` etc.
- "Run" button POSTs to `/skills/{id}/test`. Shows spinner while pending.
- Result section: outcome badge (green "ok" / red "timeout|crashed|error") + result/message (pretty-JSON block for `ok`, plain text for errors).
- **New-skill caveat**: for skills that haven't been saved yet (`id === "new"`), the Test button is disabled with tooltip "Save the skill first, then test." Testing an unsaved skill would require a `POST /skills/test-transient` endpoint — out of scope for MVP.

### 7.4 Hooks

New file `frontend/src/hooks/use-skills.ts` — matches SP4 `use-agents.ts` shape:

```ts
export function useSkills() { ... }              // GET /skills
export function useSkill(id: string) { ... }    // GET /skills/{id}
export function useCreateSkill() { ... }        // POST, invalidates ['skills']
export function useUpdateSkill(id: string) { ... }   // PATCH, invalidates ['skills'], ['skill', id]
export function useDeleteSkill(id: string) { ... }   // DELETE, invalidates ['skills']
export function useTestSkill(id: string) { ... }     // POST /test (mutation)
```

Same 401 handling via QueryCache (established SP3b).

### 7.5 Header nav link

`frontend/src/components/layout/header.tsx` gets one new link: **Skills** — positioned between **Agents** and **Stats** (SP4 established the Dashboard/Agents/Stats ordering; Skills slots in the middle).

### 7.6 shadcn primitives to add

Already installed (SP3b/SP4): Table, Badge, Button, Textarea, Input, Label, AlertDialog, Select, Checkbox, Sonner.

New this sub-project (via `npx shadcn add`):
- `switch` — for the Enabled toggle in the edit page.
- `dialog` — for the Test dialog.
- `collapsible` — for the Config schema/values sections.

### 7.7 No SSE integration

Skill saves don't need push notification — the user is the one saving; the mutation response has the fresh row. `<SSEProvider>` untouched.

## 8. Migration & seed

### 8.1 Initial schema

`sql/007_init_skills.sh` runs at container init (same slot as `004_init_mission.sh`, `005_init_checkpointer.sh`, `006_init_agents.sh`). Creates the `skill` table + both triggers + the `skill_enabled_idx` partial index. **No seed rows.**

### 8.2 Existing volumes

On the running production volume, the migration needs one-shot manual application (container init only runs on fresh volumes):

```bash
docker exec -i twaky-pg bash /docker-entrypoint-initdb.d/007_init_skills.sh
```

Documented in the README's "Custom skills" section.

### 8.3 No back-migration

Existing missions have no skill invocations (skills didn't exist). New missions may include skill tool calls from their first message. Missions are terminal artifacts (see SP3a spec §5); they are not re-executed.

## 9. Security & threat model

### 9.1 Auth

Identical to SP4: `require_owner` dependency on every `/skills/*` endpoint. 401 without session; 403 for non-owner session (impossible in mono-user but the guard stays for API consistency).

### 9.2 Threat model

**In scope (defended)**
- **Owner mistakes.** Typo → 422 at save via `ast.parse`. Infinite loop → 30 s timeout kill. OOM → 256 MB `RLIMIT_AS` kill. Crash → surfaced as tool return string, daemon stays alive.
- **External API misuse.** 401 on unauth requests, 422 on malformed bodies, no path to raise 500 from user input alone.
- **Skill-name collision.** DB UNIQUE constraint → 422 with clear message.

**NOT in scope (documented as owner-trust)**
- **Hostile owner.** If the owner pastes `os.remove('/')` and clicks Save, the daemon runs it. They also have SSH access to the host. Same tier of trust as editing Python files on disk — no new attack surface. Federation (SP6) will need to revisit this.
- **Network egress.** The subprocess inherits the daemon's network — a skill can call any internal or external HTTP endpoint. Container-level network policy is the only mitigation and is deploy-specific (out of scope for this sub-project).
- **Secret leakage.** Env vars (`TWAKY_PG_PASSWORD`, provider API keys) are inherited by the subprocess. **Real risk** — mitigation would be to `subprocess.Popen(env=scrubbed_env)` instead of `multiprocessing.Process`, but that costs the pickle-args convenience. Documented in README + spec; not addressed in MVP.

### 9.3 Defense in depth

- **API layer** (pydantic + service): syntax valid, `def run` present, name regex, bound_agents subset, config_schema+values type-checked.
- **DB layer** (CHECK constraints): name regex, description/python_source length bounds, bound_agents is an array. Redundant with API — catches direct `psql` edits.
- **Runtime layer** (subprocess executor): memory + CPU + wall-clock limits + no-fork. Kills accidents.

## 10. Testing

Matches SP4 conventions.

### 10.1 Python unit tests (~20)

- `tests/skills_config/test_repository.py` — CRUD (create returns row, get returns None on unknown, list_bound_and_enabled filters correctly, update partial, delete returns True/False, unique-name conflict raises `SkillNameConflict`).
- `tests/skills_config/test_service.py` — validation matrix:
  - Name regex: valid + invalid forms.
  - `python_source`: valid, syntax error, missing `run`, empty.
  - `bound_agents`: valid subsets, invalid ids, non-array.
  - `config_schema`: valid JSON Schema, invalid schema.
  - `config_values`: matches schema, mismatches schema.
  - Empty body → `ValidationError` with field=`_body`.
- `tests/skills/test_executor.py`:
  - Happy path: `def run(): return "hello"` returns `"hello"`.
  - Timeout: `def run(): time.sleep(60)` with 2 s cap → `SkillTimeout`.
  - Memory OOM: `def run(): x = [0] * 10**9` with 64 MB cap → `SkillCrashed` (OOM kill) OR `SkillError` (MemoryError caught inside).
  - Crash: `def run(): raise ValueError("boom")` → `SkillError` with message containing `"ValueError: boom"`.
  - Non-picklable return: `def run(): return threading.Lock()` → `SkillError` with `PicklingError`.
  - Config injection: `def run(**kwargs): return kwargs["endpoint"]` with `config_values={"endpoint": "https://x"}` returns `"https://x"`.
  - Both `args` AND `config`: `def run(query, endpoint): return f"{endpoint}?q={query}"` with args + config merged.
- `tests/skills/test_registry.py` — cache miss/hit, `invalidate_all` clears everything, per-agent isolation (loading atlas doesn't warm plume).
- `tests/skills/test_tool_adapter.py` — `skill_to_tool` returns `StructuredTool` with correct `name`, `description`; invoking it exercises the executor path; error mapping (SkillTimeout → tool returns `"skill 'X' timed out after 30s"`).

### 10.2 Python API tests (~10)

- `tests/api/routers/test_skills.py` — full endpoint matrix:
  - GET list: 401 no session, 200 with session returns list.
  - GET one: 200 for known id, 404 for unknown UUID.
  - POST: 201 with fresh row, 422 for each validation rule (bad name, invalid Python, missing `run`, bad bound_agent, non-JSON-Schema config_schema, empty body).
  - PATCH: 200 partial update, 422 for empty body, 404 for unknown.
  - DELETE: 204 + row gone, 404 for unknown.
  - POST /test: 200 with each outcome (ok, timeout, crashed, error via mocked executor), 404 for unknown skill.

### 10.3 Python integration tests (~2)

- `tests/integration/test_skills_executor_limits.py` — real subprocess with real `RLIMIT_AS` — verify a `[0] * 10**9` skill IS killed within timeout.
- `tests/integration/test_skills_config_listener.py` — real Postgres + NOTIFY: create a skill row, wait <1 s, assert `registry._cache` was invalidated.

### 10.4 Frontend unit tests (~10)

- `use-skills.test.ts` — MSW-mocked hooks: `useSkills`, `useSkill`, `useCreateSkill`, `useUpdateSkill`, `useDeleteSkill`, `useTestSkill`.
- `agent-checkboxes.test.tsx` — checkbox state ↔ `bound_agents` array.
- `name-validator.test.tsx` — live regex check turns error state on / off.
- `test-dialog.test.tsx` — Run button issues mutation, result panel switches on outcome.

### 10.5 Frontend E2E (~2)

- `frontend/tests/e2e/skills-create.spec.ts`: sign in → navigate `/skills` → click "+ New skill" → paste `def run(**kwargs): return "hello"` → set name `echo`, description `Echo`, check Atlas → Save → verify appears in list.
- `frontend/tests/e2e/skills-test.spec.ts`: sign in → `/skills` → click Edit on `echo` → click Test → paste `{"foo": "bar"}` → Run → verify "outcome: ok" + result contains `"hello"`.

### 10.6 Whole-repo gates

Unchanged: `pytest`, `ruff check` + `format --check`, `mypy`, `npm typecheck`, `npm lint`, `npm test:unit`, `npm build`, `make api-types && git diff --exit-code src/lib/api-types.d.ts`.

## 11. Task decomposition preview

Final numbering happens in `writing-plans`. Rough breakdown:

- **T1** — `sql/007_init_skills.sh` + drift-safe re-run + smoke migration test.
- **T2** — `skills_config/models.py` + `repository.py` (CRUD + `list_bound_and_enabled`, `SkillNameConflict`, `SkillNotFound`) + tests.
- **T3** — `skills_config/service.py` (name regex, ast.parse + `def run` presence, jsonschema validation, bound_agents subset, ValidationError) + tests.
- **T4** — `skills/executor.py` (multiprocessing.Process + rlimits + wall-clock timeout + IPC + SkillTimeout/Crashed/Error) + unit tests + integration test with real limits.
- **T5** — `skills/registry.py` (cache + `load_skills_for_agent` + `invalidate_all` + `_repository_get_bound` seam for tests) + tests.
- **T6** — `skills/tool_adapter.py` (Skill → StructuredTool + error → string mapping) + tests.
- **T7** — `skills/config_listener.py` + integration test with real NOTIFY.
- **T8** — Wire listener into `atlas_daemon._main_loop` + `registry.invalidate_all()` at boot + smoke test.
- **T9** — Refactor 4 agent modules to append skills to `TOOLS`. Existing agent tests updated with `stub_skills_for` helper.
- **T10** — `api/schemas/skills.py` + `api/routers/skills.py`: `GET /skills`, `GET /skills/{id}` + tests.
- **T11** — `POST /skills` + `PATCH /skills/{id}` + `DELETE /skills/{id}` + full 422 matrix.
- **T12** — `POST /skills/{id}/test` + 4 outcome cases + tests.
- **T13** — Regen `docs/api/openapi.yaml` via `make openapi` + regen `frontend/src/lib/api-types.d.ts` via `make api-types` + drift-check test still passes.
- **T14** — Frontend hooks in `frontend/src/hooks/use-skills.ts` + 6 MSW-mocked tests.
- **T15** — Frontend `/skills` list page + `+ New skill` button + Delete AlertDialog + Nav link (positioned between Agents and Stats).
- **T16** — Frontend `/skills/[id]` edit page (Monaco lazy-loaded + metadata form + config JSON editors + shadcn `switch`/`dialog`/`collapsible` installs).
- **T17** — Test Dialog + result panel + `useTestSkill` integration.
- **T18** — Playwright E2E: `skills-create.spec.ts` + `skills-test.spec.ts`.
- **T19** — README section "## Custom skills (sub-project 5)" including subprocess-isolation caveats + full-repo gate sweep.

**~19 tasks.** Comfortably under the 25 target. No 5a/5b decomposition needed for this sub-project — custom agents are a separate SP5b as agreed.

## 12. Global constraints (for the plan)

Copy verbatim into the plan's Global Constraints block:

- **Endpoint mount**: `/skills/*` at the API root — never `/api/skills/*` server-side. Frontend rewrites `/api/*` → server via `next.config.ts`, matching SP4 convention.
- **Table name**: `skill` (singular, unquoted).
- **NOTIFY channel name**: `skill_changed` (verbatim).
- **Payload of `skill_changed`**: the skill's UUID as string, OR `'ALL'` on delete-with-null. Listener treats any payload as "invalidate all" (coarse strategy per §5.1).
- **Skill `name` regex**: `^[a-z][a-z0-9_]{0,63}$` — enforced in DB CHECK, pydantic pattern, and frontend live validator (three layers).
- **`bound_agents` values**: subset of `{atlas, chronos, plume, iris}` — validated in service layer.
- **`python_source` bounds**: 1-32000 chars trimmed. `ast.parse` must succeed. Top-level `def run(...)` must exist.
- **`description` bounds**: 1-1000 chars trimmed.
- **Python package**: `src/twaky/skills_config/` (API/service/repo, with underscore) and `src/twaky/skills/` (daemon-side: executor, registry, tool_adapter, config_listener) — TWO packages, matching SP4's `agents_config/` vs `agents/` split.
- **Executor limits**: 256 MB memory (`RLIMIT_AS`), 60 CPU-seconds (`RLIMIT_CPU`), 0 subprocess-fork (`RLIMIT_NPROC`), 30 s wall-clock timeout (parent-side `Process.join`). MVP hardcodes; env-var overrides deferred.
- **Executor uses `multiprocessing.Process`**, not `subprocess.Popen` — args are pickled through a `Pipe`, not serialized as CLI args or stdin.
- **On unpicklable return**: raise `SkillError` with the `PicklingError` message. Do not silently coerce.
- **Skill tool wrapping**: `StructuredTool.from_function(name=skill.name, description=skill.description, func=_invoke)` — args_schema is `**kwargs` (any JSON dict) for MVP. No pydantic model derivation from user code.
- **Error envelope**: same `{"error": {"code": "...", "message": "..."}}` shape as SP4. New codes: `skill_not_found` (404), `validation_failed` (422).
- **Frontend nav link**: label `Skills`, positioned between `Agents` and `Stats` in the header.
- **Monaco lazy-loading**: `dynamic(() => import('@monaco-editor/react'), { ssr: false })` — MUST NOT bloat the initial bundle on non-`/skills` pages.
- **No versioning, no history, no marketplace, no custom agents** — YAGNI. SP5b handles custom agents; SP6+ handles federation-enabled marketplace.
- **`POST /skills/{id}/test`** — always returns HTTP 200 with an `outcome` field unless the request itself is malformed (422) or the skill is missing (404). Never treat a skill runtime error as a 500.
- **`finish_mission` and `delegate_to_*` tools** stay hardcoded — skills cannot shadow or replace them (the name regex `^[a-z][a-z0-9_]{0,63}$` allows a skill named `finish_mission`; the tool adapter must check for and reject collisions with the built-in tool names at bind time, OR the built-in tools shadow the skill via list ordering — plan T9 must specify which).

---

**End of design spec.** Ready for implementation planning via `superpowers:writing-plans`.
