# Twaky Custom Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner author custom Python skills via the web UI, each running in an isolated subprocess with resource limits, live-reloaded via `LISTEN/NOTIFY`, and bindable to any subset of the 4 built-in agents (Atlas, Chronos, Plume, Iris) — with no daemon restart.

**Architecture:** New `skill` table on `twaky-pg` (UUID PK, owner-editable). Two new Python packages: `src/twaky/skills_config/` (repository + service + validation) exposes CRUD over FastAPI at `/skills/*`; `src/twaky/skills/` (executor + registry + tool_adapter + config_listener) runs alongside the atlas daemon. Executor uses `multiprocessing.Process` + `resource.setrlimit(RLIMIT_AS, RLIMIT_CPU, RLIMIT_NPROC)` + wall-clock timeout. Registry cache is invalidated coarsely on any `skill_changed` NOTIFY. Each `Skill` row is wrapped as a LangChain `StructuredTool` and appended to the hardcoded `TOOLS` list of each bound agent at node-invocation time. Frontend adds `/skills` list + `/skills/[id]` edit page with a lazy-loaded Monaco Python editor and a Test dialog.

**Tech Stack:** Python 3.12, psycopg3 (raw SQL, no ORM), FastAPI, pydantic v2, LangGraph, LangChain `StructuredTool`, ChatLiteLLM, `jsonschema` (Draft 2020-12), `multiprocessing` + `resource` stdlib, Next.js 15 App Router, TanStack Query v5, openapi-fetch, shadcn/ui, Radix primitives, `@monaco-editor/react` (MIT), `ajv` (browser JSON Schema), Vitest, MSW, Playwright.

## Global Constraints

Every task's requirements implicitly include this section — copied verbatim from spec §12:

- **Endpoint mount:** `/skills/*` at the API root — never `/api/skills/*` server-side. Frontend rewrites `/api/*` → server via `next.config.ts`, matching SP4 convention.
- **Table name:** `skill` (singular, unquoted).
- **NOTIFY channel name:** `skill_changed` (verbatim).
- **Payload of `skill_changed`:** the skill's UUID as string, OR `'ALL'` on delete-with-null. Listener treats any payload as "invalidate all" (coarse strategy per spec §5.1).
- **Skill `name` regex:** `^[a-z][a-z0-9_]{0,63}$` — enforced in DB CHECK, pydantic pattern, and frontend live validator (three layers).
- **`bound_agents` values:** subset of `{atlas, chronos, plume, iris}` — validated in service layer.
- **`python_source` bounds:** 1-32000 chars trimmed. `ast.parse` must succeed. Top-level `def run(...)` must exist.
- **`description` bounds:** 1-1000 chars trimmed.
- **Python packages:** `src/twaky/skills_config/` (API/service/repo, with underscore) and `src/twaky/skills/` (daemon-side: executor, registry, tool_adapter, config_listener) — TWO packages, matching SP4's `agents_config/` vs `agents/` split.
- **Executor limits:** 256 MB memory (`RLIMIT_AS`), 60 CPU-seconds (`RLIMIT_CPU`), 0 subprocess-fork (`RLIMIT_NPROC`), 30 s wall-clock timeout (parent-side `Process.join`). MVP hardcodes; env-var overrides deferred.
- **Executor uses `multiprocessing.Process`**, not `subprocess.Popen` — args are pickled through a `Pipe`, not serialized as CLI args or stdin.
- **On unpicklable return:** raise `SkillError` with the `PicklingError` message. Do not silently coerce.
- **Skill tool wrapping:** `StructuredTool.from_function(name=skill.name, description=skill.description, func=_invoke)` — args_schema is `**kwargs` (any JSON dict) for MVP. No pydantic model derivation from user code.
- **Error envelope:** same `{"error": {"code": "...", "message": "..."}}` shape as SP4. New codes: `skill_not_found` (404), `validation_failed` (422).
- **Frontend nav link:** label `Skills`, positioned between `Agents` and `Stats` in the header.
- **Monaco lazy-loading:** `dynamic(() => import('@monaco-editor/react'), { ssr: false })` — MUST NOT bloat the initial bundle on non-`/skills` pages.
- **No versioning, no history, no marketplace, no custom agents** — YAGNI. SP5b handles custom agents; SP6+ handles federation-enabled marketplace.
- **`POST /skills/{id}/test`** — always returns HTTP 200 with an `outcome` field unless the request itself is malformed (422) or the skill is missing (404). Never treat a skill runtime error as a 500.
- **`finish_mission` and `delegate_to_*` tools** stay hardcoded — the tool adapter (T9) MUST filter out any skill whose `name` collides with a built-in tool name at bind time, with a warning log.
- **SQL migration convention:** twaky-pg init scripts are `.sh` files running heredoc'd `psql`, numbered `NNN_init_<domain>.sh` — see `sql/006_init_agents.sh` for the template. This plan writes `sql/007_init_skills.sh`.

---

## File Structure

**Created files (new)**

| Path | Purpose |
|---|---|
| `sql/007_init_skills.sh` | psql-heredoc migration: `skill` table + `notify_skill_changed` + `skill_bump_updated_at` triggers + `skill_enabled_idx` partial index |
| `src/twaky/skills_config/__init__.py` | Empty package init |
| `src/twaky/skills_config/models.py` | `Skill` frozen dataclass |
| `src/twaky/skills_config/repository.py` | Raw psycopg CRUD: `list_all`, `get`, `list_bound_and_enabled(agent_id)`, `create`, `update`, `delete` + `SkillNameConflict`, `SkillNotFound` |
| `src/twaky/skills_config/service.py` | Validation (name regex, ast.parse+`def run` presence, jsonschema, bound_agents subset) + `ValidationError` |
| `src/twaky/skills/__init__.py` | Empty package init |
| `src/twaky/skills/executor.py` | `run_skill()` + `SkillTimeout`, `SkillCrashed`, `SkillError` — multiprocessing.Process + rlimits + IPC pipe |
| `src/twaky/skills/registry.py` | Thread-safe per-agent cache: `load_skills_for_agent()`, `invalidate_all()` |
| `src/twaky/skills/tool_adapter.py` | `skill_to_tool(Skill) -> StructuredTool` + error → string mapping |
| `src/twaky/skills/config_listener.py` | Async LISTEN loop; invalidates registry on any `skill_changed` NOTIFY |
| `src/twaky/api/routers/skills.py` | 6 FastAPI endpoints (list, get, create, patch, delete, test) |
| `src/twaky/api/schemas/skills.py` | `Skill`, `SkillSummary`, `SkillCreate`, `SkillUpdate`, `SkillTestRequest`, `SkillTestResponse` pydantic models |
| `tests/skills_config/__init__.py` | Empty |
| `tests/skills_config/test_repository.py` | CRUD unit tests + unique-name conflict + list_bound_and_enabled filtering |
| `tests/skills_config/test_service.py` | Validation matrix (name regex, ast.parse, def-run presence, bound_agents, config_schema/values, empty body) |
| `tests/skills/__init__.py` | Empty |
| `tests/skills/test_executor.py` | Happy path + timeout + memory OOM + crash + non-picklable return + config injection + args+config merge |
| `tests/skills/test_registry.py` | Cache miss/hit + invalidate_all + per-agent isolation |
| `tests/skills/test_tool_adapter.py` | `skill_to_tool` returns StructuredTool with correct name/description + error mapping |
| `tests/api/routers/test_skills.py` | Full endpoint matrix (list, get, create, patch, delete, test) + 422 rules + 404s |
| `tests/integration/test_skills_executor_limits.py` | Real subprocess with real RLIMIT_AS — verify OOM kill within timeout |
| `tests/integration/test_skills_config_listener.py` | Real Postgres + NOTIFY: create skill row, wait <1s, assert registry cache invalidated |
| `frontend/src/hooks/use-skills.ts` | `useSkills`, `useSkill`, `useCreateSkill`, `useUpdateSkill`, `useDeleteSkill`, `useTestSkill` |
| `frontend/src/hooks/use-skills.test.tsx` | MSW-mocked hook tests |
| `frontend/src/app/skills/page.tsx` | List page (Table + New Skill button + Delete AlertDialog + empty state) |
| `frontend/src/app/skills/[id]/page.tsx` | Edit page — also serves `/skills/new` (Monaco left column + metadata form right column + Test/Save bar) |
| `frontend/src/components/skills/skill-name-input.tsx` | Live regex validator |
| `frontend/src/components/skills/skill-name-input.test.tsx` | Toggle on/off for valid/invalid patterns |
| `frontend/src/components/skills/skill-bound-agents.tsx` | 4 checkboxes ↔ `bound_agents` array |
| `frontend/src/components/skills/skill-bound-agents.test.tsx` | Checkbox state ↔ array |
| `frontend/src/components/skills/skill-python-editor.tsx` | Lazy-loaded `@monaco-editor/react` Python editor with starter template |
| `frontend/src/components/skills/skill-config-editors.tsx` | Collapsible JSON Schema + JSON Values editors (Monaco + ajv validation) |
| `frontend/src/components/skills/skill-test-dialog.tsx` | Test dialog (JSON args input + Run + outcome/result panel) |
| `frontend/src/components/skills/skill-test-dialog.test.tsx` | Run button issues mutation; outcome switches |
| `frontend/src/components/ui/switch.tsx` | shadcn Switch (added via `npx shadcn add switch`) |
| `frontend/src/components/ui/dialog.tsx` | shadcn Dialog (`npx shadcn add dialog`) |
| `frontend/src/components/ui/collapsible.tsx` | shadcn Collapsible (`npx shadcn add collapsible`) |
| `frontend/tests/e2e/skills-create.spec.ts` | E2E happy path (create + save + appears in list) |
| `frontend/tests/e2e/skills-test.spec.ts` | E2E test-dialog path (open, run, verify outcome) |

**Modified files (existing)**

| Path | Change |
|---|---|
| `src/twaky/daemon/atlas_daemon.py` | Import `twaky.skills.config_listener`; boot-time `skills_registry.invalidate_all()`; `asyncio.create_task(skills_config_listener.run(stop))`; cancel on shutdown |
| `src/twaky/agents/atlas/agent.py` | Node function appends filtered skills to `TOOLS` before `bind_tools()` |
| `src/twaky/agents/chronos/agent.py` | Same shape as atlas |
| `src/twaky/agents/plume/agent.py` | Same shape |
| `src/twaky/agents/iris/agent.py` | Same shape |
| `src/twaky/api/main.py` | `app.include_router(skills.router)` after agents |
| `docs/api/openapi.yaml` | Regenerated via `make openapi` |
| `frontend/src/lib/api-types.d.ts` | Regenerated via `make api-types` |
| `frontend/src/components/layout/header.tsx` | Add "Skills" nav link between "Agents" and "Stats" |
| `frontend/package.json` | Add `@monaco-editor/react` + `ajv` deps |
| `tests/agents/test_atlas_agent.py` | Stub `load_skills_for_agent("atlas")` returning `[]` |
| `tests/agents/test_chronos_agent.py` | Stub returns `[]` |
| `tests/agents/test_plume_agent.py` | Stub returns `[]` |
| `tests/agents/test_iris_agent.py` | Stub returns `[]` |
| `README.md` | New section "## Custom skills (sub-project 5)" — includes subprocess-isolation caveats + one-shot migration on existing volume |

---

## Task 1: Migration — `sql/007_init_skills.sh`

**Files:**
- Create: `sql/007_init_skills.sh`
- Create: `tests/sql/test_skills_migration.py`

**Interfaces:**
- Consumes: nothing (fresh table).
- Produces:
  - Postgres table `skill` with columns `id UUID PK`, `name TEXT UNIQUE` (regex CHECK), `description TEXT` (1-1000 CHECK), `python_source TEXT` (1-32000 CHECK), `config_schema JSONB`, `config_values JSONB`, `bound_agents JSONB` (array-typeof CHECK), `enabled BOOLEAN`, `created_at TIMESTAMPTZ`, `updated_at TIMESTAMPTZ`.
  - Partial index `skill_enabled_idx ON skill (enabled) WHERE enabled`.
  - PG function `notify_skill_changed()` (AFTER INSERT/UPDATE/DELETE) — payload is `COALESCE(NEW.id::text, OLD.id::text, 'ALL')` on channel `skill_changed`.
  - PG function `skill_bump_updated_at()` (BEFORE UPDATE) — sets `NEW.updated_at := now()`.

- [ ] **Step 1: Write `sql/007_init_skills.sh`**

Model after `sql/006_init_agents.sh`. Uses a single quoted heredoc (`<<-'EOSQL'`) so dollar-quoted psql function bodies (`$NOTIFYFN$`, `$BUMPFN$`) are NOT expanded by bash — psql sees them literally.

```bash
#!/bin/bash
# Provision the `skill` table + NOTIFY/updated_at triggers + partial index.
# Runs once on first-boot volume init. For existing volumes:
#   docker exec -i twaky-pg bash /docker-entrypoint-initdb.d/007_init_skills.sh
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-twaky}" <<-'EOSQL'
    CREATE TABLE IF NOT EXISTS public.skill (
        id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name           TEXT NOT NULL UNIQUE
                       CHECK (name ~ '^[a-z][a-z0-9_]{0,63}$'),
        description    TEXT NOT NULL
                       CHECK (length(description) BETWEEN 1 AND 1000),
        python_source  TEXT NOT NULL
                       CHECK (length(python_source) BETWEEN 1 AND 32000),
        config_schema  JSONB NOT NULL DEFAULT '{}'::jsonb,
        config_values  JSONB NOT NULL DEFAULT '{}'::jsonb,
        bound_agents   JSONB NOT NULL DEFAULT '[]'::jsonb
                       CHECK (jsonb_typeof(bound_agents) = 'array'),
        enabled        BOOLEAN NOT NULL DEFAULT true,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE INDEX IF NOT EXISTS skill_enabled_idx
        ON public.skill (enabled) WHERE enabled;

    CREATE OR REPLACE FUNCTION public.notify_skill_changed() RETURNS trigger AS $NOTIFYFN$
    BEGIN
      PERFORM pg_notify('skill_changed',
        COALESCE(NEW.id::text, OLD.id::text, 'ALL'));
      RETURN COALESCE(NEW, OLD);
    END;
    $NOTIFYFN$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS skill_notify ON public.skill;
    CREATE TRIGGER skill_notify
      AFTER INSERT OR UPDATE OR DELETE ON public.skill
      FOR EACH ROW EXECUTE FUNCTION public.notify_skill_changed();

    CREATE OR REPLACE FUNCTION public.skill_bump_updated_at() RETURNS trigger AS $BUMPFN$
    BEGIN
      NEW.updated_at := now();
      RETURN NEW;
    END;
    $BUMPFN$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS skill_touch_updated_at ON public.skill;
    CREATE TRIGGER skill_touch_updated_at
      BEFORE UPDATE ON public.skill
      FOR EACH ROW EXECUTE FUNCTION public.skill_bump_updated_at();
EOSQL
```

- [ ] **Step 2: Make the shell script executable**

```bash
chmod +x sql/007_init_skills.sh
```

- [ ] **Step 3: Write `tests/sql/test_skills_migration.py`**

```python
"""Static assertions on the migration script.

Runs without a live Postgres. Full DB behavior is exercised in
tests/integration/test_skills_config_listener.py (real NOTIFY).
"""

from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "sql" / "007_init_skills.sh"


def test_script_exists_and_is_executable():
    assert SCRIPT.exists(), f"{SCRIPT} missing"
    assert SCRIPT.stat().st_mode & 0o111, f"{SCRIPT} not executable"


def test_creates_skill_table():
    text = SCRIPT.read_text()
    assert "CREATE TABLE IF NOT EXISTS public.skill" in text
    for col in (
        "id             UUID PRIMARY KEY",
        "name           TEXT NOT NULL UNIQUE",
        "python_source  TEXT NOT NULL",
        "bound_agents   JSONB NOT NULL",
        "enabled        BOOLEAN NOT NULL",
    ):
        assert col in text, f"missing column definition: {col!r}"


def test_declares_name_regex_check():
    text = SCRIPT.read_text()
    assert "name ~ '^[a-z][a-z0-9_]{0,63}$'" in text


def test_declares_partial_enabled_index():
    text = SCRIPT.read_text()
    assert (
        "CREATE INDEX IF NOT EXISTS skill_enabled_idx" in text
        and "WHERE enabled" in text
    )


def test_declares_notify_trigger_on_all_dml():
    text = SCRIPT.read_text()
    assert "pg_notify('skill_changed'" in text
    assert "AFTER INSERT OR UPDATE OR DELETE ON public.skill" in text


def test_declares_updated_at_trigger():
    text = SCRIPT.read_text()
    assert "BEFORE UPDATE ON public.skill" in text
    assert "NEW.updated_at := now()" in text
```

- [ ] **Step 4: Run the migration tests**

```bash
uv run pytest tests/sql/test_skills_migration.py -v
```

Expected: 5 tests pass.

- [ ] **Step 5: Apply the migration on the local twaky-pg volume**

The migration only auto-runs on fresh volumes. Existing volumes need a one-shot manual run:

```bash
docker exec -i twaky-pg bash /docker-entrypoint-initdb.d/007_init_skills.sh
```

Then verify the table exists:

```bash
docker exec -i twaky-pg psql -U "$POSTGRES_USER" -d twaky -c '\d skill'
```

Expected: table structure printed, showing the 10 columns and both triggers.

- [ ] **Step 6: Commit**

```bash
git add sql/007_init_skills.sh tests/sql/test_skills_migration.py
git commit -m "feat(skills): init skill table + NOTIFY/updated_at triggers"
```

---

## Task 2: `skills_config` — models + repository

**Files:**
- Create: `src/twaky/skills_config/__init__.py` (empty)
- Create: `src/twaky/skills_config/models.py`
- Create: `src/twaky/skills_config/repository.py`
- Create: `tests/skills_config/__init__.py` (empty)
- Create: `tests/skills_config/test_repository.py`

**Interfaces:**
- Consumes: `twaky.db.get_pool()` (existing), Postgres `skill` table (T1).
- Produces:
  - `Skill` frozen dataclass — `id: UUID, name: str, description: str, python_source: str, config_schema: dict, config_values: dict, bound_agents: list[str], enabled: bool, created_at: datetime, updated_at: datetime`.
  - Exceptions: `SkillNotFound`, `SkillNameConflict`.
  - Functions in `repository`:
    - `list_all() -> list[Skill]` — ordered by name.
    - `get(skill_id: UUID) -> Skill | None`.
    - `list_bound_and_enabled(agent_id: str) -> list[Skill]` — `bound_agents @> [agent_id] AND enabled`.
    - `create(*, name, description, python_source, config_schema, config_values, bound_agents, enabled) -> Skill` — raises `SkillNameConflict` on UNIQUE violation.
    - `update(skill_id: UUID, patch: dict) -> Skill` — raises `SkillNotFound` if row missing, `SkillNameConflict` on UNIQUE violation, `ValueError` on empty patch.
    - `delete(skill_id: UUID) -> bool` — returns True if a row was deleted, False if not found.

- [ ] **Step 1: Write `src/twaky/skills_config/__init__.py`**

Empty file:

```python
```

- [ ] **Step 2: Write `src/twaky/skills_config/models.py`**

```python
"""Dataclass carried between DB, service, tool_adapter, and API mapping layers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class Skill:
    id: UUID
    name: str
    description: str
    python_source: str
    config_schema: dict
    config_values: dict
    bound_agents: list[str]
    enabled: bool
    created_at: datetime
    updated_at: datetime


__all__ = ["Skill"]
```

- [ ] **Step 3: Write `src/twaky/skills_config/repository.py`**

```python
"""psycopg3 CRUD for the `skill` table.

Raw SQL, matching src/twaky/agents_config/repository.py convention.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from twaky.db import get_pool
from twaky.skills_config.models import Skill


class SkillNotFound(Exception):
    pass


class SkillNameConflict(Exception):
    pass


_UNIQUE_NAME_CONSTRAINT = "skill_name_key"


def _row_to_skill(row: dict[str, Any]) -> Skill:
    return Skill(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        python_source=row["python_source"],
        config_schema=row["config_schema"] or {},
        config_values=row["config_values"] or {},
        bound_agents=list(row["bound_agents"] or []),
        enabled=row["enabled"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def list_all() -> list[Skill]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM skill ORDER BY name")
        rows = cur.fetchall()
    return [_row_to_skill(r) for r in rows]


def get(skill_id: UUID) -> Skill | None:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM skill WHERE id = %s", (skill_id,))
        row = cur.fetchone()
    return _row_to_skill(row) if row else None


def list_bound_and_enabled(agent_id: str) -> list[Skill]:
    """Enabled skills whose bound_agents JSONB array contains agent_id."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM skill "
            "WHERE enabled AND bound_agents @> %s::jsonb "
            "ORDER BY name",
            (json.dumps([agent_id]),),
        )
        rows = cur.fetchall()
    return [_row_to_skill(r) for r in rows]


def create(
    *,
    name: str,
    description: str,
    python_source: str,
    config_schema: dict,
    config_values: dict,
    bound_agents: list[str],
    enabled: bool = True,
) -> Skill:
    try:
        with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "INSERT INTO skill "
                "(name, description, python_source, config_schema, "
                " config_values, bound_agents, enabled) "
                "VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s) "
                "RETURNING *",
                (
                    name,
                    description,
                    python_source,
                    json.dumps(config_schema),
                    json.dumps(config_values),
                    json.dumps(bound_agents),
                    enabled,
                ),
            )
            row = cur.fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise SkillNameConflict(name) from exc
    return _row_to_skill(row)


def update(skill_id: UUID, patch: dict[str, Any]) -> Skill:
    if not patch:
        raise ValueError("empty patch")

    allowed = {
        "name", "description", "python_source",
        "config_schema", "config_values", "bound_agents", "enabled",
    }
    bad = set(patch) - allowed
    if bad:
        raise ValueError(f"unknown fields: {sorted(bad)}")

    set_clauses: list[str] = []
    params: list[Any] = []
    for key, value in patch.items():
        if key in {"config_schema", "config_values", "bound_agents"}:
            set_clauses.append(f"{key} = %s::jsonb")
            params.append(json.dumps(value))
        else:
            set_clauses.append(f"{key} = %s")
            params.append(value)
    params.append(skill_id)

    sql = f"UPDATE skill SET {', '.join(set_clauses)} WHERE id = %s RETURNING *"

    try:
        with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise SkillNameConflict(patch.get("name")) from exc

    if row is None:
        raise SkillNotFound(str(skill_id))
    return _row_to_skill(row)


def delete(skill_id: UUID) -> bool:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM skill WHERE id = %s", (skill_id,))
        return cur.rowcount == 1


__all__ = [
    "Skill", "SkillNotFound", "SkillNameConflict",
    "list_all", "get", "list_bound_and_enabled",
    "create", "update", "delete",
]
```

- [ ] **Step 4: Write `tests/skills_config/__init__.py`**

Empty file.

- [ ] **Step 5: Write `tests/skills_config/test_repository.py`**

Uses the existing `_pg_pool` fixture pattern from `tests/agents_config/test_repository.py` — a real Postgres via `docker-compose up twaky-pg` (or the test DB fixture already established in SP4).

```python
"""CRUD tests for skills_config.repository. Uses the shared real-Postgres fixture."""

from __future__ import annotations

import pytest

from twaky.skills_config import repository as repo
from twaky.skills_config.repository import SkillNameConflict, SkillNotFound

pytestmark = pytest.mark.usefixtures("pg_pool")  # existing SP4 fixture


@pytest.fixture(autouse=True)
def _clean_skills(pg_pool):
    with pg_pool.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM skill")
    yield
    with pg_pool.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM skill")


def _mk(**overrides):
    defaults = dict(
        name="echo",
        description="Echo tool",
        python_source="def run(**kwargs):\n    return str(kwargs)",
        config_schema={},
        config_values={},
        bound_agents=["atlas"],
        enabled=True,
    )
    defaults.update(overrides)
    return defaults


def test_create_returns_row_with_generated_id():
    sk = repo.create(**_mk())
    assert sk.id is not None
    assert sk.name == "echo"
    assert sk.bound_agents == ["atlas"]
    assert sk.enabled is True


def test_get_unknown_returns_none():
    from uuid import uuid4
    assert repo.get(uuid4()) is None


def test_get_by_id_after_create():
    created = repo.create(**_mk())
    fetched = repo.get(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.name == "echo"


def test_list_all_orders_by_name():
    repo.create(**_mk(name="zzz_last"))
    repo.create(**_mk(name="aaa_first"))
    names = [s.name for s in repo.list_all()]
    assert names == ["aaa_first", "zzz_last"]


def test_list_bound_and_enabled_filters_by_agent_and_enabled():
    repo.create(**_mk(name="a", bound_agents=["atlas"]))
    repo.create(**_mk(name="b", bound_agents=["plume"]))
    repo.create(**_mk(name="c", bound_agents=["atlas"], enabled=False))
    repo.create(**_mk(name="d", bound_agents=["atlas", "plume"]))
    atlas = [s.name for s in repo.list_bound_and_enabled("atlas")]
    assert atlas == ["a", "d"]  # not b (bound to plume only), not c (disabled)


def test_update_partial_patch():
    sk = repo.create(**_mk())
    fresh = repo.update(sk.id, {"description": "Updated"})
    assert fresh.description == "Updated"
    assert fresh.name == "echo"  # unchanged


def test_update_bound_agents_replaces_array():
    sk = repo.create(**_mk(bound_agents=["atlas"]))
    fresh = repo.update(sk.id, {"bound_agents": ["plume", "iris"]})
    assert fresh.bound_agents == ["plume", "iris"]


def test_update_empty_patch_raises_value_error():
    sk = repo.create(**_mk())
    with pytest.raises(ValueError):
        repo.update(sk.id, {})


def test_update_unknown_field_raises_value_error():
    sk = repo.create(**_mk())
    with pytest.raises(ValueError):
        repo.update(sk.id, {"nonexistent": 42})


def test_update_missing_row_raises_not_found():
    from uuid import uuid4
    with pytest.raises(SkillNotFound):
        repo.update(uuid4(), {"description": "x"})


def test_delete_returns_true_when_row_existed():
    sk = repo.create(**_mk())
    assert repo.delete(sk.id) is True
    assert repo.get(sk.id) is None


def test_delete_returns_false_when_row_missing():
    from uuid import uuid4
    assert repo.delete(uuid4()) is False


def test_create_duplicate_name_raises_conflict():
    repo.create(**_mk(name="dup"))
    with pytest.raises(SkillNameConflict):
        repo.create(**_mk(name="dup"))


def test_update_to_duplicate_name_raises_conflict():
    repo.create(**_mk(name="taken"))
    other = repo.create(**_mk(name="other"))
    with pytest.raises(SkillNameConflict):
        repo.update(other.id, {"name": "taken"})
```

- [ ] **Step 6: Run repository tests**

```bash
uv run pytest tests/skills_config/test_repository.py -v
```

Expected: 13 tests pass. Requires `twaky-pg` container running (`docker compose up -d twaky-pg`) and the T1 migration applied.

- [ ] **Step 7: Full Python gates**

```bash
uv run ruff check src/twaky/skills_config tests/skills_config \
  && uv run ruff format --check src/twaky/skills_config tests/skills_config \
  && uv run mypy src/twaky/skills_config
```

Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add src/twaky/skills_config/__init__.py \
        src/twaky/skills_config/models.py \
        src/twaky/skills_config/repository.py \
        tests/skills_config/__init__.py \
        tests/skills_config/test_repository.py
git commit -m "feat(skills-config): Skill dataclass + repository CRUD"
```

---

## Task 3: `skills_config.service` — validation

**Files:**
- Create: `src/twaky/skills_config/service.py`
- Create: `tests/skills_config/test_service.py`

**Interfaces:**
- Consumes: `Skill` dataclass (T2), stdlib `ast`, `re`, `jsonschema`.
- Produces:
  - `ValidationError(Exception)` — carries `field: str` and `message: str`.
  - `NAME_RE: re.Pattern[str]` — compiled `^[a-z][a-z0-9_]{0,63}$`.
  - `BOUND_AGENT_IDS: frozenset[str]` — `{"atlas", "chronos", "plume", "iris"}`.
  - `validate_create(body: dict) -> dict` — full validation for new skills; returns the normalized dict.
  - `validate_patch(body: dict) -> dict` — validates partial update; empty raises `ValidationError(field="_body")`.
  - Internal helpers `_validate_python_source(src)`, `_validate_json_schema(schema)`, `_validate_config_values(schema, values)`, `_validate_bound_agents(agents)`, `_validate_name(name)`, `_validate_description(desc)`.

- [ ] **Step 1: Add `jsonschema` to the runtime deps if not already present**

Check `pyproject.toml`:

```bash
grep -n jsonschema /home/mmaudet/work/twaky/pyproject.toml
```

If missing, add to `[project] dependencies`:

```toml
dependencies = [
    # ... existing ...
    "jsonschema>=4.21",
]
```

Then:

```bash
uv sync
```

- [ ] **Step 2: Write `src/twaky/skills_config/service.py`**

```python
"""Validation layer for skill create/update payloads.

Rules enforced (from spec §6.3):
- name: regex ^[a-z][a-z0-9_]{0,63}$
- description: 1-1000 chars trimmed
- python_source: 1-32000 chars trimmed + ast.parse OK + top-level def run(...)
- bound_agents: subset of {atlas, chronos, plume, iris}
- config_schema: valid JSON Schema Draft 2020-12
- config_values: validates against config_schema
- Empty patch body → ValidationError(field="_body")
"""

from __future__ import annotations

import ast
import re
from typing import Any

import jsonschema
from jsonschema.exceptions import SchemaError

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
BOUND_AGENT_IDS = frozenset({"atlas", "chronos", "plume", "iris"})


class ValidationError(Exception):
    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


def _validate_name(name: Any) -> str:
    if not isinstance(name, str):
        raise ValidationError("name", "must be a string")
    if not NAME_RE.match(name):
        raise ValidationError(
            "name",
            "must match ^[a-z][a-z0-9_]{0,63}$ (lowercase, digits, underscore; "
            "start with letter; 1-64 chars)",
        )
    return name


def _validate_description(desc: Any) -> str:
    if not isinstance(desc, str):
        raise ValidationError("description", "must be a string")
    trimmed = desc.strip()
    if not (1 <= len(trimmed) <= 1000):
        raise ValidationError("description", "must be 1-1000 characters (trimmed)")
    return trimmed


def _has_top_level_run(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == "run":
            return True
    return False


def _validate_python_source(src: Any) -> str:
    if not isinstance(src, str):
        raise ValidationError("python_source", "must be a string")
    trimmed = src.strip()
    if not (1 <= len(trimmed) <= 32000):
        raise ValidationError("python_source", "must be 1-32000 characters (trimmed)")
    try:
        tree = ast.parse(src, mode="exec")
    except SyntaxError as exc:
        raise ValidationError(
            "python_source",
            f"SyntaxError at line {exc.lineno}, col {exc.offset}: {exc.msg}",
        ) from exc
    if not _has_top_level_run(tree):
        raise ValidationError(
            "python_source",
            "module must define a top-level 'def run(...)' function",
        )
    return src


def _validate_bound_agents(agents: Any) -> list[str]:
    if not isinstance(agents, list):
        raise ValidationError("bound_agents", "must be an array")
    bad = [a for a in agents if a not in BOUND_AGENT_IDS]
    if bad:
        raise ValidationError(
            "bound_agents",
            f"unknown agent ids: {bad}. Allowed: {sorted(BOUND_AGENT_IDS)}",
        )
    return list(agents)


def _validate_json_schema(schema: Any) -> dict:
    if not isinstance(schema, dict):
        raise ValidationError("config_schema", "must be an object")
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValidationError("config_schema", f"invalid JSON Schema: {exc.message}") from exc
    return schema


def _validate_config_values(schema: dict, values: Any) -> dict:
    if not isinstance(values, dict):
        raise ValidationError("config_values", "must be an object")
    try:
        jsonschema.validate(values, schema)
    except jsonschema.ValidationError as exc:
        raise ValidationError(
            "config_values",
            f"does not match config_schema: {exc.message}",
        ) from exc
    return values


def validate_create(body: dict) -> dict:
    """Full validation for POST /skills. Returns normalized dict."""
    if not isinstance(body, dict):
        raise ValidationError("_body", "must be an object")
    normalized = {
        "name": _validate_name(body.get("name")),
        "description": _validate_description(body.get("description")),
        "python_source": _validate_python_source(body.get("python_source")),
        "bound_agents": _validate_bound_agents(body.get("bound_agents", [])),
        "enabled": bool(body.get("enabled", True)),
    }
    schema = _validate_json_schema(body.get("config_schema", {}))
    values = _validate_config_values(schema, body.get("config_values", {}))
    normalized["config_schema"] = schema
    normalized["config_values"] = values
    return normalized


def validate_patch(body: dict) -> dict:
    """Partial validation for PATCH /skills/{id}. Empty body → ValidationError."""
    if not isinstance(body, dict):
        raise ValidationError("_body", "must be an object")
    if not body:
        raise ValidationError("_body", "at least one field required")

    patch: dict[str, Any] = {}
    if "name" in body:
        patch["name"] = _validate_name(body["name"])
    if "description" in body:
        patch["description"] = _validate_description(body["description"])
    if "python_source" in body:
        patch["python_source"] = _validate_python_source(body["python_source"])
    if "bound_agents" in body:
        patch["bound_agents"] = _validate_bound_agents(body["bound_agents"])
    if "enabled" in body:
        patch["enabled"] = bool(body["enabled"])
    if "config_schema" in body or "config_values" in body:
        schema = _validate_json_schema(
            body.get("config_schema", {}) if "config_schema" in body else {}
        )
        if "config_schema" in body:
            patch["config_schema"] = schema
        if "config_values" in body:
            # Validate values against the incoming schema if present, else empty
            # schema (which accepts anything).
            patch["config_values"] = _validate_config_values(schema, body["config_values"])
    return patch


__all__ = [
    "ValidationError",
    "NAME_RE",
    "BOUND_AGENT_IDS",
    "validate_create",
    "validate_patch",
]
```

- [ ] **Step 3: Write `tests/skills_config/test_service.py`**

Pure unit tests — no DB.

```python
"""Validation matrix for skills_config.service."""

from __future__ import annotations

import pytest

from twaky.skills_config.service import (
    BOUND_AGENT_IDS,
    ValidationError,
    validate_create,
    validate_patch,
)

VALID_BODY = {
    "name": "echo",
    "description": "Echo tool",
    "python_source": "def run(**kwargs):\n    return str(kwargs)",
    "bound_agents": ["atlas"],
}


# ---- name ----

@pytest.mark.parametrize("name", ["echo", "search_wikipedia", "a", "a1", "z_9_"])
def test_valid_names(name):
    body = {**VALID_BODY, "name": name}
    assert validate_create(body)["name"] == name


@pytest.mark.parametrize(
    "name",
    ["", "Echo", "1abc", "with-hyphen", "with space", "a" * 65, "sendEmail"],
)
def test_invalid_names_rejected(name):
    body = {**VALID_BODY, "name": name}
    with pytest.raises(ValidationError) as exc:
        validate_create(body)
    assert exc.value.field == "name"


# ---- description ----

def test_description_trimmed():
    body = {**VALID_BODY, "description": "   hello   "}
    assert validate_create(body)["description"] == "hello"


def test_description_empty_after_trim_rejected():
    body = {**VALID_BODY, "description": "     "}
    with pytest.raises(ValidationError) as exc:
        validate_create(body)
    assert exc.value.field == "description"


def test_description_too_long():
    body = {**VALID_BODY, "description": "x" * 1001}
    with pytest.raises(ValidationError) as exc:
        validate_create(body)
    assert exc.value.field == "description"


# ---- python_source ----

def test_python_syntax_error_rejected():
    body = {**VALID_BODY, "python_source": "def run("}  # unterminated
    with pytest.raises(ValidationError) as exc:
        validate_create(body)
    assert exc.value.field == "python_source"
    assert "SyntaxError" in exc.value.message


def test_missing_run_function_rejected():
    body = {**VALID_BODY, "python_source": "def other():\n    return 1"}
    with pytest.raises(ValidationError) as exc:
        validate_create(body)
    assert exc.value.field == "python_source"
    assert "def run" in exc.value.message


def test_run_as_lambda_rejected():
    body = {**VALID_BODY, "python_source": "run = lambda **kw: 1"}
    with pytest.raises(ValidationError) as exc:
        validate_create(body)
    assert exc.value.field == "python_source"


def test_run_as_async_function_accepted():
    body = {**VALID_BODY, "python_source": "async def run(**kw):\n    return 1"}
    assert validate_create(body)["python_source"].startswith("async def run")


def test_python_source_too_long():
    body = {**VALID_BODY, "python_source": "def run():\n    " + ("x = 1\n    " * 4000)}
    with pytest.raises(ValidationError) as exc:
        validate_create(body)
    assert exc.value.field == "python_source"


# ---- bound_agents ----

def test_bound_agents_subset_ok():
    body = {**VALID_BODY, "bound_agents": ["atlas", "plume"]}
    assert validate_create(body)["bound_agents"] == ["atlas", "plume"]


def test_bound_agents_empty_list_ok():
    body = {**VALID_BODY, "bound_agents": []}
    assert validate_create(body)["bound_agents"] == []


def test_bound_agents_unknown_id_rejected():
    body = {**VALID_BODY, "bound_agents": ["atlas", "zeus"]}
    with pytest.raises(ValidationError) as exc:
        validate_create(body)
    assert exc.value.field == "bound_agents"


def test_bound_agents_wrong_type_rejected():
    body = {**VALID_BODY, "bound_agents": "atlas"}
    with pytest.raises(ValidationError) as exc:
        validate_create(body)
    assert exc.value.field == "bound_agents"


def test_bound_agent_ids_constant_matches_spec():
    assert BOUND_AGENT_IDS == frozenset({"atlas", "chronos", "plume", "iris"})


# ---- config_schema + config_values ----

def test_valid_json_schema_and_matching_values():
    body = {
        **VALID_BODY,
        "config_schema": {
            "type": "object",
            "properties": {"endpoint": {"type": "string"}},
            "required": ["endpoint"],
        },
        "config_values": {"endpoint": "https://x"},
    }
    result = validate_create(body)
    assert result["config_values"] == {"endpoint": "https://x"}


def test_invalid_json_schema_rejected():
    body = {**VALID_BODY, "config_schema": {"type": "not-a-real-type"}}
    with pytest.raises(ValidationError) as exc:
        validate_create(body)
    assert exc.value.field == "config_schema"


def test_config_values_not_matching_schema_rejected():
    body = {
        **VALID_BODY,
        "config_schema": {
            "type": "object",
            "properties": {"endpoint": {"type": "string"}},
            "required": ["endpoint"],
        },
        "config_values": {},  # missing "endpoint"
    }
    with pytest.raises(ValidationError) as exc:
        validate_create(body)
    assert exc.value.field == "config_values"


# ---- patch ----

def test_patch_empty_body_rejected():
    with pytest.raises(ValidationError) as exc:
        validate_patch({})
    assert exc.value.field == "_body"


def test_patch_single_field_ok():
    assert validate_patch({"description": "new"}) == {"description": "new"}


def test_patch_unknown_field_ignored_or_kept_pure():
    # Service layer accepts unknown top-level keys silently — repository
    # layer is the one that rejects unknown columns (see T2 test).
    # This isolates responsibilities: service = shape/rules, repo = column names.
    result = validate_patch({"description": "x", "extraneous": 1})
    assert result == {"description": "x"}


def test_patch_config_values_validated_against_new_schema():
    result = validate_patch({
        "config_schema": {"type": "object", "required": ["k"], "properties": {"k": {"type": "string"}}},
        "config_values": {"k": "v"},
    })
    assert result["config_values"] == {"k": "v"}
```

- [ ] **Step 4: Run service tests**

```bash
uv run pytest tests/skills_config/test_service.py -v
```

Expected: ~24 tests pass. No DB required.

- [ ] **Step 5: Gates**

```bash
uv run ruff check src/twaky/skills_config tests/skills_config \
  && uv run ruff format --check src/twaky/skills_config tests/skills_config \
  && uv run mypy src/twaky/skills_config
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/twaky/skills_config/service.py tests/skills_config/test_service.py
git commit -m "feat(skills-config): validation service (regex, ast.parse, jsonschema)"
```

---

## Task 4: `skills.executor` — subprocess isolation

**Files:**
- Create: `src/twaky/skills/__init__.py` (empty)
- Create: `src/twaky/skills/executor.py`
- Create: `tests/skills/__init__.py` (empty)
- Create: `tests/skills/test_executor.py`
- Create: `tests/integration/test_skills_executor_limits.py`

**Interfaces:**
- Consumes: stdlib `multiprocessing`, `resource`, `sys`, `pickle`.
- Produces:
  - `SkillTimeout(Exception)`, `SkillCrashed(Exception)`, `SkillError(Exception)`.
  - `run_skill(python_source: str, args: dict, config: dict, timeout_s: float = 30, memory_limit_mb: int = 256, cpu_seconds: int = 60) -> Any`.
  - `_worker(pipe, python_source, args, config, memory_limit_mb, cpu_seconds)` — private target for `multiprocessing.Process`.

- [ ] **Step 1: Write `src/twaky/skills/__init__.py`**

Empty file.

- [ ] **Step 2: Write `src/twaky/skills/executor.py`**

```python
"""Isolated subprocess executor for user-authored skills.

Each invocation forks a fresh multiprocessing.Process, applies rlimits
inside the child, execs the user source into a fresh namespace, calls
run(**args, **config), and pipes the pickled result back.

Isolation trade-offs are documented in the module docstring AND the README:
this is a SAFETY boundary (catches accidents), not a SECURITY boundary
(against a hostile owner — see spec §9.2).
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import pickle
import platform
import resource
import sys
from typing import Any

log = logging.getLogger("twaky.skills.executor")

_MB = 1024 * 1024


class SkillTimeout(Exception):
    pass


class SkillCrashed(Exception):
    pass


class SkillError(Exception):
    pass


def _set_rlimits(memory_mb: int, cpu_s: int) -> None:
    """Apply resource caps inside the child. Linux-only for RLIMIT_NPROC."""
    resource.setrlimit(resource.RLIMIT_AS, (memory_mb * _MB, memory_mb * _MB))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
    # RLIMIT_NPROC on macOS counts parent's threads — setting it to 0 kills
    # the whole test harness. Only apply on Linux.
    if platform.system() == "Linux":
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))


def _worker(
    pipe: "mp.connection.Connection",
    python_source: str,
    args: dict,
    config: dict,
    memory_mb: int,
    cpu_s: int,
) -> None:
    try:
        _set_rlimits(memory_mb, cpu_s)
    except (ValueError, OSError) as exc:
        pipe.send(("error", f"rlimit setup failed: {type(exc).__name__}: {exc}"))
        sys.exit(2)

    namespace: dict[str, Any] = {}
    try:
        exec(compile(python_source, "<skill>", "exec"), namespace)  # noqa: S102
    except MemoryError:
        pipe.send(("error", "MemoryError during module import"))
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        pipe.send(("error", f"{type(exc).__name__}: {exc}"))
        sys.exit(1)

    run_fn = namespace.get("run")
    if not callable(run_fn):
        pipe.send(("error", "module does not define a callable 'run'"))
        sys.exit(1)

    try:
        result = run_fn(**args, **config)
    except MemoryError:
        pipe.send(("error", "MemoryError during run()"))
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        pipe.send(("error", f"{type(exc).__name__}: {exc}"))
        sys.exit(1)

    try:
        pipe.send(("ok", result))
    except (pickle.PicklingError, TypeError) as exc:
        pipe.send(("error", f"PicklingError: return value not pickleable: {exc}"))
        sys.exit(1)

    sys.exit(0)


def run_skill(
    python_source: str,
    args: dict,
    config: dict,
    *,
    timeout_s: float = 30,
    memory_limit_mb: int = 256,
    cpu_seconds: int = 60,
) -> Any:
    """Fork, run, return. Raises SkillTimeout, SkillCrashed, or SkillError."""
    parent_conn, child_conn = mp.Pipe(duplex=False)

    ctx = mp.get_context("fork")
    proc = ctx.Process(
        target=_worker,
        args=(child_conn, python_source, args, config, memory_limit_mb, cpu_seconds),
        daemon=True,
    )
    proc.start()
    child_conn.close()  # parent doesn't write

    proc.join(timeout=timeout_s)

    if proc.is_alive():
        proc.terminate()
        proc.join(3)
        if proc.is_alive():
            proc.kill()
            proc.join(3)
        parent_conn.close()
        raise SkillTimeout(f"skill timed out after {timeout_s}s")

    # Read whatever the child managed to send.
    payload: tuple[str, Any] | None = None
    try:
        if parent_conn.poll(0):
            payload = parent_conn.recv()
    except EOFError:
        payload = None
    finally:
        parent_conn.close()

    if payload is None:
        raise SkillCrashed(
            f"skill exited with code {proc.exitcode} and sent no result"
        )

    tag, value = payload
    if tag == "ok":
        return value
    if tag == "error":
        raise SkillError(str(value))
    raise SkillCrashed(f"unknown payload tag: {tag!r}")


__all__ = [
    "SkillTimeout",
    "SkillCrashed",
    "SkillError",
    "run_skill",
]
```

- [ ] **Step 3: Write `tests/skills/__init__.py`**

Empty file.

- [ ] **Step 4: Write `tests/skills/test_executor.py`**

Unit tests that don't rely on hitting the real memory rlimit (that's the integration test in Step 6).

```python
"""Unit tests for skills.executor. No DB, no Postgres."""

from __future__ import annotations

import pytest

from twaky.skills.executor import (
    SkillCrashed,
    SkillError,
    SkillTimeout,
    run_skill,
)


def test_happy_path_returns_string():
    src = "def run(**kwargs):\n    return 'hello'"
    assert run_skill(src, args={}, config={}) == "hello"


def test_args_and_config_merged():
    src = "def run(query, endpoint):\n    return f'{endpoint}?q={query}'"
    result = run_skill(
        src,
        args={"query": "twake"},
        config={"endpoint": "https://x"},
    )
    assert result == "https://x?q=twake"


def test_kwargs_only_run_signature():
    src = "def run(**kwargs):\n    return kwargs"
    result = run_skill(src, args={"a": 1}, config={"b": 2})
    assert result == {"a": 1, "b": 2}


def test_syntax_error_at_exec_raises_skill_error():
    # Note: ast-level syntax errors are caught by the service layer at save
    # time. This tests the executor's own defense — a source that parsed but
    # blows up at compile is rare, but the path must still be safe.
    src = "def run():\n    return undefined_name"
    with pytest.raises(SkillError) as exc:
        run_skill(src, args={}, config={})
    assert "NameError" in str(exc.value)


def test_run_raises_exception_returns_skill_error():
    src = "def run(**kwargs):\n    raise ValueError('boom')"
    with pytest.raises(SkillError) as exc:
        run_skill(src, args={}, config={})
    assert "ValueError" in str(exc.value)
    assert "boom" in str(exc.value)


def test_wall_clock_timeout():
    src = "import time\ndef run(**kwargs):\n    time.sleep(30)"
    with pytest.raises(SkillTimeout):
        run_skill(src, args={}, config={}, timeout_s=1)


def test_module_missing_run_returns_skill_error():
    src = "x = 1"  # no run at all
    with pytest.raises(SkillError) as exc:
        run_skill(src, args={}, config={})
    assert "run" in str(exc.value).lower()


def test_non_picklable_return_raises_skill_error():
    src = (
        "import threading\n"
        "def run(**kwargs):\n"
        "    return threading.Lock()"
    )
    with pytest.raises(SkillError) as exc:
        run_skill(src, args={}, config={})
    assert "PicklingError" in str(exc.value)


def test_return_dict_survives_round_trip():
    src = "def run(**kwargs):\n    return {'a': [1, 2, 3], 'b': None}"
    result = run_skill(src, args={}, config={})
    assert result == {"a": [1, 2, 3], "b": None}


def test_crash_via_os_exit_maps_to_skill_crashed():
    src = "import os\ndef run(**kwargs):\n    os._exit(42)"
    with pytest.raises(SkillCrashed) as exc:
        run_skill(src, args={}, config={})
    assert "42" in str(exc.value)
```

- [ ] **Step 5: Run unit tests**

```bash
uv run pytest tests/skills/test_executor.py -v
```

Expected: 10 tests pass. Fork-per-test overhead → total wall time ~5-8 s.

- [ ] **Step 6: Write `tests/integration/test_skills_executor_limits.py`**

Real-rlimit test — verifies memory limit actually kicks in.

```python
"""Real-rlimit integration test for the skills executor.

Marked as `integration` so it can be skipped on CI hosts where the kernel
disallows large virtual allocations. Runs a small allocator that MUST hit
RLIMIT_AS at 64 MB (default 256 MB is too big to reliably OOM in a test).
"""

from __future__ import annotations

import platform
import pytest

from twaky.skills.executor import SkillError, SkillTimeout, run_skill

pytestmark = pytest.mark.integration


@pytest.mark.skipif(platform.system() != "Linux", reason="RLIMIT_AS reliable on Linux only")
def test_memory_limit_kills_allocator():
    # Try to allocate ~1 GB inside a 64 MB cap. Python raises MemoryError,
    # which the worker catches and reports as SkillError.
    src = (
        "def run(**kwargs):\n"
        "    x = bytearray(1024 * 1024 * 1024)\n"
        "    return len(x)"
    )
    with pytest.raises((SkillError, SkillTimeout)):
        run_skill(src, args={}, config={}, memory_limit_mb=64, timeout_s=5)


@pytest.mark.skipif(platform.system() != "Linux", reason="RLIMIT_NPROC not applied on macOS")
def test_fork_denied_by_nproc_limit():
    src = (
        "import subprocess\n"
        "def run(**kwargs):\n"
        "    return subprocess.check_output(['/bin/echo', 'hi'])"
    )
    with pytest.raises(SkillError) as exc:
        run_skill(src, args={}, config={}, timeout_s=5)
    # subprocess.Popen → fork() → EAGAIN when NPROC=0
    assert "BlockingIOError" in str(exc.value) or "Resource" in str(exc.value) or "OSError" in str(exc.value)
```

- [ ] **Step 7: Register the `integration` marker if not already**

Check `pyproject.toml`:

```bash
grep -n "markers" /home/mmaudet/work/twaky/pyproject.toml
```

Ensure `integration` is in `[tool.pytest.ini_options] markers`:

```toml
[tool.pytest.ini_options]
markers = [
    "integration: requires real Postgres/rlimits",
]
```

- [ ] **Step 8: Run the integration test on Linux**

```bash
uv run pytest tests/integration/test_skills_executor_limits.py -v
```

Expected on Linux: 2 tests pass. On macOS: 2 skipped.

- [ ] **Step 9: Full gates**

```bash
uv run ruff check src/twaky/skills tests/skills \
  && uv run ruff format --check src/twaky/skills tests/skills \
  && uv run mypy src/twaky/skills
```

Expected: all green.

- [ ] **Step 10: Commit**

```bash
git add src/twaky/skills/__init__.py \
        src/twaky/skills/executor.py \
        tests/skills/__init__.py \
        tests/skills/test_executor.py \
        tests/integration/test_skills_executor_limits.py
git commit -m "feat(skills): subprocess executor with rlimits + wall-clock timeout"
```

---

## Task 5: `skills.registry` — cache + invalidate_all

**Files:**
- Create: `src/twaky/skills/registry.py`
- Create: `tests/skills/test_registry.py`

**Interfaces:**
- Consumes: `Skill` dataclass and `list_bound_and_enabled(agent_id)` from T2.
- Produces:
  - `load_skills_for_agent(agent_id: str) -> list[Skill]` — cache-first, populates on miss.
  - `invalidate_all() -> None` — clears cache (called at boot and on any NOTIFY).
  - `_repository_get_bound(agent_id)` — indirection kept for test monkeypatching (mirrors SP4).

- [ ] **Step 1: Write `src/twaky/skills/registry.py`**

```python
"""Thread-safe per-agent skill cache.

Populated lazily on first call per agent. Invalidated coarsely on any
skill_changed NOTIFY (spec §5.1) — 4 tiny DB queries on next agent
invocation is cheaper than tracking per-agent dependencies.
"""

from __future__ import annotations

import threading

from twaky.skills_config import repository
from twaky.skills_config.models import Skill

_cache: dict[str, list[Skill]] = {}
_lock = threading.Lock()


def _repository_get_bound(agent_id: str) -> list[Skill]:
    """Indirection kept for test monkeypatching."""
    return repository.list_bound_and_enabled(agent_id)


def load_skills_for_agent(agent_id: str) -> list[Skill]:
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


__all__ = ["load_skills_for_agent", "invalidate_all"]
```

- [ ] **Step 2: Write `tests/skills/test_registry.py`**

Uses `monkeypatch` on `_repository_get_bound` — no DB.

```python
"""Registry cache tests — no DB, monkeypatched repository seam."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

from twaky.skills import registry
from twaky.skills_config.models import Skill


def _mk_skill(name: str, agent: str) -> Skill:
    return Skill(
        id=uuid4(),
        name=name,
        description="d",
        python_source="def run(**kw): return 1",
        config_schema={},
        config_values={},
        bound_agents=[agent],
        enabled=True,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    registry.invalidate_all()
    yield
    registry.invalidate_all()


def test_cache_miss_populates_and_returns(monkeypatch):
    calls: list[str] = []

    def fake(agent_id):
        calls.append(agent_id)
        return [_mk_skill("s1", "atlas")]

    monkeypatch.setattr(registry, "_repository_get_bound", fake)
    result = registry.load_skills_for_agent("atlas")
    assert len(result) == 1
    assert calls == ["atlas"]


def test_cache_hit_avoids_second_repo_call(monkeypatch):
    calls: list[str] = []

    def fake(agent_id):
        calls.append(agent_id)
        return [_mk_skill("s1", "atlas")]

    monkeypatch.setattr(registry, "_repository_get_bound", fake)
    registry.load_skills_for_agent("atlas")
    registry.load_skills_for_agent("atlas")
    assert calls == ["atlas"]  # only one call


def test_invalidate_all_forces_refresh(monkeypatch):
    calls: list[str] = []

    def fake(agent_id):
        calls.append(agent_id)
        return []

    monkeypatch.setattr(registry, "_repository_get_bound", fake)
    registry.load_skills_for_agent("atlas")
    registry.invalidate_all()
    registry.load_skills_for_agent("atlas")
    assert calls == ["atlas", "atlas"]


def test_per_agent_isolation(monkeypatch):
    calls: list[str] = []

    def fake(agent_id):
        calls.append(agent_id)
        return [_mk_skill("s", agent_id)]

    monkeypatch.setattr(registry, "_repository_get_bound", fake)
    registry.load_skills_for_agent("atlas")
    registry.load_skills_for_agent("plume")
    registry.load_skills_for_agent("atlas")  # still cached
    registry.load_skills_for_agent("plume")  # still cached
    assert calls == ["atlas", "plume"]  # each fetched exactly once
```

- [ ] **Step 3: Run registry tests**

```bash
uv run pytest tests/skills/test_registry.py -v
```

Expected: 4 tests pass.

- [ ] **Step 4: Gates**

```bash
uv run ruff check src/twaky/skills tests/skills \
  && uv run mypy src/twaky/skills
```

- [ ] **Step 5: Commit**

```bash
git add src/twaky/skills/registry.py tests/skills/test_registry.py
git commit -m "feat(skills): per-agent registry cache + invalidate_all"
```

---

## Task 6: `skills.tool_adapter` — Skill → StructuredTool

**Files:**
- Create: `src/twaky/skills/tool_adapter.py`
- Create: `tests/skills/test_tool_adapter.py`

**Interfaces:**
- Consumes: `Skill` from T2, `run_skill` + errors from T4, `langchain_core.tools.StructuredTool`.
- Produces:
  - `skill_to_tool(skill: Skill) -> StructuredTool` — args_schema is `**kwargs` (LLM passes any JSON dict).
  - Error mapping: `SkillTimeout` → `"skill 'X' timed out after 30s"`, `SkillCrashed` → `"skill 'X' crashed: <msg>"`, `SkillError` → `"skill 'X' raised: <msg>"`.
  - Non-string results serialized via `json.dumps(..., default=str)`.

- [ ] **Step 1: Write `src/twaky/skills/tool_adapter.py`**

```python
"""Wrap a Skill row into a LangChain StructuredTool.

The LLM sees each skill as a callable whose name is skill.name and whose
description is skill.description. Errors are converted to human-readable
strings — the LLM gets to decide whether to retry, apologize, or abandon.
"""

from __future__ import annotations

import json

from langchain_core.tools import StructuredTool

from twaky.skills.executor import (
    SkillCrashed,
    SkillError,
    SkillTimeout,
    run_skill,
)
from twaky.skills_config.models import Skill


def skill_to_tool(skill: Skill) -> StructuredTool:
    def _invoke(**kwargs) -> str:
        try:
            result = run_skill(
                python_source=skill.python_source,
                args=kwargs,
                config=skill.config_values,
                timeout_s=30,
                memory_limit_mb=256,
                cpu_seconds=60,
            )
        except SkillTimeout:
            return f"skill '{skill.name}' timed out after 30s"
        except SkillCrashed as exc:
            return f"skill '{skill.name}' crashed: {exc}"
        except SkillError as exc:
            return f"skill '{skill.name}' raised: {exc}"

        if isinstance(result, str):
            return result
        return json.dumps(result, default=str)

    return StructuredTool.from_function(
        name=skill.name,
        description=skill.description,
        func=_invoke,
    )


__all__ = ["skill_to_tool"]
```

- [ ] **Step 2: Write `tests/skills/test_tool_adapter.py`**

```python
"""Tool adapter tests. Uses the real executor — same in-process fork tests."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

from twaky.skills import tool_adapter
from twaky.skills.executor import SkillCrashed, SkillError, SkillTimeout
from twaky.skills_config.models import Skill


def _mk(**overrides) -> Skill:
    defaults = dict(
        id=uuid4(),
        name="echo",
        description="Echo tool",
        python_source="def run(**kwargs):\n    return str(kwargs)",
        config_schema={},
        config_values={},
        bound_agents=["atlas"],
        enabled=True,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    defaults.update(overrides)
    return Skill(**defaults)


def test_skill_to_tool_returns_structured_tool_with_correct_metadata():
    tool = tool_adapter.skill_to_tool(_mk())
    assert tool.name == "echo"
    assert tool.description == "Echo tool"


def test_tool_invoke_returns_string_result():
    tool = tool_adapter.skill_to_tool(_mk(
        python_source="def run(**kwargs): return 'ok:' + kwargs.get('x', '')"
    ))
    assert tool.invoke({"x": "hi"}) == "ok:hi"


def test_tool_invoke_serializes_dict_result_as_json():
    tool = tool_adapter.skill_to_tool(_mk(
        python_source="def run(**kwargs): return {'a': 1, 'b': [2, 3]}"
    ))
    result = tool.invoke({})
    assert result == '{"a": 1, "b": [2, 3]}'


def test_timeout_maps_to_readable_string():
    tool = tool_adapter.skill_to_tool(_mk(name="slow"))
    with patch("twaky.skills.tool_adapter.run_skill", side_effect=SkillTimeout("boom")):
        assert tool.invoke({}) == "skill 'slow' timed out after 30s"


def test_crash_maps_to_readable_string():
    tool = tool_adapter.skill_to_tool(_mk(name="bad"))
    with patch("twaky.skills.tool_adapter.run_skill", side_effect=SkillCrashed("exit 1")):
        assert tool.invoke({}) == "skill 'bad' crashed: exit 1"


def test_error_maps_to_readable_string():
    tool = tool_adapter.skill_to_tool(_mk(name="raiser"))
    with patch(
        "twaky.skills.tool_adapter.run_skill",
        side_effect=SkillError("ValueError: nope"),
    ):
        assert tool.invoke({}) == "skill 'raiser' raised: ValueError: nope"


def test_config_values_forwarded_as_kwargs():
    tool = tool_adapter.skill_to_tool(_mk(
        python_source="def run(query, endpoint): return f'{endpoint}?q={query}'",
        config_values={"endpoint": "https://x"},
    ))
    assert tool.invoke({"query": "twake"}) == "https://x?q=twake"
```

- [ ] **Step 3: Run adapter tests**

```bash
uv run pytest tests/skills/test_tool_adapter.py -v
```

Expected: 7 tests pass. Overhead ~5 s (2 real forks for the happy-path + dict tests; 4 patched).

- [ ] **Step 4: Gates**

```bash
uv run ruff check src/twaky/skills tests/skills \
  && uv run mypy src/twaky/skills
```

- [ ] **Step 5: Commit**

```bash
git add src/twaky/skills/tool_adapter.py tests/skills/test_tool_adapter.py
git commit -m "feat(skills): StructuredTool adapter with error-string mapping"
```

---

## Task 7: `skills.config_listener` — LISTEN skill_changed

**Files:**
- Create: `src/twaky/skills/config_listener.py`
- Create: `tests/integration/test_skills_config_listener.py`

**Interfaces:**
- Consumes: `twaky.daemon.notify.listen` (existing), `twaky.skills.registry.invalidate_all` (T5), `twaky.config.settings`.
- Produces:
  - `async def run(stop_event: asyncio.Event) -> None` — long-running task, cancellable via `stop_event.set()` or `task.cancel()`.

- [ ] **Step 1: Write `src/twaky/skills/config_listener.py`**

```python
"""LISTEN for `skill_changed` NOTIFYs and invalidate the registry cache.

The daemon spawns this as an asyncio task alongside the mission scheduler
and SP4's agent config listener. Coarse invalidation strategy per spec §5.1:
any NOTIFY clears all per-agent caches.
"""

from __future__ import annotations

import asyncio
import logging

from twaky.config import settings
from twaky.daemon.notify import listen
from twaky.skills import registry

log = logging.getLogger("twaky.skills.config_listener")


async def run(stop_event: asyncio.Event) -> None:
    log.info("skill config listener starting")
    try:
        async for ch, payload in listen(["skill_changed"], settings.pg_dsn):
            if stop_event.is_set():
                return
            if ch == "skill_changed":
                log.info(
                    "skill changed (payload=%s), invalidating registry cache",
                    payload,
                )
                registry.invalidate_all()
    except asyncio.CancelledError:
        log.info("skill config listener cancelled")
        raise
    except Exception:
        log.exception("skill config listener crashed")
        raise


__all__ = ["run"]
```

- [ ] **Step 2: Write `tests/integration/test_skills_config_listener.py`**

Real Postgres + real NOTIFY. Cache pre-warmed, then a real DB write must clear it within 1 s.

```python
"""Integration test: real skill row write fires NOTIFY → registry.invalidate_all()."""

from __future__ import annotations

import asyncio

import pytest

from twaky.skills import config_listener, registry
from twaky.skills_config import repository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _clean_skills(pg_pool):
    with pg_pool.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM skill")
    yield
    with pg_pool.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM skill")


async def test_notify_invalidates_registry_cache_within_1s(monkeypatch):
    # Warm the cache so we can detect its clearing.
    registry.invalidate_all()
    registry._cache["atlas"] = []  # type: ignore[attr-defined]

    stop = asyncio.Event()
    task = asyncio.create_task(config_listener.run(stop))
    try:
        # Give the listener a moment to LISTEN.
        await asyncio.sleep(0.5)

        # Fire the NOTIFY via a real insert.
        repository.create(
            name="notify_probe",
            description="d",
            python_source="def run(**kw): return 1",
            config_schema={},
            config_values={},
            bound_agents=["atlas"],
            enabled=True,
        )

        # Poll until the cache clears (up to 1 s).
        for _ in range(10):
            await asyncio.sleep(0.1)
            if "atlas" not in registry._cache:  # type: ignore[attr-defined]
                break
        assert "atlas" not in registry._cache  # type: ignore[attr-defined]
    finally:
        stop.set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
```

- [ ] **Step 3: Run integration test**

```bash
uv run pytest tests/integration/test_skills_config_listener.py -v
```

Expected: 1 test passes. Requires `twaky-pg` up + migration applied.

- [ ] **Step 4: Gates**

```bash
uv run ruff check src/twaky/skills tests/integration \
  && uv run mypy src/twaky/skills
```

- [ ] **Step 5: Commit**

```bash
git add src/twaky/skills/config_listener.py tests/integration/test_skills_config_listener.py
git commit -m "feat(skills): NOTIFY listener with real-Postgres integration test"
```

---

## Task 8: Wire the listener + boot-time invalidate into `atlas_daemon`

**Files:**
- Modify: `src/twaky/daemon/atlas_daemon.py`
- Modify: (existing) `tests/daemon/test_atlas_daemon.py` — smoke assertion the task is created

**Interfaces:**
- Consumes: `twaky.skills.config_listener.run` (T7), `twaky.skills.registry.invalidate_all` (T5).
- Produces: no new public API. Daemon now boots with a warm-empty skill cache and continuously invalidates on skill_changed NOTIFYs.

- [ ] **Step 1: Read the current daemon `_main_loop` shape**

```bash
grep -n "config_listener\|config_task\|asyncio.create_task" src/twaky/daemon/atlas_daemon.py
```

Note the existing SP4 pattern (line ~364):

```python
config_task = asyncio.create_task(config_listener.run(stop))
```

You will add a sibling line for the skill listener and a boot-time cache flush.

- [ ] **Step 2: Add the import**

At the top of `src/twaky/daemon/atlas_daemon.py`, alongside the existing SP4 import:

```python
from twaky.agents import config_listener, registry as agents_registry
from twaky.skills import config_listener as skills_config_listener, registry as skills_registry
```

(Rename the existing `registry` import to `agents_registry` if it collides. Do a full-file grep for `registry.` calls first and rename them all in one pass.)

- [ ] **Step 3: Boot-time cache flush + task creation in `_main_loop`**

Locate the existing block:

```python
    config_task = asyncio.create_task(config_listener.run(stop))
```

Immediately before it, add the boot-time flush (defensive — cold-start cache is already empty, but keeps behavior identical to SP4):

```python
    # Boot: invalidate any stale in-process skill cache (belt-and-suspenders
    # for hot-reload dev workflows; cold starts get an empty cache anyway).
    skills_registry.invalidate_all()
```

Immediately after the SP4 config_task line, add:

```python
    skills_config_task = asyncio.create_task(skills_config_listener.run(stop))
```

- [ ] **Step 4: Cancel on shutdown**

Locate the shutdown block that cancels `config_task`. Add:

```python
    skills_config_task.cancel()
    try:
        await skills_config_task
    except asyncio.CancelledError:
        pass
```

Follow the exact pattern already used for `config_task`.

- [ ] **Step 5: Write a smoke test**

Append to `tests/daemon/test_atlas_daemon.py` (or create the file if it doesn't yet exist):

```python
"""Boot-time smoke test — skills listener is spawned alongside agents listener."""

import ast
from pathlib import Path


def test_skills_config_listener_is_spawned_in_main_loop():
    src = Path("src/twaky/daemon/atlas_daemon.py").read_text()
    # Both listener tasks should be created in _main_loop.
    assert "config_listener.run(stop)" in src, "SP4 agents listener missing"
    assert "skills_config_listener.run(stop)" in src, "SP5 skills listener missing"


def test_skills_registry_invalidate_at_boot():
    src = Path("src/twaky/daemon/atlas_daemon.py").read_text()
    assert "skills_registry.invalidate_all()" in src
```

(Static assertions — no daemon boot in-process. The real end-to-end wiring is covered by the integration test in T7.)

- [ ] **Step 6: Run the smoke test**

```bash
uv run pytest tests/daemon/test_atlas_daemon.py -v -k skills
```

Expected: 2 tests pass.

- [ ] **Step 7: Gates**

```bash
uv run ruff check src/twaky/daemon \
  && uv run mypy src/twaky/daemon
```

- [ ] **Step 8: Commit**

```bash
git add src/twaky/daemon/atlas_daemon.py tests/daemon/test_atlas_daemon.py
git commit -m "feat(skills): wire config_listener + boot invalidate into atlas_daemon"
```

---

## Task 9: Refactor 4 agents — append skills to TOOLS with collision guard

**Files:**
- Modify: `src/twaky/agents/atlas/agent.py`
- Modify: `src/twaky/agents/chronos/agent.py`
- Modify: `src/twaky/agents/plume/agent.py`
- Modify: `src/twaky/agents/iris/agent.py`
- Modify: `tests/agents/test_atlas_agent.py`
- Modify: `tests/agents/test_chronos_agent.py`
- Modify: `tests/agents/test_plume_agent.py`
- Modify: `tests/agents/test_iris_agent.py`

**Interfaces:**
- Consumes: `twaky.skills.registry.load_skills_for_agent` (T5), `twaky.skills.tool_adapter.skill_to_tool` (T6).
- Produces: no new public API. Each agent node now includes bound skills alongside its hardcoded TOOLS.

- [ ] **Step 1: Read one agent's node function to understand current shape**

```bash
grep -n "TOOLS\|bind_tools\|def.*_node" src/twaky/agents/atlas/agent.py | head
```

Existing shape from SP4:

```python
def _atlas_node(state):
    cfg = load_agent_config("atlas")
    llm = _make_llm(cfg).bind_tools(TOOLS)
    ...
```

- [ ] **Step 2: Create a small helper module for the collision-guarded merge**

Create `src/twaky/skills/agent_tools.py` (new — belongs in `skills/` because the logic is skill-specific):

```python
"""Merge hardcoded agent TOOLS with owner-authored skills.

Skills whose name collides with a built-in tool are DROPPED at bind time
with a warning log. `finish_mission`, `delegate_to_*`, and other hardcoded
tools stay unshadowable.
"""

from __future__ import annotations

import logging
from typing import Any

from twaky.skills.registry import load_skills_for_agent
from twaky.skills.tool_adapter import skill_to_tool

log = logging.getLogger("twaky.skills.agent_tools")


def merged_tools_for(agent_id: str, builtins: list[Any]) -> list[Any]:
    """Return `builtins` + wrapped skills, minus any skill whose name shadows a builtin."""
    builtin_names = {getattr(t, "name", None) for t in builtins}
    skills = load_skills_for_agent(agent_id)
    safe = [s for s in skills if s.name not in builtin_names]
    dropped = len(skills) - len(safe)
    if dropped:
        log.warning(
            "agent=%s dropped %d skill(s) colliding with built-in tool names",
            agent_id,
            dropped,
        )
    return list(builtins) + [skill_to_tool(s) for s in safe]


__all__ = ["merged_tools_for"]
```

- [ ] **Step 3: Write unit test for the merge helper**

Create `tests/skills/test_agent_tools.py`:

```python
"""Collision-guard tests for skills.agent_tools.merged_tools_for."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from twaky.skills import agent_tools
from twaky.skills_config.models import Skill


def _mk_skill(name: str) -> Skill:
    return Skill(
        id=uuid4(),
        name=name,
        description="d",
        python_source="def run(**kw): return 1",
        config_schema={},
        config_values={},
        bound_agents=["atlas"],
        enabled=True,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )


def _fake_builtin(name: str):
    # StructuredTool-like shape — just needs a `.name` attribute.
    return SimpleNamespace(name=name)


def test_merged_appends_skills_after_builtins():
    builtins = [_fake_builtin("finish_mission")]
    with patch.object(agent_tools, "load_skills_for_agent", return_value=[_mk_skill("echo")]):
        merged = agent_tools.merged_tools_for("atlas", builtins)
    assert len(merged) == 2
    assert merged[0].name == "finish_mission"
    assert merged[1].name == "echo"


def test_colliding_skill_is_dropped():
    builtins = [_fake_builtin("finish_mission")]
    with patch.object(
        agent_tools, "load_skills_for_agent",
        return_value=[_mk_skill("finish_mission"), _mk_skill("safe")],
    ):
        merged = agent_tools.merged_tools_for("atlas", builtins)
    names = [t.name for t in merged]
    assert names == ["finish_mission", "safe"]


def test_multiple_collisions_dropped_and_warned(caplog):
    builtins = [_fake_builtin("finish_mission"), _fake_builtin("delegate_to_plume")]
    with patch.object(
        agent_tools, "load_skills_for_agent",
        return_value=[
            _mk_skill("finish_mission"),
            _mk_skill("delegate_to_plume"),
            _mk_skill("ok"),
        ],
    ):
        with caplog.at_level("WARNING", logger="twaky.skills.agent_tools"):
            merged = agent_tools.merged_tools_for("atlas", builtins)
    assert [t.name for t in merged] == ["finish_mission", "delegate_to_plume", "ok"]
    assert "dropped 2 skill(s)" in caplog.text


def test_no_skills_returns_builtins_unchanged():
    builtins = [_fake_builtin("finish_mission")]
    with patch.object(agent_tools, "load_skills_for_agent", return_value=[]):
        merged = agent_tools.merged_tools_for("atlas", builtins)
    assert [t.name for t in merged] == ["finish_mission"]
```

- [ ] **Step 4: Run agent_tools tests**

```bash
uv run pytest tests/skills/test_agent_tools.py -v
```

Expected: 4 tests pass.

- [ ] **Step 5: Modify all 4 agent modules to use `merged_tools_for`**

For each of `src/twaky/agents/{atlas,chronos,plume,iris}/agent.py`, locate the `.bind_tools(TOOLS)` call inside the node function and replace with:

```python
from twaky.skills.agent_tools import merged_tools_for  # top of file

def _<agent>_node(state):
    cfg = load_agent_config("<agent_id>")
    tools = merged_tools_for("<agent_id>", TOOLS)
    llm = _make_llm(cfg).bind_tools(tools)
    ...
```

Do this for all four agents, using their respective agent_id string (`atlas`, `chronos`, `plume`, `iris`).

**Atlas special case**: the routing logic in `_route` inspects the last message for tool_calls. That logic is agnostic to which tool was called — skill tool_calls behave identically. No `_route` changes needed. (Confirm by re-reading `atlas/agent.py`'s `_route` before committing.)

- [ ] **Step 6: Update existing agent unit tests with a stub fixture**

The existing agent tests (`tests/agents/test_atlas_agent.py` etc.) call the node function directly with a synthetic state. They now transitively call `load_skills_for_agent` which will try to hit Postgres. Add a stub.

For each of the 4 test files, add near the top:

```python
import pytest
from twaky.skills import registry as skills_registry


@pytest.fixture(autouse=True)
def _stub_skills_for(monkeypatch):
    """Prevent test agents from touching real Postgres for skill loading."""
    monkeypatch.setattr(
        skills_registry, "_repository_get_bound", lambda agent_id: []
    )
    skills_registry.invalidate_all()
    yield
    skills_registry.invalidate_all()
```

- [ ] **Step 7: Run all agent tests**

```bash
uv run pytest tests/agents/ -v
```

Expected: all pre-existing agent tests still green (SP4 baseline preserved).

- [ ] **Step 8: Full-repo Python gates**

```bash
uv run ruff check . \
  && uv run ruff format --check . \
  && uv run mypy src/ \
  && uv run pytest -q -m "not integration"
```

Expected: all green.

- [ ] **Step 9: Commit**

```bash
git add src/twaky/skills/agent_tools.py \
        tests/skills/test_agent_tools.py \
        src/twaky/agents/atlas/agent.py \
        src/twaky/agents/chronos/agent.py \
        src/twaky/agents/plume/agent.py \
        src/twaky/agents/iris/agent.py \
        tests/agents/test_atlas_agent.py \
        tests/agents/test_chronos_agent.py \
        tests/agents/test_plume_agent.py \
        tests/agents/test_iris_agent.py
git commit -m "feat(agents): append bound skills to TOOLS with collision guard"
```

---

## Task 10: API — schemas + GET endpoints

**Files:**
- Create: `src/twaky/api/schemas/skills.py`
- Create: `src/twaky/api/routers/skills.py` (partial — GET list + GET one only for this task)
- Modify: `src/twaky/api/main.py` — include router
- Create: `tests/api/routers/test_skills.py` (partial — GET tests only)

**Interfaces:**
- Consumes: `require_owner` dep, `error_response` helper (existing SP4), `repository.list_all`, `repository.get` (T2).
- Produces:
  - `Skill`, `SkillSummary` pydantic models (full and list-view shapes).
  - `SkillCreate`, `SkillUpdate`, `SkillTestRequest`, `SkillTestResponse` (defined in this task; used in T11-T12).
  - Router mounted at `/skills` with `GET /skills` and `GET /skills/{id}`.
  - New error code `skill_not_found` (404).

- [ ] **Step 1: Write `src/twaky/api/schemas/skills.py`**

```python
"""Pydantic models for the /skills surface."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
_AGENT_IDS = Literal["atlas", "chronos", "plume", "iris"]


class Skill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    name: str = Field(pattern=_NAME_PATTERN)
    description: str = Field(min_length=1, max_length=1000)
    python_source: str = Field(min_length=1, max_length=32000)
    config_schema: dict[str, Any]
    config_values: dict[str, Any]
    bound_agents: list[_AGENT_IDS]
    enabled: bool
    created_at: datetime
    updated_at: datetime


class SkillSummary(BaseModel):
    """Shorter payload for the list endpoint. Omits code + config."""

    model_config = ConfigDict(extra="forbid")
    id: UUID
    name: str
    description: str
    bound_agents: list[_AGENT_IDS]
    enabled: bool
    created_at: datetime
    updated_at: datetime


class SkillCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=_NAME_PATTERN)
    description: str = Field(min_length=1, max_length=1000)
    python_source: str = Field(min_length=1, max_length=32000)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    config_values: dict[str, Any] = Field(default_factory=dict)
    bound_agents: list[_AGENT_IDS] = Field(default_factory=list)
    enabled: bool = True


class SkillUpdate(BaseModel):
    """Partial update. All fields optional; empty body → 422 (enforced in router)."""

    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, pattern=_NAME_PATTERN)
    description: str | None = Field(default=None, min_length=1, max_length=1000)
    python_source: str | None = Field(default=None, min_length=1, max_length=32000)
    config_schema: dict[str, Any] | None = None
    config_values: dict[str, Any] | None = None
    bound_agents: list[_AGENT_IDS] | None = None
    enabled: bool | None = None


class SkillTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    args: dict[str, Any] = Field(default_factory=dict)


class SkillTestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["ok", "timeout", "crashed", "error"]
    result: Any = None
    message: str | None = None


__all__ = [
    "Skill",
    "SkillSummary",
    "SkillCreate",
    "SkillUpdate",
    "SkillTestRequest",
    "SkillTestResponse",
]
```

- [ ] **Step 2: Write `src/twaky/api/routers/skills.py` (GET only)**

```python
"""Skills configuration routes.

This file is grown across T10 (GET), T11 (POST/PATCH/DELETE), T12 (test).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from twaky.api.deps import require_owner
from twaky.api.errors import error_response
from twaky.api.schemas.skills import Skill, SkillSummary
from twaky.skills_config import repository

router = APIRouter(prefix="/skills", tags=["skills"])


def _to_summary(sk) -> SkillSummary:
    return SkillSummary(
        id=sk.id,
        name=sk.name,
        description=sk.description,
        bound_agents=sk.bound_agents,
        enabled=sk.enabled,
        created_at=sk.created_at,
        updated_at=sk.updated_at,
    )


def _to_full(sk) -> Skill:
    return Skill(
        id=sk.id,
        name=sk.name,
        description=sk.description,
        python_source=sk.python_source,
        config_schema=sk.config_schema,
        config_values=sk.config_values,
        bound_agents=sk.bound_agents,
        enabled=sk.enabled,
        created_at=sk.created_at,
        updated_at=sk.updated_at,
    )


@router.get("", response_model=list[SkillSummary])
def list_skills(_email: str = Depends(require_owner)) -> list[SkillSummary]:
    return [_to_summary(s) for s in repository.list_all()]


@router.get("/{skill_id}", response_model=Skill)
def get_skill(skill_id: UUID, _email: str = Depends(require_owner)):
    sk = repository.get(skill_id)
    if sk is None:
        return error_response(
            code="skill_not_found",
            message=f"skill {skill_id} not found",
            status_code=404,
        )
    return _to_full(sk)


__all__ = ["router"]
```

- [ ] **Step 3: Wire the router in `src/twaky/api/main.py`**

Locate the `app.include_router(agents.router)` line and add immediately after:

```python
from twaky.api.routers import skills  # top-of-file, alongside other router imports

# ... after agents router:
app.include_router(skills.router)
```

- [ ] **Step 4: Write `tests/api/routers/test_skills.py` (GET tests only)**

Follow the pattern of `tests/api/routers/test_agents.py` — TestClient + session cookie fixture + real Postgres via `pg_pool` fixture.

```python
"""API tests for GET /skills and GET /skills/{id}."""

from __future__ import annotations

from uuid import uuid4

import pytest

from twaky.skills_config import repository

pytestmark = pytest.mark.usefixtures("pg_pool")


@pytest.fixture(autouse=True)
def _clean_skills(pg_pool):
    with pg_pool.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM skill")
    yield
    with pg_pool.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM skill")


def _seed(name="echo") -> "repository.Skill":
    return repository.create(
        name=name,
        description="Echo tool",
        python_source="def run(**kw): return 1",
        config_schema={},
        config_values={},
        bound_agents=["atlas"],
        enabled=True,
    )


def test_list_skills_401_without_session(anon_client):
    resp = anon_client.get("/skills")
    assert resp.status_code == 401


def test_list_skills_empty(owner_client):
    resp = owner_client.get("/skills")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_skills_returns_summaries(owner_client):
    _seed(name="a")
    _seed(name="b")
    resp = owner_client.get("/skills")
    assert resp.status_code == 200
    body = resp.json()
    assert [s["name"] for s in body] == ["a", "b"]
    # Summary omits python_source + config_*
    assert "python_source" not in body[0]
    assert "config_schema" not in body[0]


def test_get_skill_returns_full_shape(owner_client):
    sk = _seed()
    resp = owner_client.get(f"/skills/{sk.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "echo"
    assert body["python_source"] == "def run(**kw): return 1"
    assert body["config_schema"] == {}
    assert body["bound_agents"] == ["atlas"]


def test_get_skill_404_for_unknown_id(owner_client):
    resp = owner_client.get(f"/skills/{uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "skill_not_found"


def test_get_skill_422_for_malformed_uuid(owner_client):
    resp = owner_client.get("/skills/not-a-uuid")
    # FastAPI path validation → 422.
    assert resp.status_code == 422
```

`owner_client` and `anon_client` are existing SP4 fixtures in `tests/conftest.py` (or `tests/api/conftest.py`) — reuse verbatim.

- [ ] **Step 5: Run the tests**

```bash
uv run pytest tests/api/routers/test_skills.py -v
```

Expected: 6 tests pass.

- [ ] **Step 6: Gates**

```bash
uv run ruff check src/twaky/api tests/api \
  && uv run mypy src/twaky/api
```

- [ ] **Step 7: Commit**

```bash
git add src/twaky/api/schemas/skills.py \
        src/twaky/api/routers/skills.py \
        src/twaky/api/main.py \
        tests/api/routers/test_skills.py
git commit -m "feat(api): GET /skills + GET /skills/{id}"
```

---

## Task 11: API — POST, PATCH, DELETE + full 422 matrix

**Files:**
- Modify: `src/twaky/api/routers/skills.py` — add 3 endpoints
- Modify: `tests/api/routers/test_skills.py` — add ~15 test cases

**Interfaces:**
- Consumes: `service.validate_create`, `service.validate_patch`, `service.ValidationError` (T3), `repository.create`, `repository.update`, `repository.delete`, `SkillNameConflict`, `SkillNotFound` (T2).
- Produces:
  - `POST /skills` → 201 with the fresh Skill row, 422 on validation, 422 on unique-name conflict.
  - `PATCH /skills/{id}` → 200 with fresh row, 404 on unknown, 422 on invalid patch, 422 on name conflict.
  - `DELETE /skills/{id}` → 204 on success, 404 on unknown.

- [ ] **Step 1: Extend `src/twaky/api/routers/skills.py`**

Append below the existing GET endpoints:

```python
from fastapi import Response, status

from twaky.api.schemas.skills import SkillCreate, SkillUpdate
from twaky.skills_config import service
from twaky.skills_config.repository import SkillNameConflict, SkillNotFound
from twaky.skills_config.service import ValidationError


@router.post("", response_model=Skill, status_code=status.HTTP_201_CREATED)
def create_skill(body: SkillCreate, _email: str = Depends(require_owner)):
    try:
        norm = service.validate_create(body.model_dump())
    except ValidationError as exc:
        return error_response(
            code="validation_failed",
            message=exc.message,
            status_code=422,
            details={"field": exc.field},
        )
    try:
        sk = repository.create(**norm)
    except SkillNameConflict:
        return error_response(
            code="validation_failed",
            message=f"a skill named {norm['name']!r} already exists",
            status_code=422,
            details={"field": "name"},
        )
    return _to_full(sk)


@router.patch("/{skill_id}", response_model=Skill)
def patch_skill(
    skill_id: UUID,
    body: SkillUpdate,
    _email: str = Depends(require_owner),
):
    provided = body.model_dump(exclude_unset=True)
    try:
        patch = service.validate_patch(provided)
    except ValidationError as exc:
        return error_response(
            code="validation_failed",
            message=exc.message,
            status_code=422,
            details={"field": exc.field},
        )
    try:
        fresh = repository.update(skill_id, patch)
    except SkillNotFound:
        return error_response(
            code="skill_not_found",
            message=f"skill {skill_id} not found",
            status_code=404,
        )
    except SkillNameConflict:
        return error_response(
            code="validation_failed",
            message=f"a skill named {patch.get('name')!r} already exists",
            status_code=422,
            details={"field": "name"},
        )
    return _to_full(fresh)


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_skill(skill_id: UUID, _email: str = Depends(require_owner)) -> Response:
    if not repository.delete(skill_id):
        return error_response(
            code="skill_not_found",
            message=f"skill {skill_id} not found",
            status_code=404,
        )
    return Response(status_code=204)
```

**Note on `error_response.details`**: if the existing `error_response` helper doesn't accept a `details` kwarg, add it (mirroring SP4's convention) OR fold `field` into the message. Do a quick `grep` on `src/twaky/api/errors.py`:

```bash
grep -n "def error_response" src/twaky/api/errors.py
```

If `details` isn't supported, drop the `details=` argument in each call — the field name is already present in the message via `exc.field`.

- [ ] **Step 2: Extend `tests/api/routers/test_skills.py` with the 422 matrix + happy paths**

Append:

```python
# ------- POST /skills -------

def _valid_body(**over):
    body = {
        "name": "echo",
        "description": "Echo tool",
        "python_source": "def run(**kwargs):\n    return 'ok'",
        "bound_agents": ["atlas"],
    }
    body.update(over)
    return body


def test_post_creates_skill(owner_client):
    resp = owner_client.post("/skills", json=_valid_body())
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "echo"
    assert body["bound_agents"] == ["atlas"]


def test_post_401_without_session(anon_client):
    resp = anon_client.post("/skills", json=_valid_body())
    assert resp.status_code == 401


@pytest.mark.parametrize("bad_name", ["Echo", "1abc", "with-hyphen", ""])
def test_post_422_on_bad_name(owner_client, bad_name):
    resp = owner_client.post("/skills", json=_valid_body(name=bad_name))
    assert resp.status_code == 422


def test_post_422_on_syntax_error(owner_client):
    resp = owner_client.post(
        "/skills",
        json=_valid_body(python_source="def run("),
    )
    assert resp.status_code == 422
    assert "SyntaxError" in resp.json()["error"]["message"]


def test_post_422_on_missing_run(owner_client):
    resp = owner_client.post(
        "/skills",
        json=_valid_body(python_source="def other():\n    pass"),
    )
    assert resp.status_code == 422
    assert "run" in resp.json()["error"]["message"].lower()


def test_post_422_on_unknown_bound_agent(owner_client):
    resp = owner_client.post(
        "/skills",
        json=_valid_body(bound_agents=["atlas", "zeus"]),
    )
    assert resp.status_code == 422


def test_post_422_on_invalid_json_schema(owner_client):
    body = _valid_body()
    body["config_schema"] = {"type": "not-a-real-type"}
    resp = owner_client.post("/skills", json=body)
    assert resp.status_code == 422


def test_post_422_on_config_values_mismatching_schema(owner_client):
    body = _valid_body()
    body["config_schema"] = {
        "type": "object", "required": ["k"], "properties": {"k": {"type": "string"}},
    }
    body["config_values"] = {}
    resp = owner_client.post("/skills", json=body)
    assert resp.status_code == 422


def test_post_422_on_duplicate_name(owner_client):
    owner_client.post("/skills", json=_valid_body(name="dup"))
    resp = owner_client.post("/skills", json=_valid_body(name="dup"))
    assert resp.status_code == 422
    assert "already exists" in resp.json()["error"]["message"]


# ------- PATCH /skills/{id} -------

def test_patch_updates_description(owner_client):
    sk = _seed()
    resp = owner_client.patch(f"/skills/{sk.id}", json={"description": "new"})
    assert resp.status_code == 200
    assert resp.json()["description"] == "new"


def test_patch_422_on_empty_body(owner_client):
    sk = _seed()
    resp = owner_client.patch(f"/skills/{sk.id}", json={})
    assert resp.status_code == 422
    assert "at least one field" in resp.json()["error"]["message"]


def test_patch_404_on_unknown(owner_client):
    resp = owner_client.patch(f"/skills/{uuid4()}", json={"description": "x"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "skill_not_found"


def test_patch_422_on_name_collision(owner_client):
    _seed(name="taken")
    other = _seed(name="other")
    resp = owner_client.patch(f"/skills/{other.id}", json={"name": "taken"})
    assert resp.status_code == 422
    assert "already exists" in resp.json()["error"]["message"]


# ------- DELETE /skills/{id} -------

def test_delete_returns_204(owner_client):
    sk = _seed()
    resp = owner_client.delete(f"/skills/{sk.id}")
    assert resp.status_code == 204
    # Confirm gone.
    assert owner_client.get(f"/skills/{sk.id}").status_code == 404


def test_delete_404_on_unknown(owner_client):
    resp = owner_client.delete(f"/skills/{uuid4()}")
    assert resp.status_code == 404
```

- [ ] **Step 3: Run the full router test file**

```bash
uv run pytest tests/api/routers/test_skills.py -v
```

Expected: ~21 tests pass.

- [ ] **Step 4: Gates**

```bash
uv run ruff check src/twaky/api tests/api \
  && uv run mypy src/twaky/api
```

- [ ] **Step 5: Commit**

```bash
git add src/twaky/api/routers/skills.py tests/api/routers/test_skills.py
git commit -m "feat(api): POST/PATCH/DELETE /skills with full 422 matrix"
```

---

## Task 12: API — `POST /skills/{id}/test`

**Files:**
- Modify: `src/twaky/api/routers/skills.py` — add `POST /skills/{id}/test`
- Modify: `tests/api/routers/test_skills.py` — 4 outcome cases

**Interfaces:**
- Consumes: `executor.run_skill`, `SkillTimeout`, `SkillCrashed`, `SkillError` (T4); `repository.get` (T2).
- Produces:
  - `POST /skills/{id}/test` → 200 with `SkillTestResponse`, 404 if skill missing, 422 if body malformed.
  - **Never returns 500 for a skill runtime error** — that's the answer being asked for.

- [ ] **Step 1: Extend the router**

Append to `src/twaky/api/routers/skills.py`:

```python
from twaky.api.schemas.skills import SkillTestRequest, SkillTestResponse
from twaky.skills.executor import SkillCrashed, SkillError, SkillTimeout, run_skill


@router.post("/{skill_id}/test", response_model=SkillTestResponse)
def test_skill(
    skill_id: UUID,
    body: SkillTestRequest,
    _email: str = Depends(require_owner),
):
    sk = repository.get(skill_id)
    if sk is None:
        return error_response(
            code="skill_not_found",
            message=f"skill {skill_id} not found",
            status_code=404,
        )
    try:
        result = run_skill(
            python_source=sk.python_source,
            args=body.args,
            config=sk.config_values,
            timeout_s=30,
            memory_limit_mb=256,
            cpu_seconds=60,
        )
    except SkillTimeout as exc:
        return SkillTestResponse(outcome="timeout", message=str(exc))
    except SkillCrashed as exc:
        return SkillTestResponse(outcome="crashed", message=str(exc))
    except SkillError as exc:
        return SkillTestResponse(outcome="error", message=str(exc))
    return SkillTestResponse(outcome="ok", result=result)
```

- [ ] **Step 2: Test cases (4 outcomes + 404 + 422 body)**

Append to `tests/api/routers/test_skills.py`:

```python
# ------- POST /skills/{id}/test -------

def test_test_endpoint_ok_outcome(owner_client):
    sk = _seed()  # returns "1" from run(**kw)
    resp = owner_client.post(f"/skills/{sk.id}/test", json={"args": {}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "ok"
    assert body["result"] == 1


def test_test_endpoint_error_outcome(owner_client):
    sk = repository.create(
        name="raiser",
        description="Always raises",
        python_source="def run(**kw):\n    raise ValueError('nope')",
        config_schema={}, config_values={}, bound_agents=[], enabled=True,
    )
    resp = owner_client.post(f"/skills/{sk.id}/test", json={"args": {}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "error"
    assert "ValueError" in body["message"]
    assert "nope" in body["message"]


def test_test_endpoint_timeout_outcome(owner_client, monkeypatch):
    # Force a fast timeout by patching the timeout constant in the router.
    from twaky.api.routers import skills as skills_router

    real_run = skills_router.run_skill

    def fast_timeout(*, python_source, args, config, timeout_s, memory_limit_mb, cpu_seconds):
        return real_run(
            python_source=python_source, args=args, config=config,
            timeout_s=0.5, memory_limit_mb=memory_limit_mb, cpu_seconds=cpu_seconds,
        )

    monkeypatch.setattr(skills_router, "run_skill", fast_timeout)
    sk = repository.create(
        name="slow",
        description="Sleeps",
        python_source="import time\ndef run(**kw):\n    time.sleep(5)",
        config_schema={}, config_values={}, bound_agents=[], enabled=True,
    )
    resp = owner_client.post(f"/skills/{sk.id}/test", json={"args": {}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "timeout"
    assert "timed out" in body["message"]


def test_test_endpoint_404_unknown_skill(owner_client):
    resp = owner_client.post(f"/skills/{uuid4()}/test", json={"args": {}})
    assert resp.status_code == 404


def test_test_endpoint_422_malformed_body(owner_client):
    sk = _seed()
    resp = owner_client.post(f"/skills/{sk.id}/test", json={"args": "not-a-dict"})
    assert resp.status_code == 422


def test_test_endpoint_default_empty_args(owner_client):
    sk = _seed()
    resp = owner_client.post(f"/skills/{sk.id}/test", json={})
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "ok"
```

- [ ] **Step 3: Run the tests**

```bash
uv run pytest tests/api/routers/test_skills.py -v -k test
```

Expected: 6 test-endpoint tests pass. Full file now ~27 tests.

- [ ] **Step 4: Gates**

```bash
uv run ruff check src/twaky/api tests/api \
  && uv run mypy src/twaky/api
```

- [ ] **Step 5: Commit**

```bash
git add src/twaky/api/routers/skills.py tests/api/routers/test_skills.py
git commit -m "feat(api): POST /skills/{id}/test with 4-outcome envelope"
```

---

## Task 13: OpenAPI regen + api-types drift check

**Files:**
- Modify: `docs/api/openapi.yaml` (regenerated)
- Modify: `frontend/src/lib/api-types.d.ts` (regenerated)

**Interfaces:**
- Consumes: existing `make openapi` + `make api-types` targets (SP3a established).
- Produces: schemas from T10 + T12 visible to the frontend via typed openapi-fetch client.

- [ ] **Step 1: Regenerate OpenAPI dump**

```bash
make openapi
```

This dumps `docs/api/openapi.yaml` from the live FastAPI app. Diff should now include `Skill`, `SkillSummary`, `SkillCreate`, `SkillUpdate`, `SkillTestRequest`, `SkillTestResponse`, and paths `/skills`, `/skills/{skill_id}`, `/skills/{skill_id}/test`.

- [ ] **Step 2: Sanity-check the diff**

```bash
git diff docs/api/openapi.yaml | head -80
```

Confirm no unrelated churn (dependency-bump artifacts, unrelated route reorderings). If churn appears, investigate before continuing — it may indicate an ordering issue in `main.py`.

- [ ] **Step 3: Regenerate frontend types**

```bash
make api-types
```

This regenerates `frontend/src/lib/api-types.d.ts` via `openapi-typescript`.

- [ ] **Step 4: Run the drift-check test**

The existing SP3a/SP4 drift test asserts `make api-types` produces an idempotent output. Run:

```bash
uv run pytest tests/api/test_openapi_drift.py -v
```

Or the frontend equivalent:

```bash
cd frontend && npm run typecheck
```

Expected: green. If the drift test complains, re-run `make api-types` — it may have hit a race with `openapi.yaml` write.

- [ ] **Step 5: Commit**

```bash
git add docs/api/openapi.yaml frontend/src/lib/api-types.d.ts
git commit -m "chore(api): regen openapi + api-types for /skills endpoints"
```

---

## Task 14: Frontend hooks — `use-skills.ts`

**Files:**
- Create: `frontend/src/hooks/use-skills.ts`
- Create: `frontend/src/hooks/use-skills.test.tsx`

**Interfaces:**
- Consumes: `apiClient` (openapi-fetch, existing), TanStack Query v5 hooks.
- Produces:
  - `useSkills()` → list query (`['skills']`).
  - `useSkill(id)` → single query (`['skill', id]`).
  - `useCreateSkill()` → mutation; invalidates `['skills']`.
  - `useUpdateSkill(id)` → mutation; invalidates `['skills']` + `['skill', id]`.
  - `useDeleteSkill()` → mutation; invalidates `['skills']`.
  - `useTestSkill(id)` → mutation (no invalidation — non-persistent).

- [ ] **Step 1: Write `frontend/src/hooks/use-skills.ts`**

Match the shape of the existing `frontend/src/hooks/use-agents.ts` (SP4). Adjust naming.

```typescript
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '@/lib/api-client'
import type { components } from '@/lib/api-types'

type Skill = components['schemas']['Skill']
type SkillSummary = components['schemas']['SkillSummary']
type SkillCreate = components['schemas']['SkillCreate']
type SkillUpdate = components['schemas']['SkillUpdate']
type SkillTestResponse = components['schemas']['SkillTestResponse']

export function useSkills() {
  return useQuery({
    queryKey: ['skills'] as const,
    queryFn: async (): Promise<SkillSummary[]> => {
      const { data, error } = await apiClient.GET('/skills')
      if (error) throw error
      return data
    },
  })
}

export function useSkill(id: string | undefined) {
  return useQuery({
    queryKey: ['skill', id] as const,
    enabled: !!id && id !== 'new',
    queryFn: async (): Promise<Skill> => {
      const { data, error } = await apiClient.GET('/skills/{skill_id}', {
        params: { path: { skill_id: id! } },
      })
      if (error) throw error
      return data
    },
  })
}

export function useCreateSkill() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: SkillCreate): Promise<Skill> => {
      const { data, error } = await apiClient.POST('/skills', { body })
      if (error) throw error
      return data
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['skills'] })
    },
  })
}

export function useUpdateSkill(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: SkillUpdate): Promise<Skill> => {
      const { data, error } = await apiClient.PATCH('/skills/{skill_id}', {
        params: { path: { skill_id: id } },
        body,
      })
      if (error) throw error
      return data
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['skills'] })
      qc.invalidateQueries({ queryKey: ['skill', id] })
    },
  })
}

export function useDeleteSkill() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string): Promise<void> => {
      const { error } = await apiClient.DELETE('/skills/{skill_id}', {
        params: { path: { skill_id: id } },
      })
      if (error) throw error
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['skills'] })
    },
  })
}

export function useTestSkill(id: string) {
  return useMutation({
    mutationFn: async (args: Record<string, unknown>): Promise<SkillTestResponse> => {
      const { data, error } = await apiClient.POST('/skills/{skill_id}/test', {
        params: { path: { skill_id: id } },
        body: { args },
      })
      if (error) throw error
      return data
    },
  })
}
```

- [ ] **Step 2: Write `frontend/src/hooks/use-skills.test.tsx`**

Use MSW to mock `/skills*`. Follow `frontend/src/hooks/use-agents.test.tsx` for provider setup.

```typescript
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import {
  useCreateSkill, useDeleteSkill, useSkill, useSkills,
  useTestSkill, useUpdateSkill,
} from './use-skills'

const server = setupServer(
  http.get('/skills', () =>
    HttpResponse.json([
      { id: '11111111-1111-1111-1111-111111111111', name: 'echo',
        description: 'e', bound_agents: ['atlas'], enabled: true,
        created_at: '2026-08-02T00:00:00Z', updated_at: '2026-08-02T00:00:00Z' },
    ])
  ),
  http.get('/skills/:id', ({ params }) =>
    HttpResponse.json({
      id: params.id, name: 'echo', description: 'e',
      python_source: 'def run(**k): return 1',
      config_schema: {}, config_values: {}, bound_agents: ['atlas'],
      enabled: true,
      created_at: '2026-08-02T00:00:00Z', updated_at: '2026-08-02T00:00:00Z',
    })
  ),
  http.post('/skills', async ({ request }) => {
    const body = (await request.json()) as { name: string }
    return HttpResponse.json({
      id: '22222222-2222-2222-2222-222222222222',
      name: body.name, description: 'x', python_source: 'def run(): pass',
      config_schema: {}, config_values: {}, bound_agents: [], enabled: true,
      created_at: '2026-08-02T00:00:00Z', updated_at: '2026-08-02T00:00:00Z',
    }, { status: 201 })
  }),
  http.patch('/skills/:id', async ({ request, params }) => {
    const body = (await request.json()) as { description?: string }
    return HttpResponse.json({
      id: params.id, name: 'echo', description: body.description ?? 'e',
      python_source: 'x', config_schema: {}, config_values: {},
      bound_agents: [], enabled: true,
      created_at: '2026-08-02T00:00:00Z', updated_at: '2026-08-02T00:00:00Z',
    })
  }),
  http.delete('/skills/:id', () => new HttpResponse(null, { status: 204 })),
  http.post('/skills/:id/test', async ({ request }) => {
    const body = (await request.json()) as { args?: unknown }
    return HttpResponse.json({ outcome: 'ok', result: body.args })
  }),
)

beforeAll(() => server.listen())
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function wrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

describe('use-skills', () => {
  it('useSkills lists skills', async () => {
    const { result } = renderHook(() => useSkills(), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toHaveLength(1)
    expect(result.current.data![0].name).toBe('echo')
  })

  it('useSkill fetches by id', async () => {
    const { result } = renderHook(
      () => useSkill('11111111-1111-1111-1111-111111111111'),
      { wrapper: wrapper() },
    )
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data!.python_source).toContain('def run')
  })

  it('useSkill is disabled for id="new"', () => {
    const { result } = renderHook(() => useSkill('new'), { wrapper: wrapper() })
    expect(result.current.isFetching).toBe(false)
  })

  it('useCreateSkill posts and returns fresh row', async () => {
    const { result } = renderHook(() => useCreateSkill(), { wrapper: wrapper() })
    await result.current.mutateAsync({
      name: 'newone', description: 'x',
      python_source: 'def run(): pass', bound_agents: [],
      config_schema: {}, config_values: {}, enabled: true,
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data!.name).toBe('newone')
  })

  it('useUpdateSkill patches and returns updated row', async () => {
    const { result } = renderHook(
      () => useUpdateSkill('11111111-1111-1111-1111-111111111111'),
      { wrapper: wrapper() },
    )
    await result.current.mutateAsync({ description: 'updated' })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data!.description).toBe('updated')
  })

  it('useDeleteSkill resolves without a body', async () => {
    const { result } = renderHook(() => useDeleteSkill(), { wrapper: wrapper() })
    await result.current.mutateAsync('11111111-1111-1111-1111-111111111111')
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
  })

  it('useTestSkill returns SkillTestResponse', async () => {
    const { result } = renderHook(
      () => useTestSkill('11111111-1111-1111-1111-111111111111'),
      { wrapper: wrapper() },
    )
    await result.current.mutateAsync({ foo: 'bar' })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data!.outcome).toBe('ok')
    expect(result.current.data!.result).toEqual({ foo: 'bar' })
  })
})
```

- [ ] **Step 3: Run frontend hook tests**

```bash
cd frontend && npm run test:unit -- use-skills
```

Expected: 7 tests pass.

- [ ] **Step 4: Frontend gates**

```bash
cd frontend && npm run typecheck && npm run lint
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/use-skills.ts frontend/src/hooks/use-skills.test.tsx
git commit -m "feat(frontend): use-skills hooks (list/get/create/update/delete/test)"
```

---

## Task 15: Frontend list page + Nav link + shadcn primitives

**Files:**
- Create: `frontend/src/app/skills/page.tsx`
- Modify: `frontend/src/components/layout/header.tsx` — insert "Skills" nav link
- Create: `frontend/src/components/ui/switch.tsx` (shadcn CLI)
- Create: `frontend/src/components/ui/dialog.tsx` (shadcn CLI)
- Create: `frontend/src/components/ui/collapsible.tsx` (shadcn CLI)

**Interfaces:**
- Consumes: `useSkills`, `useDeleteSkill` (T14), shadcn Table/Badge/Button/AlertDialog (already installed SP4).
- Produces: `/skills` route, "Skills" nav link between "Agents" and "Stats".

- [ ] **Step 1: Install missing shadcn primitives**

```bash
cd frontend && npx shadcn@latest add switch dialog collapsible
```

This creates `frontend/src/components/ui/{switch,dialog,collapsible}.tsx`. Commit them as-is (no edits).

- [ ] **Step 2: Add "Skills" nav link**

Locate the existing nav in `frontend/src/components/layout/header.tsx` — SP4 established `Dashboard / Missions / Agents / Stats` order. Insert `Skills` between Agents and Stats:

```tsx
<nav>
  <Link href="/">Dashboard</Link>
  <Link href="/missions">Missions</Link>
  <Link href="/agents">Agents</Link>
  <Link href="/skills">Skills</Link>       {/* NEW */}
  <Link href="/stats">Stats</Link>
</nav>
```

(Adapt the exact JSX to the current file — likely uses a component like `<NavItem />` instead of raw `<Link />`.)

- [ ] **Step 3: Write `frontend/src/app/skills/page.tsx`**

```tsx
'use client'

import Link from 'next/link'
import { useState } from 'react'
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader,
  AlertDialogTitle, AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'
import { RelativeTime } from '@/components/ui/relative-time'
import { toast } from 'sonner'
import { useDeleteSkill, useSkills } from '@/hooks/use-skills'

export default function SkillsPage() {
  const { data: skills, isLoading } = useSkills()
  const deleteSkill = useDeleteSkill()
  const [pendingDelete, setPendingDelete] = useState<string | null>(null)

  async function handleDelete(id: string, name: string) {
    try {
      await deleteSkill.mutateAsync(id)
      toast.success(`Skill '${name}' deleted`)
    } catch {
      toast.error(`Failed to delete '${name}'`)
    } finally {
      setPendingDelete(null)
    }
  }

  if (isLoading) return <div className="p-8 text-muted-foreground">Loading…</div>

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Skills</h1>
        <Button asChild>
          <Link href="/skills/new">+ New skill</Link>
        </Button>
      </div>

      {(!skills || skills.length === 0) ? (
        <div className="mx-auto max-w-md rounded-lg border p-8 text-center space-y-4">
          <p className="text-muted-foreground">No skills yet.</p>
          <Button asChild>
            <Link href="/skills/new">+ Create your first skill</Link>
          </Button>
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Description</TableHead>
              <TableHead>Bound to</TableHead>
              <TableHead>Enabled</TableHead>
              <TableHead>Updated</TableHead>
              <TableHead className="w-40" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {skills.map((s) => (
              <TableRow key={s.id}>
                <TableCell><code className="font-mono text-sm">{s.name}</code></TableCell>
                <TableCell className="max-w-md truncate">{s.description}</TableCell>
                <TableCell className="space-x-1">
                  {s.bound_agents.map((a) => (
                    <Badge key={a} variant="secondary">{a}</Badge>
                  ))}
                </TableCell>
                <TableCell>
                  <span
                    className={
                      'inline-block h-2.5 w-2.5 rounded-full ' +
                      (s.enabled ? 'bg-green-500' : 'border border-muted-foreground')
                    }
                    aria-label={s.enabled ? 'enabled' : 'disabled'}
                  />
                </TableCell>
                <TableCell><RelativeTime iso={s.updated_at} /></TableCell>
                <TableCell className="space-x-2 text-right">
                  <Button asChild size="sm" variant="outline">
                    <Link href={`/skills/${s.id}`}>Edit</Link>
                  </Button>
                  <AlertDialog
                    open={pendingDelete === s.id}
                    onOpenChange={(o) => setPendingDelete(o ? s.id : null)}
                  >
                    <AlertDialogTrigger asChild>
                      <Button size="sm" variant="destructive">Delete</Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>Delete <code>{s.name}</code>?</AlertDialogTitle>
                        <AlertDialogDescription>
                          Missions in flight that use it will fail on next call.
                          This cannot be undone.
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={() => handleDelete(s.id, s.name)}>
                          Delete
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Smoke check — dev server**

```bash
cd frontend && npm run dev
```

Open `http://localhost:3000/skills`. Sign in first if needed. Expected: empty state visible ("No skills yet."). Click "+ New skill" — routes to `/skills/new` (blank page for now; T16 fills it in).

Ctrl-C when done.

- [ ] **Step 5: Frontend gates**

```bash
cd frontend && npm run typecheck && npm run lint && npm run build
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/skills/page.tsx \
        frontend/src/components/layout/header.tsx \
        frontend/src/components/ui/switch.tsx \
        frontend/src/components/ui/dialog.tsx \
        frontend/src/components/ui/collapsible.tsx
git commit -m "feat(frontend): /skills list page + nav link + shadcn primitives"
```

---

## Task 16: Frontend edit page — Monaco + metadata form + config editors

**Files:**
- Create: `frontend/src/app/skills/[id]/page.tsx`
- Create: `frontend/src/components/skills/skill-name-input.tsx`
- Create: `frontend/src/components/skills/skill-name-input.test.tsx`
- Create: `frontend/src/components/skills/skill-bound-agents.tsx`
- Create: `frontend/src/components/skills/skill-bound-agents.test.tsx`
- Create: `frontend/src/components/skills/skill-python-editor.tsx`
- Create: `frontend/src/components/skills/skill-config-editors.tsx`
- Modify: `frontend/package.json` — add `@monaco-editor/react` + `ajv`

**Interfaces:**
- Consumes: `useSkill`, `useCreateSkill`, `useUpdateSkill` (T14); shadcn Input/Textarea/Checkbox/Switch/Collapsible.
- Produces: `/skills/new` blank form + `/skills/[id]` populated edit form; both share the same page component.

- [ ] **Step 1: Install `@monaco-editor/react` + `ajv`**

```bash
cd frontend && npm install @monaco-editor/react ajv
```

Confirm additions in `package.json` are the MIT-licensed lines (both are).

- [ ] **Step 2: Write `frontend/src/components/skills/skill-name-input.tsx`**

```tsx
'use client'

import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

const NAME_RE = /^[a-z][a-z0-9_]{0,63}$/

export function SkillNameInput({
  value, onChange, disabled,
}: {
  value: string
  onChange: (v: string) => void
  disabled?: boolean
}) {
  const isValid = value === '' || NAME_RE.test(value)
  return (
    <div className="space-y-1">
      <Label htmlFor="skill-name">Name</Label>
      <Input
        id="skill-name"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="search_wikipedia"
        disabled={disabled}
        aria-invalid={!isValid}
      />
      {!isValid && (
        <p className="text-xs text-destructive">
          Must match <code>^[a-z][a-z0-9_]{'{'}0,63{'}'}$</code>
          {' '}(lowercase, digits, underscore; start with letter; 1-64 chars).
        </p>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Test — `skill-name-input.test.tsx`**

```tsx
import { render, screen, fireEvent } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SkillNameInput } from './skill-name-input'

describe('SkillNameInput', () => {
  it('accepts a valid name silently', () => {
    render(<SkillNameInput value="echo" onChange={() => {}} />)
    expect(screen.queryByText(/Must match/)).toBeNull()
  })

  it('shows error on invalid name', () => {
    render(<SkillNameInput value="Echo" onChange={() => {}} />)
    expect(screen.getByText(/Must match/)).toBeInTheDocument()
  })

  it('accepts empty string silently (pre-input state)', () => {
    render(<SkillNameInput value="" onChange={() => {}} />)
    expect(screen.queryByText(/Must match/)).toBeNull()
  })

  it('emits onChange on user input', () => {
    const onChange = vi.fn()
    render(<SkillNameInput value="" onChange={onChange} />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'x' } })
    expect(onChange).toHaveBeenCalledWith('x')
  })
})
```

- [ ] **Step 4: Write `skill-bound-agents.tsx` + test**

```tsx
'use client'

import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'

const AGENTS = ['atlas', 'chronos', 'plume', 'iris'] as const
type AgentId = typeof AGENTS[number]

export function SkillBoundAgents({
  value, onChange,
}: {
  value: AgentId[]
  onChange: (next: AgentId[]) => void
}) {
  function toggle(agent: AgentId, checked: boolean) {
    onChange(
      checked
        ? [...value.filter((a) => a !== agent), agent]
        : value.filter((a) => a !== agent),
    )
  }
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium">Bound agents</legend>
      {AGENTS.map((a) => (
        <div key={a} className="flex items-center space-x-2">
          <Checkbox
            id={`bind-${a}`}
            checked={value.includes(a)}
            onCheckedChange={(c) => toggle(a, c === true)}
          />
          <Label htmlFor={`bind-${a}`} className="font-normal capitalize">{a}</Label>
        </div>
      ))}
    </fieldset>
  )
}
```

Test:

```tsx
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SkillBoundAgents } from './skill-bound-agents'

describe('SkillBoundAgents', () => {
  it('checks the boxes for bound agents', () => {
    render(<SkillBoundAgents value={['atlas', 'plume']} onChange={() => {}} />)
    expect(screen.getByLabelText('atlas')).toBeChecked()
    expect(screen.getByLabelText('plume')).toBeChecked()
    expect(screen.getByLabelText('chronos')).not.toBeChecked()
    expect(screen.getByLabelText('iris')).not.toBeChecked()
  })

  it('adds agent on check', () => {
    const onChange = vi.fn()
    render(<SkillBoundAgents value={['atlas']} onChange={onChange} />)
    fireEvent.click(screen.getByLabelText('plume'))
    expect(onChange).toHaveBeenCalledWith(['atlas', 'plume'])
  })

  it('removes agent on uncheck', () => {
    const onChange = vi.fn()
    render(<SkillBoundAgents value={['atlas', 'plume']} onChange={onChange} />)
    fireEvent.click(screen.getByLabelText('atlas'))
    expect(onChange).toHaveBeenCalledWith(['plume'])
  })
})
```

- [ ] **Step 5: Write `skill-python-editor.tsx`** (lazy-loaded Monaco)

```tsx
'use client'

import dynamic from 'next/dynamic'

const MonacoEditor = dynamic(() => import('@monaco-editor/react'), { ssr: false })

const STARTER = `def run(**kwargs) -> str:
    """One-line description shown to the LLM."""
    # kwargs come from the LLM; config injected via config_values
    return "hello"
`

export function SkillPythonEditor({
  value, onChange,
}: {
  value: string
  onChange: (v: string) => void
}) {
  return (
    <div className="border rounded-md overflow-hidden" style={{ height: 500 }}>
      <MonacoEditor
        language="python"
        theme="vs-dark"
        value={value || STARTER}
        onChange={(v) => onChange(v ?? '')}
        options={{
          minimap: { enabled: false },
          fontSize: 13,
          tabSize: 4,
          scrollBeyondLastLine: false,
        }}
      />
    </div>
  )
}
```

- [ ] **Step 6: Write `skill-config-editors.tsx`** (Collapsible JSON Schema + Values)

```tsx
'use client'

import Ajv from 'ajv'
import dynamic from 'next/dynamic'
import { useMemo, useState } from 'react'
import {
  Collapsible, CollapsibleContent, CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { Button } from '@/components/ui/button'

const MonacoEditor = dynamic(() => import('@monaco-editor/react'), { ssr: false })

const ajv = new Ajv({ allErrors: true, strict: false })

export function SkillConfigEditors({
  schema, values, onSchemaChange, onValuesChange,
}: {
  schema: object
  values: object
  onSchemaChange: (o: object) => void
  onValuesChange: (o: object) => void
}) {
  const [schemaText, setSchemaText] = useState(JSON.stringify(schema, null, 2))
  const [valuesText, setValuesText] = useState(JSON.stringify(values, null, 2))
  const [schemaError, setSchemaError] = useState<string | null>(null)
  const [valuesError, setValuesError] = useState<string | null>(null)

  const validate = useMemo(() => {
    try {
      return ajv.compile(schema)
    } catch (e) {
      return null
    }
  }, [schema])

  function handleSchemaEdit(v: string) {
    setSchemaText(v)
    try {
      const parsed = JSON.parse(v)
      onSchemaChange(parsed)
      setSchemaError(null)
    } catch (e) {
      setSchemaError('Invalid JSON')
    }
  }

  function handleValuesEdit(v: string) {
    setValuesText(v)
    try {
      const parsed = JSON.parse(v)
      onValuesChange(parsed)
      if (validate && !validate(parsed)) {
        setValuesError(ajv.errorsText(validate.errors))
      } else {
        setValuesError(null)
      }
    } catch {
      setValuesError('Invalid JSON')
    }
  }

  return (
    <div className="space-y-2">
      <Collapsible>
        <CollapsibleTrigger asChild>
          <Button variant="ghost" size="sm">Config schema (JSON)</Button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="border rounded" style={{ height: 150 }}>
            <MonacoEditor
              language="json" theme="vs-dark"
              value={schemaText} onChange={(v) => handleSchemaEdit(v ?? '')}
              options={{ minimap: { enabled: false }, fontSize: 12 }}
            />
          </div>
          {schemaError && <p className="text-xs text-destructive">{schemaError}</p>}
        </CollapsibleContent>
      </Collapsible>

      <Collapsible>
        <CollapsibleTrigger asChild>
          <Button variant="ghost" size="sm">Config values (JSON)</Button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="border rounded" style={{ height: 150 }}>
            <MonacoEditor
              language="json" theme="vs-dark"
              value={valuesText} onChange={(v) => handleValuesEdit(v ?? '')}
              options={{ minimap: { enabled: false }, fontSize: 12 }}
            />
          </div>
          {valuesError && <p className="text-xs text-destructive">{valuesError}</p>}
        </CollapsibleContent>
      </Collapsible>
    </div>
  )
}
```

- [ ] **Step 7: Write `frontend/src/app/skills/[id]/page.tsx`**

```tsx
'use client'

import { useParams, useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { SkillBoundAgents } from '@/components/skills/skill-bound-agents'
import { SkillConfigEditors } from '@/components/skills/skill-config-editors'
import { SkillNameInput } from '@/components/skills/skill-name-input'
import { SkillPythonEditor } from '@/components/skills/skill-python-editor'
import { SkillTestDialog } from '@/components/skills/skill-test-dialog'
import {
  useCreateSkill, useSkill, useUpdateSkill,
} from '@/hooks/use-skills'

const NAME_RE = /^[a-z][a-z0-9_]{0,63}$/
type AgentId = 'atlas' | 'chronos' | 'plume' | 'iris'

export default function SkillEditPage() {
  const { id } = useParams<{ id: string }>()
  const isNew = id === 'new'
  const router = useRouter()

  const skillQuery = useSkill(isNew ? undefined : id)
  const createSkill = useCreateSkill()
  const updateSkill = useUpdateSkill(id)

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [pythonSource, setPythonSource] = useState('')
  const [boundAgents, setBoundAgents] = useState<AgentId[]>([])
  const [enabled, setEnabled] = useState(true)
  const [configSchema, setConfigSchema] = useState<object>({})
  const [configValues, setConfigValues] = useState<object>({})
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    if (skillQuery.data) {
      setName(skillQuery.data.name)
      setDescription(skillQuery.data.description)
      setPythonSource(skillQuery.data.python_source)
      setBoundAgents(skillQuery.data.bound_agents)
      setEnabled(skillQuery.data.enabled)
      setConfigSchema(skillQuery.data.config_schema)
      setConfigValues(skillQuery.data.config_values)
      setDirty(false)
    }
  }, [skillQuery.data])

  function mark<T>(setter: (v: T) => void) {
    return (v: T) => { setter(v); setDirty(true) }
  }

  const isFormValid =
    NAME_RE.test(name)
    && description.trim().length >= 1 && description.length <= 1000
    && pythonSource.trim().length >= 1 && pythonSource.length <= 32000

  async function handleSave() {
    const body = {
      name, description, python_source: pythonSource,
      bound_agents: boundAgents, enabled,
      config_schema: configSchema, config_values: configValues,
    }
    try {
      if (isNew) {
        const created = await createSkill.mutateAsync(body)
        toast.success(`Skill '${created.name}' created`)
        router.push(`/skills/${created.id}`)
      } else {
        await updateSkill.mutateAsync(body)
        toast.success('Saved')
        setDirty(false)
      }
    } catch (e) {
      toast.error(`Save failed: ${(e as Error).message}`)
    }
  }

  if (!isNew && skillQuery.isLoading) {
    return <div className="p-8 text-muted-foreground">Loading…</div>
  }

  return (
    <div className="p-6 grid grid-cols-3 gap-6">
      <div className="col-span-2 space-y-2">
        <Label>Python source</Label>
        <SkillPythonEditor value={pythonSource} onChange={mark(setPythonSource)} />
      </div>

      <div className="space-y-4">
        <SkillNameInput value={name} onChange={mark(setName)} disabled={!isNew} />

        <div className="space-y-1">
          <Label htmlFor="desc">Description</Label>
          <Textarea
            id="desc"
            rows={3}
            value={description}
            onChange={(e) => { setDescription(e.target.value); setDirty(true) }}
          />
          <p className="text-xs text-muted-foreground text-right">
            {description.length} / 1000
          </p>
        </div>

        <SkillBoundAgents value={boundAgents} onChange={mark(setBoundAgents)} />

        <div className="flex items-center space-x-2">
          <Switch
            id="enabled"
            checked={enabled}
            onCheckedChange={mark(setEnabled)}
          />
          <Label htmlFor="enabled">Enabled</Label>
        </div>

        <SkillConfigEditors
          schema={configSchema} values={configValues}
          onSchemaChange={mark(setConfigSchema)}
          onValuesChange={mark(setConfigValues)}
        />
      </div>

      <div className="col-span-3 flex items-center justify-between border-t pt-4">
        <SkillTestDialog
          skillId={id}
          disabled={isNew}
          tooltip={isNew ? 'Save the skill first, then test.' : undefined}
        />
        <div className="space-x-2">
          <Button variant="outline" onClick={() => router.push('/skills')}>Cancel</Button>
          <Button onClick={handleSave} disabled={!isFormValid || !dirty}>
            {isNew ? 'Create' : 'Save'}
          </Button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 8: Run component tests**

```bash
cd frontend && npm run test:unit -- skills
```

Expected: name + bound-agents tests pass (~7 tests). The editor page has no isolated test — it's covered by the E2E in T18.

- [ ] **Step 9: Smoke — dev server**

```bash
cd frontend && npm run dev
```

- `/skills/new` — Monaco loads (300 ms lazy chunk), starter template visible, Save disabled until name+description filled.
- Fill in `name=demo`, description, keep starter Python, tick Atlas, click Save. Expect toast + redirect to `/skills/[uuid]`.
- Edit description, click Save — toast + no redirect.
- Go back to `/skills` — new row visible.

Ctrl-C. If anything fails, check browser console.

- [ ] **Step 10: Frontend gates**

```bash
cd frontend && npm run typecheck && npm run lint && npm run build
```

Verify the Monaco chunk is lazy (build output should show a separate chunk for `@monaco-editor/react`, NOT part of the main bundle).

- [ ] **Step 11: Commit**

```bash
git add frontend/src/app/skills/'[id]'/page.tsx \
        frontend/src/components/skills/ \
        frontend/package.json frontend/package-lock.json
git commit -m "feat(frontend): skill edit page with Monaco + config editors"
```

---

## Task 17: Frontend Test dialog

**Files:**
- Create: `frontend/src/components/skills/skill-test-dialog.tsx`
- Create: `frontend/src/components/skills/skill-test-dialog.test.tsx`

**Interfaces:**
- Consumes: `useTestSkill` (T14), shadcn Dialog, Button, Textarea, Badge.
- Produces: `<SkillTestDialog skillId disabled tooltip />` — button + dialog + result panel.

- [ ] **Step 1: Write `skill-test-dialog.tsx`**

```tsx
'use client'

import { useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { useTestSkill } from '@/hooks/use-skills'

type Outcome = 'ok' | 'timeout' | 'crashed' | 'error'

const badgeVariant: Record<Outcome, 'default' | 'destructive'> = {
  ok: 'default',
  timeout: 'destructive',
  crashed: 'destructive',
  error: 'destructive',
}

export function SkillTestDialog({
  skillId, disabled, tooltip,
}: {
  skillId: string
  disabled?: boolean
  tooltip?: string
}) {
  const [open, setOpen] = useState(false)
  const [argsText, setArgsText] = useState('{}')
  const [parseError, setParseError] = useState<string | null>(null)
  const testSkill = useTestSkill(skillId)

  async function handleRun() {
    let args: Record<string, unknown>
    try {
      args = JSON.parse(argsText)
      if (typeof args !== 'object' || Array.isArray(args) || args === null) {
        throw new Error('args must be a JSON object')
      }
      setParseError(null)
    } catch (e) {
      setParseError((e as Error).message)
      return
    }
    testSkill.mutate(args)
  }

  const outcome = testSkill.data?.outcome as Outcome | undefined

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" disabled={disabled} title={tooltip}>
          Test
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Test skill</DialogTitle>
        </DialogHeader>

        <Label htmlFor="test-args">Args (JSON object)</Label>
        <Textarea
          id="test-args"
          rows={4}
          value={argsText}
          onChange={(e) => setArgsText(e.target.value)}
          placeholder='{"query": "twake"}'
          className="font-mono"
        />
        {parseError && <p className="text-xs text-destructive">{parseError}</p>}

        {testSkill.isPending && (
          <p className="text-sm text-muted-foreground">Running…</p>
        )}

        {outcome && (
          <div className="space-y-2 border-t pt-3">
            <Badge variant={badgeVariant[outcome]}>outcome: {outcome}</Badge>
            {outcome === 'ok' ? (
              <pre className="text-xs bg-muted p-2 rounded overflow-auto">
                {JSON.stringify(testSkill.data!.result, null, 2)}
              </pre>
            ) : (
              <p className="text-sm text-destructive">{testSkill.data!.message}</p>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)}>Close</Button>
          <Button onClick={handleRun} disabled={testSkill.isPending}>Run</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
```

- [ ] **Step 2: Write the test**

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { SkillTestDialog } from './skill-test-dialog'

const server = setupServer(
  http.post('/skills/:id/test', async ({ request }) => {
    const body = (await request.json()) as { args: Record<string, unknown> }
    if ((body.args as { fail?: boolean }).fail) {
      return HttpResponse.json({ outcome: 'error', message: 'ValueError: nope' })
    }
    return HttpResponse.json({ outcome: 'ok', result: body.args })
  }),
)

beforeAll(() => server.listen())
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function wrap(el: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{el}</QueryClientProvider>)
}

describe('SkillTestDialog', () => {
  it('renders a disabled button with tooltip when disabled', () => {
    wrap(<SkillTestDialog skillId="x" disabled tooltip="Save first" />)
    const btn = screen.getByRole('button', { name: /test/i })
    expect(btn).toBeDisabled()
    expect(btn).toHaveAttribute('title', 'Save first')
  })

  it('opens on click and shows args textarea', () => {
    wrap(<SkillTestDialog skillId="x" />)
    fireEvent.click(screen.getByRole('button', { name: /test/i }))
    expect(screen.getByLabelText(/args \(json object\)/i)).toBeInTheDocument()
  })

  it('shows JSON parse error for invalid input', () => {
    wrap(<SkillTestDialog skillId="x" />)
    fireEvent.click(screen.getByRole('button', { name: /test/i }))
    const ta = screen.getByLabelText(/args/i)
    fireEvent.change(ta, { target: { value: 'not-json' } })
    fireEvent.click(screen.getByRole('button', { name: /^run$/i }))
    expect(screen.getByText(/Unexpected token|Invalid|Expected/i)).toBeInTheDocument()
  })

  it('shows outcome=ok and result on success', async () => {
    wrap(<SkillTestDialog skillId="x" />)
    fireEvent.click(screen.getByRole('button', { name: /test/i }))
    fireEvent.change(screen.getByLabelText(/args/i), { target: { value: '{"foo":"bar"}' } })
    fireEvent.click(screen.getByRole('button', { name: /^run$/i }))
    await waitFor(() => expect(screen.getByText(/outcome: ok/i)).toBeInTheDocument())
    expect(screen.getByText(/"foo": "bar"/)).toBeInTheDocument()
  })

  it('shows outcome=error and message on failure', async () => {
    wrap(<SkillTestDialog skillId="x" />)
    fireEvent.click(screen.getByRole('button', { name: /test/i }))
    fireEvent.change(screen.getByLabelText(/args/i), { target: { value: '{"fail":true}' } })
    fireEvent.click(screen.getByRole('button', { name: /^run$/i }))
    await waitFor(() => expect(screen.getByText(/outcome: error/i)).toBeInTheDocument())
    expect(screen.getByText(/ValueError: nope/)).toBeInTheDocument()
  })
})
```

- [ ] **Step 3: Run tests**

```bash
cd frontend && npm run test:unit -- skill-test-dialog
```

Expected: 5 tests pass.

- [ ] **Step 4: Frontend gates**

```bash
cd frontend && npm run typecheck && npm run lint
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/skills/skill-test-dialog.tsx \
        frontend/src/components/skills/skill-test-dialog.test.tsx
git commit -m "feat(frontend): skill test dialog with outcome/result panel"
```

---

## Task 18: Playwright E2E

**Files:**
- Create: `frontend/tests/e2e/skills-create.spec.ts`
- Create: `frontend/tests/e2e/skills-test.spec.ts`

**Interfaces:**
- Consumes: existing Playwright config, session-cookie helper (SP3b), running dev stack (`api` + `frontend` + `twaky-pg`).
- Produces: 2 E2E specs covering the golden paths.

- [ ] **Step 1: Ensure the dev stack is running**

```bash
docker compose up -d twaky-pg
cd /home/mmaudet/work/twaky && uv run twaky-api &
cd frontend && npm run dev &
```

Then verify with the existing SP4 E2E:

```bash
cd frontend && npx playwright test tests/e2e/agents-edit.spec.ts --headed=false
```

If green, the environment is ready. Kill the background dev processes before continuing (`jobs -p | xargs kill`).

- [ ] **Step 2: Write `frontend/tests/e2e/skills-create.spec.ts`**

Follow the shape of `frontend/tests/e2e/agents-edit.spec.ts`.

```typescript
import { expect, test } from '@playwright/test'
import { loginAsOwner } from './helpers/auth'

test('create skill via UI, verify it appears in list', async ({ page }) => {
  await loginAsOwner(page)
  await page.goto('/skills')

  await page.getByRole('link', { name: /new skill/i }).click()
  await expect(page).toHaveURL(/\/skills\/new$/)

  // Fill the form.
  await page.getByLabel(/^name$/i).fill('e2e_echo')
  await page.getByLabel(/^description$/i).fill('E2E echo skill')

  // Monaco is a canvas — we edit via textbox role after focusing.
  const editor = page.locator('.monaco-editor').first()
  await editor.click()
  await page.keyboard.press('Meta+A')  // select-all
  await page.keyboard.press('Delete')
  await page.keyboard.type(`def run(**kwargs):\n    return "hello"`)

  // Bind Atlas.
  await page.getByLabel('atlas').check()

  await page.getByRole('button', { name: /^create$/i }).click()

  // Toast + redirect to /skills/<uuid>.
  await expect(page).toHaveURL(/\/skills\/[0-9a-f-]{36}$/)

  // Back to list → row visible.
  await page.goto('/skills')
  await expect(page.getByText('e2e_echo')).toBeVisible()

  // Cleanup: delete the row so the test is idempotent.
  await page.getByRole('row', { name: /e2e_echo/ })
    .getByRole('button', { name: /delete/i }).click()
  await page.getByRole('button', { name: /^delete$/i }).last().click()
  await expect(page.getByText('e2e_echo')).not.toBeVisible()
})
```

- [ ] **Step 3: Write `frontend/tests/e2e/skills-test.spec.ts`**

```typescript
import { expect, test } from '@playwright/test'
import { loginAsOwner } from './helpers/auth'

test('run skill via Test dialog, verify outcome=ok', async ({ page, request }) => {
  await loginAsOwner(page)

  // Seed a skill via the API (faster than UI create).
  const created = await request.post('/api/skills', {
    data: {
      name: 'e2e_testable',
      description: 'Testable',
      python_source: 'def run(**kwargs):\n    return {"echo": kwargs}',
      bound_agents: [],
    },
  })
  expect(created.ok()).toBeTruthy()
  const { id } = await created.json()

  try {
    await page.goto(`/skills/${id}`)
    await page.getByRole('button', { name: /^test$/i }).click()

    const argsBox = page.getByLabel(/args \(json object\)/i)
    await argsBox.fill('{"foo": "bar"}')
    await page.getByRole('button', { name: /^run$/i }).click()

    await expect(page.getByText(/outcome: ok/i)).toBeVisible()
    await expect(page.locator('pre')).toContainText('"foo": "bar"')
  } finally {
    await request.delete(`/api/skills/${id}`)
  }
})
```

- [ ] **Step 4: Run the E2E specs**

```bash
cd frontend && npx playwright test tests/e2e/skills-create.spec.ts tests/e2e/skills-test.spec.ts
```

Expected: 2 specs pass. If Monaco selection fails on your OS (Meta+A is macOS), switch to `Control+A`.

- [ ] **Step 5: Commit**

```bash
git add frontend/tests/e2e/skills-create.spec.ts frontend/tests/e2e/skills-test.spec.ts
git commit -m "test(e2e): skills-create + skills-test Playwright specs"
```

---

## Task 19: README + full-repo gate sweep

**Files:**
- Modify: `README.md` — add "## Custom skills (sub-project 5)" section

**Interfaces:**
- Consumes: nothing new.
- Produces: user-visible docs; final green run of all gates.

- [ ] **Step 1: Add README section**

Append to `README.md` (between the SP4 section and any Roadmap section):

```markdown
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
```

- [ ] **Step 2: Full-repo gate sweep (Python)**

```bash
uv run ruff check . \
  && uv run ruff format --check . \
  && uv run mypy src/ \
  && uv run pytest -q
```

Expected: all green. Baseline test count from SP4 + ~65 new skills tests.

- [ ] **Step 3: Full-repo gate sweep (frontend)**

```bash
cd frontend \
  && npm run typecheck \
  && npm run lint \
  && npm run test:unit \
  && npm run build
```

- [ ] **Step 4: OpenAPI drift check**

```bash
make api-types && git diff --exit-code frontend/src/lib/api-types.d.ts
```

Expected: no diff — types are already up to date from T13.

- [ ] **Step 5: E2E full run**

```bash
cd frontend && npx playwright test
```

Expected: all specs pass (SP4 agents + SP5 skills).

- [ ] **Step 6: Integration tests on real Postgres**

```bash
uv run pytest -m integration -v
```

Expected: T4 rlimit tests + T7 listener test pass on Linux.

- [ ] **Step 7: Manual sanity check on the running daemon**

```bash
# Restart daemon so it picks up the new listener.
docker compose restart twaky-atlas

# Watch logs for the boot messages.
docker compose logs -f twaky-atlas | head -30
```

Expected log lines (interleaved with existing SP4 output):
- `skill config listener starting`
- `agent config listener starting`

Create a skill via the UI, then in the same terminal:

```bash
docker compose logs --since 30s twaky-atlas | grep skill
```

Expected: `skill changed (payload=<uuid>), invalidating registry cache`.

- [ ] **Step 8: Commit + tag**

```bash
git add README.md
git commit -m "docs(skills): README section for sub-project 5"
git tag sp5-done
```

---

## Post-plan self-review notes

Cross-checked plan against spec §11 (task decomposition preview). Coverage:

| Spec §11 item | Plan task |
|---|---|
| T1 `sql/007_init_skills.sh` | Plan T1 ✅ |
| T2 `skills_config/models.py` + `repository.py` | Plan T2 ✅ |
| T3 `skills_config/service.py` | Plan T3 ✅ |
| T4 `skills/executor.py` | Plan T4 ✅ |
| T5 `skills/registry.py` | Plan T5 ✅ |
| T6 `skills/tool_adapter.py` | Plan T6 ✅ |
| T7 `skills/config_listener.py` | Plan T7 ✅ |
| T8 wire listener into atlas_daemon | Plan T8 ✅ |
| T9 refactor 4 agent modules | Plan T9 ✅ |
| T10-T13 API endpoints | Plan T10–T13 ✅ |
| T14 frontend hooks | Plan T14 ✅ |
| T15 frontend list page | Plan T15 ✅ |
| T16 frontend edit page + Monaco | Plan T16 ✅ |
| T17 test dialog | Plan T17 ✅ |
| T18 Playwright E2E | Plan T18 ✅ |
| T19 README + gates | Plan T19 ✅ |

Cross-checked with spec §12 global constraints — all 19 items copied verbatim into the Global Constraints block at the top.

Cross-checked with spec §2.3 success criteria:

1. **Create + save + call from a mission — no daemon restart.** T7 listener + T5 invalidate_all + T9 merged_tools_for → yes.
2. **422 on malformed edit.** T3 validation + T11 422 matrix → yes.
3. **Malicious skill (infinite loop, OOM, crash) contained.** T4 executor + T6 error-string mapping → yes; daemon stays alive by design.
4. **Restart preserves edited skills.** DB row survives; T8 boot-time `invalidate_all` + T5 cache-first fetch on next call → yes.
5. **In-flight mission picks up new skill on next sub-agent invocation.** T9 `merged_tools_for` runs at every node invocation, cache is invalidated on NOTIFY → yes.
6. **All gates green.** T19 sweeps `ruff`, `mypy`, `pytest`, `npm typecheck/lint/build`, drift check, E2E → yes.

Type consistency verified: `Skill` dataclass fields (T2) match pydantic `Skill` model fields (T10) match `Skill` dataclass round-trip in tool_adapter (T6). `run_skill(python_source, args, config, *, timeout_s, memory_limit_mb, cpu_seconds)` signature is stable across T4/T6/T12. `load_skills_for_agent(agent_id)` and `invalidate_all()` signatures stable across T5/T7/T8/T9. `service.ValidationError(field, message)` stable across T3/T11.

No placeholders detected. Each code block is directly runnable.

---

**End of plan.** ~19 tasks, TDD-shaped (test-first for T2–T7, T9–T12, T14, T16–T18). Estimated implementer time: 2–3 focused days.
