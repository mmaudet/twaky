# Twaky Agent Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the 4 built-in agents (Atlas, Chronos, Plume, Iris) into Postgres-backed configurable entities editable via API + web UI, with the daemon live-reloading changed configs on the next sub-agent invocation via `LISTEN/NOTIFY` — no restart required.

**Architecture:** New `agent` table on `twaky-pg` seeded with the 4 rows at first boot. New Python package `src/twaky/agents_config/` (repository + service) exposes the config over the FastAPI app at `/api/agents/*`. New module `src/twaky/agents/registry.py` provides a thread-safe in-process cache read by every sub-agent node function. New module `src/twaky/agents/config_listener.py` runs alongside the atlas daemon, invalidating cache entries on `agent_config_changed` NOTIFY. Frontend adds `/agents` list + `/agents/[id]` edit page.

**Tech Stack:** Python 3.12, psycopg3 (raw SQL, no ORM), FastAPI, pydantic v2, LangGraph, ChatLiteLLM, Next.js 15 App Router, TanStack Query v5, openapi-fetch, shadcn/ui, Radix primitives, vitest, Playwright.

## Global Constraints

Every task's requirements implicitly include this section — copied verbatim from spec §11:

- **Endpoint mount:** `/api/agents/*` — never `/agents/*` at root, never a versioned prefix.
- **Table name:** `agent` (singular, unquoted).
- **NOTIFY channel name:** `agent_config_changed` (verbatim).
- **Agent IDs (source-of-truth):** `atlas`, `chronos`, `plume`, `iris` — exactly these 4, lowercase, no plural.
- **Model fallback rule:** `cfg.model or settings.model` — never invert; a set value always wins over the env var.
- **Temperature fallback rule:** `if cfg.temperature is not None: kwargs["temperature"] = cfg.temperature` — never pass `temperature=None` to `ChatLiteLLM`.
- **Prompt bounds:** 1-8000 chars, enforced in DB CHECK, pydantic, and frontend form — all three layers.
- **Temperature bounds:** 0.0-2.0 inclusive, enforced in DB CHECK, pydantic, and frontend form.
- **New Python package:** `src/twaky/agents_config/` (with underscore) — NOT `agentsconfig/` and NOT under `src/twaky/agents/`.
- **Error envelope:** same shape as sub-project 3a — `{"error": {"code": "...", "message": "..."}}`.
- **Frontend nav link:** label `Agents`, positioned between `Missions` and `Stats` in the header.
- **`Reset to defaults`** pulls from `/api/agents/{id}/default_prompt` (server-authoritative), NOT a hardcoded frontend copy.
- **No versioning, no history table, no snapshotting** — YAGNI. If a later sub-project needs it, that sub-project adds it.
- **SQL migration convention:** twaky-pg init scripts are `.sh` files running heredoc'd `psql`, numbered `NNN_init_<domain>.sh` — see `sql/004_init_mission.sh` for the template. This plan writes `sql/006_init_agents.sh`, NOT a raw `.sql` file (the spec §7.1 mentions `003_agent_config.sql`; the plan uses the correct existing convention).

---

## File Structure

**Created files (new)**

| Path | Purpose |
|---|---|
| `sql/006_init_agents.sh` | psql-heredoc migration: `agent` table + triggers + seed |
| `src/twaky/agents/defaults.py` | `DEFAULT_PROMPTS: dict[str, str]` — source of truth for `/default_prompt` |
| `src/twaky/agents/registry.py` | Thread-safe in-process cache; `load_agent_config()`, `invalidate()`, `AgentConfigMissing` |
| `src/twaky/agents/config_listener.py` | Async LISTEN loop; invalidates cache on NOTIFY |
| `src/twaky/agents_config/__init__.py` | Empty package init |
| `src/twaky/agents_config/models.py` | `AgentConfig` dataclass (moved from `registry.py` for reuse — see T2) |
| `src/twaky/agents_config/repository.py` | Raw psycopg CRUD: `list_all()`, `get()`, `update()` |
| `src/twaky/agents_config/service.py` | Validation (temperature bounds, prompt length) + `effective_model()` |
| `src/twaky/api/routers/agents.py` | 4 FastAPI endpoints |
| `src/twaky/api/schemas/agents.py` | `Agent`, `AgentSummary`, `AgentUpdate` pydantic models |
| `tests/agents/test_defaults.py` | Assert DEFAULT_PROMPTS has 4 keys, non-empty |
| `tests/agents/test_registry.py` | Cache miss/hit/invalidate/missing-row fallback |
| `tests/agents/test_registry_notify_trigger.py` | Real-postgres: UPDATE fires notify; cache clears |
| `tests/agents_config/__init__.py` | Empty |
| `tests/agents_config/test_repository.py` | CRUD unit tests |
| `tests/agents_config/test_service.py` | Validation unit tests |
| `tests/api/routers/test_agents.py` | Router unit tests (all 4 endpoints, full matrix) |
| `tests/integration/test_agent_config_listener.py` | Daemon integration: LISTEN loop invalidates within 1s |
| `tests/sql/test_agent_seed_matches_defaults.py` | Parses `sql/006_init_agents.sh`, compares to `DEFAULT_PROMPTS` |
| `frontend/src/hooks/use-agents.ts` | `useAgents`, `useAgent`, `useDefaultPrompt`, `useUpdateAgent` |
| `frontend/src/hooks/use-agents.test.ts` | MSW-mocked hook tests |
| `frontend/src/app/agents/page.tsx` | List page |
| `frontend/src/app/agents/[id]/page.tsx` | Edit page |
| `frontend/src/components/agents/agent-model-input.tsx` | Hybrid Select + Custom input |
| `frontend/src/components/agents/agent-model-input.test.tsx` | Toggle logic tests |
| `frontend/src/components/agents/agent-temperature-input.tsx` | Slider + null checkbox |
| `frontend/src/components/agents/agent-temperature-input.test.tsx` | Slider disabled when null tests |
| `frontend/src/components/agents/agent-prompt-input.tsx` | Textarea + character counter |
| `frontend/src/components/agents/agent-prompt-input.test.tsx` | Counter turns red past 8000 |
| `frontend/src/components/agents/reset-to-defaults-dialog.tsx` | AlertDialog wrapper |
| `frontend/src/components/agents/reset-to-defaults-dialog.test.tsx` | Confirm pulls default_prompt |
| `frontend/src/components/ui/select.tsx` | shadcn Select (added via `npx shadcn add select`) |
| `frontend/src/components/ui/slider.tsx` | shadcn Slider (`npx shadcn add slider`) |
| `frontend/src/components/ui/alert-dialog.tsx` | shadcn AlertDialog (`npx shadcn add alert-dialog`) |
| `frontend/src/components/ui/checkbox.tsx` | shadcn Checkbox (`npx shadcn add checkbox`) |
| `frontend/src/components/ui/input.tsx` | shadcn Input (`npx shadcn add input`) |
| `frontend/src/components/ui/label.tsx` | shadcn Label (`npx shadcn add label`) |
| `frontend/tests/e2e/agents-edit.spec.ts` | E2E happy path |
| `frontend/tests/e2e/agents-validation.spec.ts` | E2E validation error |

**Modified files (existing)**

| Path | Change |
|---|---|
| `src/twaky/agents/atlas/agent.py` | Remove `_SYSTEM`; node calls `load_agent_config("atlas")` |
| `src/twaky/agents/chronos/agent.py` | Same shape as atlas |
| `src/twaky/agents/plume/agent.py` | Same shape |
| `src/twaky/agents/iris/agent.py` | Same shape |
| `src/twaky/daemon/atlas_daemon.py` | Start `config_listener` task in `_main_loop`; cancel on shutdown |
| `src/twaky/api/main.py` | Import + `app.include_router(agents.router, prefix="/api")` |
| `docs/api/openapi.yaml` | Add `Agent`, `AgentSummary`, `AgentUpdate` schemas + 4 endpoints |
| `frontend/src/lib/api-types.d.ts` | Regenerated via `make api-types` |
| `frontend/src/components/layout/header.tsx` | Add "Agents" nav link between Missions and Stats |
| `frontend/.env.example` | Document `NEXT_PUBLIC_TWAKY_KNOWN_MODELS` |
| `frontend/next.config.ts` | (if needed) env passthrough for `NEXT_PUBLIC_TWAKY_KNOWN_MODELS` — actually Next auto-exposes `NEXT_PUBLIC_*`, so no config change |
| `README.md` | New section "## Agent configuration" |
| `tests/agents/test_atlas_agent.py` | Update to inject registry fixture |
| `tests/agents/test_plume_agent.py` | Same |
| `tests/agents/test_chronos_agent.py` | Same |
| `tests/agents/test_iris_agent.py` | Same |

---

## Task 1: Migration + defaults module

**Files:**
- Create: `sql/006_init_agents.sh`
- Create: `src/twaky/agents/defaults.py`
- Create: `tests/agents/test_defaults.py`
- Create: `tests/sql/__init__.py` (empty)
- Create: `tests/sql/test_agent_seed_matches_defaults.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - Postgres table `agent` (columns: `id TEXT PK`, `display_name TEXT`, `role TEXT`, `system_prompt TEXT`, `model TEXT NULL`, `temperature REAL NULL`, `updated_at TIMESTAMPTZ`).
  - PG functions `notify_agent_changed()` (AFTER UPDATE) and `agent_bump_updated_at()` (BEFORE UPDATE).
  - Python `DEFAULT_PROMPTS: dict[str, str]` — 4 entries: `atlas`, `chronos`, `plume`, `iris`.

- [ ] **Step 1: Copy the four `_SYSTEM` constants**

Open each of the following files and copy the `_SYSTEM` string literal into your working notes. You will paste identical text into both the SQL seed AND the Python defaults module:

- `src/twaky/agents/atlas/agent.py` → look for `_SYSTEM = (…)` starting near line 15.
- `src/twaky/agents/chronos/agent.py` → same pattern.
- `src/twaky/agents/plume/agent.py` → near line 22.
- `src/twaky/agents/iris/agent.py` → same pattern.

Concatenate multi-line string literals into a single string (drop the parentheses; join with spaces if that's how the original was formed). Preserve punctuation, escaped quotes, and JSON examples embedded in the prompt exactly.

- [ ] **Step 2: Write `src/twaky/agents/defaults.py`**

```python
"""Original system prompts for the 4 built-in agents.

Source of truth for the /api/agents/{id}/default_prompt endpoint,
used by the frontend Reset-to-defaults button. Kept in-repo so the
reset text can never drift from what a fresh install would seed.
"""

from __future__ import annotations

DEFAULT_PROMPTS: dict[str, str] = {
    "atlas":   "<paste atlas _SYSTEM verbatim here>",
    "chronos": "<paste chronos _SYSTEM verbatim here>",
    "plume":   "<paste plume _SYSTEM verbatim here>",
    "iris":    "<paste iris _SYSTEM verbatim here>",
}

DISPLAY_NAMES: dict[str, str] = {
    "atlas":   "Atlas",
    "chronos": "Chronos",
    "plume":   "Plume",
    "iris":    "Iris",
}

ROLES: dict[str, str] = {
    "atlas":   "orchestrator",
    "chronos": "specialist",
    "plume":   "specialist",
    "iris":    "specialist",
}

__all__ = ["DEFAULT_PROMPTS", "DISPLAY_NAMES", "ROLES"]
```

- [ ] **Step 3: Write `sql/006_init_agents.sh`**

Model after `sql/004_init_mission.sh`. The seed uses **dollar-quoted string literals** (`$ATLAS$…$ATLAS$`) so you don't have to escape single quotes inside the prompts.

```bash
#!/bin/bash
# Provision the `agent` table + reload triggers, seed the 4 built-in agents.
# Runs once on first-boot volume init. For existing volumes:
#   docker exec -i twaky-pg bash /docker-entrypoint-initdb.d/006_init_agents.sh
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-twaky}" <<-'EOSQL'
    CREATE TABLE IF NOT EXISTS public.agent (
        id            TEXT PRIMARY KEY,
        display_name  TEXT NOT NULL,
        role          TEXT NOT NULL CHECK (role IN ('orchestrator', 'specialist')),
        system_prompt TEXT NOT NULL CHECK (length(system_prompt) BETWEEN 1 AND 8000),
        model         TEXT,
        temperature   REAL CHECK (temperature IS NULL OR temperature BETWEEN 0.0 AND 2.0),
        updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE OR REPLACE FUNCTION public.notify_agent_changed() RETURNS trigger AS $NOTIFYFN$
    BEGIN
      PERFORM pg_notify('agent_config_changed', NEW.id);
      RETURN NEW;
    END;
    $NOTIFYFN$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS agent_config_notify ON public.agent;
    CREATE TRIGGER agent_config_notify
      AFTER UPDATE ON public.agent
      FOR EACH ROW EXECUTE FUNCTION public.notify_agent_changed();

    CREATE OR REPLACE FUNCTION public.agent_bump_updated_at() RETURNS trigger AS $BUMPFN$
    BEGIN
      NEW.updated_at := now();
      RETURN NEW;
    END;
    $BUMPFN$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS agent_touch_updated_at ON public.agent;
    CREATE TRIGGER agent_touch_updated_at
      BEFORE UPDATE ON public.agent
      FOR EACH ROW EXECUTE FUNCTION public.agent_bump_updated_at();
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-twaky}" <<EOSQL
    INSERT INTO public.agent (id, display_name, role, system_prompt, model, temperature) VALUES
      ('atlas',   'Atlas',   'orchestrator', \$ATLAS\$$(cat <<'ATLAS_EOF'
<paste atlas _SYSTEM verbatim here — no leading/trailing whitespace stripped>
ATLAS_EOF
)\$ATLAS\$, NULL, NULL)
    ON CONFLICT (id) DO NOTHING;
EOSQL

# Repeat for chronos, plume, iris — one INSERT block each so the shell
# heredoc for each prompt stays isolated.
```

Note the two-heredoc structure: the first heredoc uses `<<-'EOSQL'` (quoted) so `$NOTIFYFN$` and friends are NOT expanded by bash — psql sees the literal `$NOTIFYFN$`. The second heredoc uses `<<EOSQL` (unquoted) so bash CAN interpolate the prompt string via `$(cat <<'ATLAS_EOF' ... ATLAS_EOF)`. Inside the psql literal, `\$ATLAS\$` escapes the bash `$` so psql receives the literal `$ATLAS$` dollar-quote marker.

Alternative (simpler if the prompts have no special chars): use one big quoted heredoc and inline the prompt strings directly with escaped single quotes. If the multi-line prompt has embedded single quotes or JSON, dollar-quoting via the two-heredoc pattern is safer.

- [ ] **Step 4: Make the shell script executable**

```bash
chmod +x sql/006_init_agents.sh
```

- [ ] **Step 5: Write `tests/agents/test_defaults.py`**

```python
"""DEFAULT_PROMPTS module surface tests (pure, no DB)."""

from twaky.agents.defaults import DEFAULT_PROMPTS, DISPLAY_NAMES, ROLES


def test_all_four_agent_ids_present():
    assert set(DEFAULT_PROMPTS.keys()) == {"atlas", "chronos", "plume", "iris"}


def test_no_empty_prompts():
    for agent_id, prompt in DEFAULT_PROMPTS.items():
        assert prompt.strip(), f"{agent_id} has an empty prompt"
        assert len(prompt) <= 8000, f"{agent_id} prompt exceeds 8000 chars"


def test_display_names_map_all_four():
    assert set(DISPLAY_NAMES.keys()) == {"atlas", "chronos", "plume", "iris"}
    assert DISPLAY_NAMES["atlas"] == "Atlas"
    assert DISPLAY_NAMES["chronos"] == "Chronos"
    assert DISPLAY_NAMES["plume"] == "Plume"
    assert DISPLAY_NAMES["iris"] == "Iris"


def test_roles():
    assert ROLES["atlas"] == "orchestrator"
    assert ROLES["chronos"] == "specialist"
    assert ROLES["plume"] == "specialist"
    assert ROLES["iris"] == "specialist"
```

- [ ] **Step 6: Write `tests/sql/test_agent_seed_matches_defaults.py`**

```python
"""The seed script INSERTs must carry exactly the same prompt text as DEFAULT_PROMPTS.

Catches the drift bug: someone edits _SYSTEM (which no longer exists —
the module was refactored in T7), forgets to touch defaults.py + the SQL
seed. If those two ever diverge, the reset button lies about defaults.
"""

import re
from pathlib import Path

from twaky.agents.defaults import DEFAULT_PROMPTS

SEED_FILE = Path(__file__).parents[2] / "sql" / "006_init_agents.sh"


def _extract_prompts_from_seed() -> dict[str, str]:
    """Parse INSERTed prompts by locating dollar-quote markers per agent."""
    content = SEED_FILE.read_text()
    prompts = {}
    for agent_id in ("atlas", "chronos", "plume", "iris"):
        # The heredoc pattern places the prompt between the two ATLAS_EOF (etc.) markers.
        marker = f"{agent_id.upper()}_EOF"
        pattern = rf"<<'{marker}'\n(.*?)\n{marker}"
        match = re.search(pattern, content, re.DOTALL)
        assert match is not None, f"no heredoc block for {agent_id} in {SEED_FILE}"
        prompts[agent_id] = match.group(1)
    return prompts


def test_seed_prompts_match_defaults():
    seed = _extract_prompts_from_seed()
    for agent_id, expected in DEFAULT_PROMPTS.items():
        assert seed[agent_id] == expected, (
            f"{agent_id}: seed script prompt diverges from defaults.py.\n"
            f"  seed: {seed[agent_id][:80]!r}\n"
            f"  defaults: {expected[:80]!r}"
        )


def test_seed_script_is_executable():
    assert SEED_FILE.stat().st_mode & 0o111, f"{SEED_FILE} is not executable"
```

- [ ] **Step 7: Run the tests**

```bash
uv run pytest tests/agents/test_defaults.py tests/sql/test_agent_seed_matches_defaults.py -v
```

Expected: 5 tests pass.

- [ ] **Step 8: Run all Python gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src/ && uv run pytest -q
```

Expected: all green. Prior baseline (194 passed / 32 skipped) plus 5 new tests.

- [ ] **Step 9: Commit**

```bash
git add sql/006_init_agents.sh src/twaky/agents/defaults.py tests/agents/test_defaults.py tests/sql/
git commit -m "feat(agents): seed migration + DEFAULT_PROMPTS module"
```

---

## Task 2: agents_config repository

**Files:**
- Create: `src/twaky/agents_config/__init__.py` (empty)
- Create: `src/twaky/agents_config/models.py`
- Create: `src/twaky/agents_config/repository.py`
- Create: `tests/agents_config/__init__.py` (empty)
- Create: `tests/agents_config/test_repository.py`

**Interfaces:**
- Consumes: `twaky.db.get_pool()` (existing), Postgres `agent` table (from T1).
- Produces:
  - `AgentConfig` frozen dataclass with fields `id: str, display_name: str, role: str, system_prompt: str, model: str | None, temperature: float | None, updated_at: datetime`.
  - `AgentConfigNotFound` exception class.
  - `list_all() -> list[AgentConfig]` — returns all 4 rows ordered by id.
  - `get(agent_id: str) -> AgentConfig | None`.
  - `update(agent_id: str, patch: dict) -> AgentConfig` — applies partial update, raises `AgentConfigNotFound` if row missing. Returns fresh row post-update.

- [ ] **Step 1: Write `src/twaky/agents_config/models.py`**

```python
"""Dataclass carried between DB, service, and (via mapping) API+registry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AgentConfig:
    id: str
    display_name: str
    role: str
    system_prompt: str
    model: str | None
    temperature: float | None
    updated_at: datetime


__all__ = ["AgentConfig"]
```

- [ ] **Step 2: Write `src/twaky/agents_config/__init__.py`**

Empty file:

```python
```

- [ ] **Step 3: Write `src/twaky/agents_config/repository.py`**

```python
"""psycopg3 CRUD for the `agent` table.

Raw SQL, matching src/twaky/missions/repository.py convention.
"""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row

from twaky.agents_config.models import AgentConfig
from twaky.db import get_pool


class AgentConfigNotFound(Exception):
    pass


def _row_to_config(row: dict[str, Any]) -> AgentConfig:
    return AgentConfig(
        id=row["id"],
        display_name=row["display_name"],
        role=row["role"],
        system_prompt=row["system_prompt"],
        model=row["model"],
        temperature=row["temperature"],
        updated_at=row["updated_at"],
    )


def list_all() -> list[AgentConfig]:
    """Return all agent rows, ordered by id (stable presentation)."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM agent ORDER BY id")
        rows = cur.fetchall()
    return [_row_to_config(r) for r in rows]


def get(agent_id: str) -> AgentConfig | None:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM agent WHERE id = %s", (agent_id,))
        row = cur.fetchone()
    return _row_to_config(row) if row else None


def update(agent_id: str, patch: dict[str, Any]) -> AgentConfig:
    """Apply partial update. Keys accepted: system_prompt, model, temperature.

    Raises AgentConfigNotFound if the row doesn't exist.
    Returns the fresh row after the DB trigger bumps updated_at.
    """
    if not patch:
        raise ValueError("empty patch")

    allowed = {"system_prompt", "model", "temperature"}
    bad = set(patch) - allowed
    if bad:
        raise ValueError(f"unknown fields: {sorted(bad)}")

    sets = [f"{k} = %s" for k in patch]
    params = [*patch.values(), agent_id]
    sql = f"UPDATE agent SET {', '.join(sets)} WHERE id = %s"

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        if cur.rowcount == 0:
            raise AgentConfigNotFound(f"agent {agent_id!r} not found")
        conn.commit()

    fresh = get(agent_id)
    assert fresh is not None  # just wrote it
    return fresh


__all__ = ["AgentConfigNotFound", "get", "list_all", "update"]
```

- [ ] **Step 4: Write `tests/agents_config/test_repository.py`**

```python
"""Repository CRUD unit tests — real Postgres via the shared fixture."""

from __future__ import annotations

import pytest

from twaky.agents_config import repository
from twaky.agents_config.models import AgentConfig
from twaky.agents_config.repository import AgentConfigNotFound

pytestmark = pytest.mark.integration  # marks tests needing real DB


class TestListAll:
    def test_returns_four_rows_sorted_by_id(self):
        rows = repository.list_all()
        ids = [r.id for r in rows]
        assert ids == ["atlas", "chronos", "iris", "plume"]  # alphabetical

    def test_all_rows_are_agent_config_instances(self):
        rows = repository.list_all()
        assert all(isinstance(r, AgentConfig) for r in rows)


class TestGet:
    def test_get_atlas_returns_row(self):
        cfg = repository.get("atlas")
        assert cfg is not None
        assert cfg.id == "atlas"
        assert cfg.display_name == "Atlas"
        assert cfg.role == "orchestrator"
        assert cfg.system_prompt  # non-empty

    def test_get_unknown_returns_none(self):
        assert repository.get("zeus") is None


class TestUpdate:
    def test_update_temperature(self):
        original = repository.get("plume")
        assert original is not None
        try:
            fresh = repository.update("plume", {"temperature": 0.3})
            assert fresh.temperature == pytest.approx(0.3)
            assert fresh.updated_at > original.updated_at
        finally:
            repository.update("plume", {"temperature": original.temperature})

    def test_update_model_to_null(self):
        original = repository.get("plume")
        assert original is not None
        try:
            fresh = repository.update("plume", {"model": "openai/gpt-4o"})
            assert fresh.model == "openai/gpt-4o"
            fresh = repository.update("plume", {"model": None})
            assert fresh.model is None
        finally:
            repository.update("plume", {"model": original.model})

    def test_update_unknown_agent_raises(self):
        with pytest.raises(AgentConfigNotFound):
            repository.update("zeus", {"temperature": 0.5})

    def test_update_empty_patch_raises_value_error(self):
        with pytest.raises(ValueError, match="empty patch"):
            repository.update("plume", {})

    def test_update_unknown_field_raises_value_error(self):
        with pytest.raises(ValueError, match="unknown fields"):
            repository.update("plume", {"model": "x", "wibble": 1})
```

Add the `integration` mark to `pyproject.toml` if not already there (check the existing markers section; sub-project 2 tests likely established it).

- [ ] **Step 5: Run the tests**

```bash
uv run pytest tests/agents_config/ -v
```

Expected: 8 tests pass against the running `twaky-pg` container (`docker compose up -d twaky-pg` first if not up).

- [ ] **Step 6: Run all Python gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src/ && uv run pytest -q
```

- [ ] **Step 7: Commit**

```bash
git add src/twaky/agents_config/ tests/agents_config/
git commit -m "feat(agents_config): repository CRUD + tests"
```

---

## Task 3: agents_config service

**Files:**
- Create: `src/twaky/agents_config/service.py`
- Create: `tests/agents_config/test_service.py`

**Interfaces:**
- Consumes: `AgentConfig` (T2), `settings.model` (existing).
- Produces:
  - `effective_model(cfg: AgentConfig) -> str` — returns `cfg.model or settings.model`.
  - `validate_patch(patch: dict) -> dict` — returns the same dict on success; raises `ValidationError` (subclass of `ValueError`) on failure. Rules:
    - Empty dict → raises "at least one field required".
    - `system_prompt`: str, 1-8000 chars after strip. Empty/whitespace → raises.
    - `temperature`: None OR float in [0.0, 2.0]. Out of range → raises.
    - `model`: None OR non-empty string (whitespace-stripped). Empty → raises.

- [ ] **Step 1: Write `src/twaky/agents_config/service.py`**

```python
"""Business logic layer above the repository."""

from __future__ import annotations

from typing import Any

from twaky.agents_config.models import AgentConfig
from twaky.config import settings


class ValidationError(ValueError):
    """Raised when a patch payload violates constraints."""

    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


def effective_model(cfg: AgentConfig) -> str:
    """Resolved model — either the row's override or the daemon-side default."""
    return cfg.model or settings.model


def validate_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Return the normalized patch on success; raise ValidationError on failure."""
    if not patch:
        raise ValidationError("_body", "at least one field required")

    allowed = {"system_prompt", "model", "temperature"}
    unknown = set(patch) - allowed
    if unknown:
        raise ValidationError(sorted(unknown)[0], "unknown field")

    normalized: dict[str, Any] = {}

    if "system_prompt" in patch:
        sp = patch["system_prompt"]
        if not isinstance(sp, str):
            raise ValidationError("system_prompt", "must be a string")
        sp = sp.strip()
        if not sp:
            raise ValidationError("system_prompt", "must not be empty")
        if len(sp) > 8000:
            raise ValidationError("system_prompt", "must be at most 8000 characters")
        normalized["system_prompt"] = sp

    if "temperature" in patch:
        temp = patch["temperature"]
        if temp is None:
            normalized["temperature"] = None
        else:
            if not isinstance(temp, (int, float)) or isinstance(temp, bool):
                raise ValidationError("temperature", "must be a number or null")
            if temp < 0.0 or temp > 2.0:
                raise ValidationError("temperature", "must be between 0.0 and 2.0")
            normalized["temperature"] = float(temp)

    if "model" in patch:
        model = patch["model"]
        if model is None:
            normalized["model"] = None
        else:
            if not isinstance(model, str):
                raise ValidationError("model", "must be a string or null")
            stripped = model.strip()
            if not stripped:
                raise ValidationError("model", "must not be empty")
            normalized["model"] = stripped

    return normalized


__all__ = ["ValidationError", "effective_model", "validate_patch"]
```

- [ ] **Step 2: Write `tests/agents_config/test_service.py`**

```python
"""Pure validation + effective_model tests (no DB)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from twaky.agents_config.models import AgentConfig
from twaky.agents_config.service import ValidationError, effective_model, validate_patch


def _cfg(model: str | None = None) -> AgentConfig:
    return AgentConfig(
        id="plume",
        display_name="Plume",
        role="specialist",
        system_prompt="hi",
        model=model,
        temperature=None,
        updated_at=datetime.now(UTC),
    )


class TestEffectiveModel:
    def test_returns_row_model_when_set(self):
        assert effective_model(_cfg(model="openai/gpt-4o")) == "openai/gpt-4o"

    def test_falls_back_to_settings_when_null(self, monkeypatch):
        from twaky import config as _cfg_mod
        monkeypatch.setattr(_cfg_mod.settings, "model", "sentinel-default")
        assert effective_model(_cfg(model=None)) == "sentinel-default"


class TestValidatePatchSystemPrompt:
    def test_ok(self):
        out = validate_patch({"system_prompt": "  hello world  "})
        assert out == {"system_prompt": "hello world"}

    def test_empty_raises(self):
        with pytest.raises(ValidationError) as exc:
            validate_patch({"system_prompt": "   "})
        assert exc.value.field == "system_prompt"

    def test_too_long_raises(self):
        with pytest.raises(ValidationError):
            validate_patch({"system_prompt": "x" * 8001})

    def test_non_string_raises(self):
        with pytest.raises(ValidationError):
            validate_patch({"system_prompt": 42})


class TestValidatePatchTemperature:
    def test_ok_low(self):
        assert validate_patch({"temperature": 0.0}) == {"temperature": 0.0}

    def test_ok_high(self):
        assert validate_patch({"temperature": 2.0}) == {"temperature": 2.0}

    def test_ok_null(self):
        assert validate_patch({"temperature": None}) == {"temperature": None}

    def test_below_zero_raises(self):
        with pytest.raises(ValidationError):
            validate_patch({"temperature": -0.1})

    def test_above_two_raises(self):
        with pytest.raises(ValidationError):
            validate_patch({"temperature": 2.01})

    def test_bool_raises(self):
        with pytest.raises(ValidationError):
            validate_patch({"temperature": True})


class TestValidatePatchModel:
    def test_ok_string(self):
        assert validate_patch({"model": " openai/gpt-4o "}) == {"model": "openai/gpt-4o"}

    def test_ok_null(self):
        assert validate_patch({"model": None}) == {"model": None}

    def test_empty_string_raises(self):
        with pytest.raises(ValidationError):
            validate_patch({"model": "   "})


class TestValidatePatchBody:
    def test_empty_body_raises(self):
        with pytest.raises(ValidationError) as exc:
            validate_patch({})
        assert "at least one field required" in exc.value.message

    def test_unknown_field_raises(self):
        with pytest.raises(ValidationError):
            validate_patch({"tools": ["read_email"]})

    def test_multi_field_patch_ok(self):
        out = validate_patch({"system_prompt": "hi", "temperature": 0.5, "model": None})
        assert out == {"system_prompt": "hi", "temperature": 0.5, "model": None}
```

- [ ] **Step 3: Run the tests**

```bash
uv run pytest tests/agents_config/test_service.py -v
```

Expected: 17 tests pass.

- [ ] **Step 4: Full gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src/ && uv run pytest -q
```

- [ ] **Step 5: Commit**

```bash
git add src/twaky/agents_config/service.py tests/agents_config/test_service.py
git commit -m "feat(agents_config): validation service"
```

---

## Task 4: Registry cache

**Files:**
- Create: `src/twaky/agents/registry.py`
- Create: `tests/agents/test_registry.py`

**Interfaces:**
- Consumes: `agents_config.repository.get()` (T2), `agents_config.models.AgentConfig` (T2), `agents.defaults.DEFAULT_PROMPTS/DISPLAY_NAMES/ROLES` (T1).
- Produces:
  - `load_agent_config(agent_id: str) -> AgentConfig` — cache-first read.
  - `invalidate(agent_id: str) -> None` — pops one cache entry.
  - `invalidate_all() -> None` — clears whole cache (used at daemon boot for safety).
  - `AgentConfigMissing(Exception)` — raised internally only, converted to defaults-based fallback config.

- [ ] **Step 1: Write the failing test**

Create `tests/agents/test_registry.py`:

```python
"""In-process cache + invalidate + fallback tests.

Uses monkeypatching on the repository layer so we don't need a real DB
for the cache-behaviour tests. A separate test file
(test_registry_notify_trigger.py, T5's integration) proves the
end-to-end NOTIFY loop.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from twaky.agents import registry
from twaky.agents_config.models import AgentConfig
from twaky.agents_config.repository import AgentConfigNotFound


def _cfg(agent_id: str = "plume", model: str | None = None) -> AgentConfig:
    return AgentConfig(
        id=agent_id,
        display_name=agent_id.capitalize(),
        role="specialist",
        system_prompt="system",
        model=model,
        temperature=None,
        updated_at=datetime.now(UTC),
    )


@pytest.fixture(autouse=True)
def _clean_cache():
    registry.invalidate_all()
    yield
    registry.invalidate_all()


class TestLoadAgentConfig:
    def test_cold_miss_loads_from_db(self):
        c = _cfg("plume", model="openai/gpt-4o")
        with patch("twaky.agents.registry._repository_get", return_value=c) as g:
            result = registry.load_agent_config("plume")
            assert result.model == "openai/gpt-4o"
            g.assert_called_once_with("plume")

    def test_warm_hit_does_not_call_db(self):
        c = _cfg("plume")
        with patch("twaky.agents.registry._repository_get", return_value=c) as g:
            registry.load_agent_config("plume")
            registry.load_agent_config("plume")
            registry.load_agent_config("plume")
        assert g.call_count == 1

    def test_invalidate_forces_reload(self):
        c1 = _cfg("plume", model="a")
        c2 = _cfg("plume", model="b")
        with patch("twaky.agents.registry._repository_get", side_effect=[c1, c2]) as g:
            first = registry.load_agent_config("plume")
            assert first.model == "a"
            registry.invalidate("plume")
            second = registry.load_agent_config("plume")
            assert second.model == "b"
        assert g.call_count == 2

    def test_invalidate_only_affects_one_key(self):
        with patch("twaky.agents.registry._repository_get") as g:
            g.side_effect = lambda aid: _cfg(aid)
            registry.load_agent_config("plume")
            registry.load_agent_config("chronos")
            g.reset_mock()
            registry.invalidate("plume")
            registry.load_agent_config("plume")
            registry.load_agent_config("chronos")
        assert g.call_count == 1  # only plume reloaded

    def test_invalidate_all_clears_everything(self):
        with patch("twaky.agents.registry._repository_get") as g:
            g.side_effect = lambda aid: _cfg(aid)
            registry.load_agent_config("plume")
            registry.load_agent_config("chronos")
            g.reset_mock()
            registry.invalidate_all()
            registry.load_agent_config("plume")
            registry.load_agent_config("chronos")
        assert g.call_count == 2


class TestFallbackOnMissingRow:
    def test_missing_row_falls_back_to_defaults(self):
        # DB row somehow absent (bad seed, manual delete) — daemon must
        # still produce a working config using the DEFAULT_PROMPTS module.
        with patch("twaky.agents.registry._repository_get", return_value=None):
            cfg = registry.load_agent_config("plume")
        assert cfg.id == "plume"
        assert cfg.system_prompt  # non-empty — pulled from DEFAULT_PROMPTS
        assert cfg.model is None
        assert cfg.temperature is None

    def test_unknown_agent_id_raises(self):
        with patch("twaky.agents.registry._repository_get", return_value=None):
            with pytest.raises(registry.AgentConfigMissing):
                registry.load_agent_config("zeus")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/agents/test_registry.py -v
```

Expected: import failure — `twaky.agents.registry` doesn't exist yet.

- [ ] **Step 3: Write `src/twaky/agents/registry.py`**

```python
"""Thread-safe in-process cache for agent configuration.

The cache is populated on cold read and cleared by config_listener.py
whenever Postgres NOTIFYs agent_config_changed. A row that is missing
from the DB falls back to defaults.py so the daemon never bricks on a
misconfigured install.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from twaky.agents.defaults import DEFAULT_PROMPTS, DISPLAY_NAMES, ROLES
from twaky.agents_config import repository
from twaky.agents_config.models import AgentConfig


class AgentConfigMissing(Exception):
    """Raised when an unknown agent id is requested (not in DEFAULT_PROMPTS)."""


_cache: dict[str, AgentConfig] = {}
_lock = threading.Lock()


def _repository_get(agent_id: str) -> AgentConfig | None:
    """Indirection kept for test monkeypatching."""
    return repository.get(agent_id)


def _fallback(agent_id: str) -> AgentConfig:
    if agent_id not in DEFAULT_PROMPTS:
        raise AgentConfigMissing(f"unknown agent id {agent_id!r}")
    return AgentConfig(
        id=agent_id,
        display_name=DISPLAY_NAMES[agent_id],
        role=ROLES[agent_id],
        system_prompt=DEFAULT_PROMPTS[agent_id],
        model=None,
        temperature=None,
        updated_at=datetime.now(UTC),
    )


def load_agent_config(agent_id: str) -> AgentConfig:
    """Fetch config, cache-first. Falls back to defaults on missing DB row."""
    with _lock:
        cached = _cache.get(agent_id)
        if cached is not None:
            return cached

    fetched = _repository_get(agent_id)
    cfg = fetched if fetched is not None else _fallback(agent_id)

    with _lock:
        _cache[agent_id] = cfg
    return cfg


def invalidate(agent_id: str) -> None:
    """Drop a single cache entry. Next load_agent_config() will re-fetch."""
    with _lock:
        _cache.pop(agent_id, None)


def invalidate_all() -> None:
    """Drop every cache entry. Called at daemon boot for a clean slate."""
    with _lock:
        _cache.clear()


__all__ = ["AgentConfigMissing", "invalidate", "invalidate_all", "load_agent_config"]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/agents/test_registry.py -v
```

Expected: 8 tests pass.

- [ ] **Step 5: Full gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src/ && uv run pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add src/twaky/agents/registry.py tests/agents/test_registry.py
git commit -m "feat(agents): thread-safe registry cache with defaults fallback"
```

---

## Task 5: Config listener + integration test

**Files:**
- Create: `src/twaky/agents/config_listener.py`
- Create: `tests/integration/test_agent_config_listener.py`

**Interfaces:**
- Consumes: `twaky.daemon.notify.listen()` (existing async iterator over channels), `twaky.agents.registry.invalidate()` (T4), `settings.pg_dsn` (existing).
- Produces:
  - `async def run(stop_event: asyncio.Event) -> None` — long-running task; exits when `stop_event.is_set()`.

- [ ] **Step 1: Write `src/twaky/agents/config_listener.py`**

```python
"""LISTEN for `agent_config_changed` NOTIFYs and invalidate the registry cache.

The daemon spawns this as an asyncio task alongside its mission scheduler.
Reuses the shared listen() helper from twaky.daemon.notify — same shape as
mission_declared / mission_resumed subscribers.
"""

from __future__ import annotations

import asyncio
import logging

from twaky.agents import registry
from twaky.config import settings
from twaky.daemon.notify import listen

log = logging.getLogger("twaky.agents.config_listener")


async def run(stop_event: asyncio.Event) -> None:
    """Long-running task: LISTEN agent_config_changed, invalidate on payload."""
    log.info("agent config listener starting")
    try:
        async for ch, payload in listen(["agent_config_changed"], settings.pg_dsn):
            if stop_event.is_set():
                return
            if ch == "agent_config_changed":
                log.info("invalidating agent cache for %s", payload)
                registry.invalidate(payload)
    except asyncio.CancelledError:
        log.info("agent config listener cancelled")
        raise
    except Exception:
        log.exception("agent config listener crashed")
        raise


__all__ = ["run"]
```

- [ ] **Step 2: Write the integration test**

Create `tests/integration/test_agent_config_listener.py`:

```python
"""End-to-end: real Postgres UPDATE fires trigger → NOTIFY → cache invalidates.

Requires the twaky-pg container running and the T1 migration applied
(check: `docker compose ps twaky-pg` shows healthy).
"""

from __future__ import annotations

import asyncio

import pytest

from twaky.agents import config_listener, registry
from twaky.agents_config import repository

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_update_invalidates_cache_within_one_second():
    # Prime the cache.
    registry.invalidate_all()
    original = repository.get("plume")
    assert original is not None
    cached = registry.load_agent_config("plume")
    assert cached.temperature == original.temperature

    stop_event = asyncio.Event()
    listener_task = asyncio.create_task(config_listener.run(stop_event))

    try:
        # Give the LISTEN a moment to attach.
        await asyncio.sleep(0.5)

        # UPDATE the row (in the caller's connection — the trigger fires).
        new_temp = 0.42 if original.temperature != 0.42 else 0.43
        repository.update("plume", {"temperature": new_temp})

        # Wait for cache invalidation. The listener has ~1s to react.
        async def _wait_for_invalidation():
            for _ in range(20):  # 20 × 100ms = 2s max
                # A cleared cache means next load_agent_config re-reads DB.
                # Detect by locking + peeking at the internal cache dict.
                with registry._lock:
                    if "plume" not in registry._cache:
                        return True
                await asyncio.sleep(0.1)
            return False

        assert await _wait_for_invalidation(), (
            "cache was not invalidated within 2 seconds"
        )
    finally:
        stop_event.set()
        listener_task.cancel()
        try:
            await listener_task
        except (asyncio.CancelledError, Exception):
            pass
        # Restore the DB row.
        repository.update("plume", {"temperature": original.temperature})
        registry.invalidate_all()
```

- [ ] **Step 3: Run the integration test**

```bash
docker compose up -d twaky-pg  # make sure it's up
uv run pytest tests/integration/test_agent_config_listener.py -v
```

Expected: 1 test passes.

- [ ] **Step 4: Full gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src/ && uv run pytest -q
```

- [ ] **Step 5: Commit**

```bash
git add src/twaky/agents/config_listener.py tests/integration/test_agent_config_listener.py
git commit -m "feat(agents): async LISTEN loop invalidates registry cache on NOTIFY"
```

---

## Task 6: Wire listener into atlas daemon

**Files:**
- Modify: `src/twaky/daemon/atlas_daemon.py`

**Interfaces:**
- Consumes: `twaky.agents.config_listener.run()` (T5), `twaky.agents.registry.invalidate_all()` (T4).
- Produces: no new public interface — behavioural change only.

**Change summary:** In `_main_loop()`, alongside the existing `_listener` (mission_declared/resumed), `_periodic_sweep`, and `_heart` tasks, add a fourth task running `config_listener.run(stop)`. On shutdown, cancel it along with the others. At the very start of the run() entrypoint, call `registry.invalidate_all()` to ensure a clean slate after restart.

- [ ] **Step 1: Read the current `_main_loop` shape**

Open `src/twaky/daemon/atlas_daemon.py` and locate `async def _main_loop():` (around line 311). Note the three existing tasks: `_listener` (line ~326-340), `_periodic_sweep`, and `_heart`. Each is started with `asyncio.create_task(...)`, cancelled on shutdown, and named accordingly (`listener_task`, `sweep_task`, `heart_task`).

- [ ] **Step 2: Add the import at the top of the file**

Add near the existing `from twaky.agents.atlas.agent import build_atlas_agent`:

```python
from twaky.agents import config_listener, registry
```

- [ ] **Step 3: Add the invalidate_all call in `run()`**

Modify the `run()` function (around line 378):

```python
def run() -> None:
    """Entry point for `twaky atlas run`."""
    log.info("atlas daemon booting", owner=settings.twaky_owner_email)
    setup_checkpointer_tables()
    registry.invalidate_all()  # clean cache slate after restart
    bump()
    asyncio.run(_main_loop())
    log.info("atlas daemon stopped")
```

- [ ] **Step 4: Add the config listener task in `_main_loop`**

After `heart_task = asyncio.create_task(_heart())` and before `await stop.wait()`, add:

```python
    config_task = asyncio.create_task(config_listener.run(stop))
```

Then extend the cancellation block:

```python
    await stop.wait()
    listener_task.cancel()
    sweep_task.cancel()
    heart_task.cancel()
    config_task.cancel()
```

- [ ] **Step 5: Write a small smoke test**

Create `tests/daemon/test_atlas_daemon_config_listener.py`:

```python
"""Smoke test: daemon's _main_loop starts the config listener task.

Doesn't actually run missions — just verifies that a boot-and-shutdown
cycle creates AND cancels a config_task without exception.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_main_loop_starts_and_cancels_config_listener():
    from twaky.daemon import atlas_daemon

    # Stub the components we don't want to actually run.
    with (
        patch.object(atlas_daemon, "_recover_and_schedule", return_value=[]),
        patch.object(atlas_daemon, "_schedule_declared_loop"),
        patch("twaky.daemon.atlas_daemon.bump"),
        patch("twaky.daemon.atlas_daemon.listen") as fake_listen,
    ):
        # listen() is called by BOTH the mission listener and the config
        # listener. Return an empty async iterator so both loops idle.
        async def _empty():
            if False:
                yield None  # never executes; typing helper

        fake_listen.return_value = _empty()

        # Run _main_loop with a rapid shutdown.
        loop_task = asyncio.create_task(atlas_daemon._main_loop())
        await asyncio.sleep(0.2)  # let tasks spawn
        # Fake SIGTERM via the stop event handled inside _main_loop:
        # simplest way is to send the actual signal.
        import os
        import signal
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(loop_task, timeout=5.0)
```

- [ ] **Step 6: Run the smoke test**

```bash
uv run pytest tests/daemon/test_atlas_daemon_config_listener.py -v
```

Expected: passes without hanging.

- [ ] **Step 7: Full gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src/ && uv run pytest -q
```

- [ ] **Step 8: Commit**

```bash
git add src/twaky/daemon/atlas_daemon.py tests/daemon/test_atlas_daemon_config_listener.py
git commit -m "feat(daemon): wire agent config_listener into atlas main loop"
```

---

## Task 7: Refactor 4 agent modules to use registry

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
- Consumes: `twaky.agents.registry.load_agent_config()` (T4), `AgentConfig` (T2).
- Produces: no interface change — refactor-in-place.

**Change summary per file:**
- Remove `_SYSTEM = "..."` module-level constant.
- Remove `_make_llm()` (or refactor into `_make_llm(cfg)` accepting an `AgentConfig`).
- In each node function, call `cfg = load_agent_config("<id>")` first, then pass `cfg.system_prompt` and `cfg.temperature` down through `SystemMessage` and `ChatLiteLLM(**kwargs)` respectively.

- [ ] **Step 1: Refactor `src/twaky/agents/plume/agent.py`**

Full new file (replace the current file):

```python
"""Plume sub-agent StateGraph."""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_litellm import ChatLiteLLM
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from twaky.agents.plume.tools import (
    draft_reply,
    list_recent_emails,
    read_email,
    search_emails,
)
from twaky.agents.registry import load_agent_config
from twaky.agents.state import AgentState
from twaky.agents_config.models import AgentConfig
from twaky.config import settings

TOOLS = [list_recent_emails, read_email, search_emails, draft_reply]


def _make_llm(cfg: AgentConfig) -> BaseChatModel:
    kwargs: dict = {
        "model": cfg.model or settings.model,
        "api_base": settings.litellm_api_base,
    }
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


def build_plume_agent():
    g = StateGraph(AgentState)
    g.add_node("agent", _agent_node)
    g.add_node("tools", ToolNode(TOOLS))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    g.add_edge("tools", "agent")
    return g.compile()


__all__ = ["build_plume_agent"]
```

- [ ] **Step 2: Refactor `src/twaky/agents/atlas/agent.py`**

Atlas has two callers of `_SYSTEM`/`_make_llm`: the `_atlas_node` function and (only `_make_llm`) nothing else. Replace fully:

```python
"""Atlas orchestrator StateGraph — Supervisor pattern."""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_litellm import ChatLiteLLM
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from twaky.agents.atlas.tools import ALL_TOOLS, FINISH_MARKER
from twaky.agents.registry import load_agent_config
from twaky.agents.state import AtlasState
from twaky.agents_config.models import AgentConfig
from twaky.config import settings


def _make_llm(cfg: AgentConfig) -> BaseChatModel:
    kwargs: dict = {
        "model": cfg.model or settings.model,
        "api_base": settings.litellm_api_base,
    }
    if cfg.temperature is not None:
        kwargs["temperature"] = cfg.temperature
    return ChatLiteLLM(**kwargs)


def _atlas_node(state: AtlasState):
    cfg = load_agent_config("atlas")
    llm = _make_llm(cfg).bind_tools(ALL_TOOLS)
    messages = state.get("messages", [])
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=cfg.system_prompt), *messages]
    ai = llm.invoke(messages)
    step_count = state.get("step_count", 0) + 1
    call_tokens: int = 0
    usage = getattr(ai, "usage_metadata", None)
    if isinstance(usage, dict):
        call_tokens = usage.get("total_tokens", 0) or 0
    total_tokens = state.get("total_tokens", 0) + call_tokens
    return {"messages": [ai], "step_count": step_count, "total_tokens": total_tokens}


def _route(state: AtlasState):
    if state.get("step_count", 0) > settings.atlas_max_steps:
        return END
    msgs = state.get("messages", [])
    if not msgs:
        return END
    last = msgs[-1]
    if getattr(last, "type", "") == "tool":
        content = getattr(last, "content", "") or ""
        if isinstance(content, str) and content.startswith(FINISH_MARKER):
            return END
        return "atlas"
    if getattr(last, "tool_calls", None):
        return "tools"
    return END


def build_atlas_agent(checkpointer=None):
    g = StateGraph(AtlasState)
    g.add_node("atlas", _atlas_node)
    g.add_node("tools", ToolNode(ALL_TOOLS))
    g.add_edge(START, "atlas")
    g.add_conditional_edges("atlas", _route, {"tools": "tools", END: END})
    g.add_conditional_edges("tools", _route, {"atlas": "atlas", END: END})
    return g.compile(checkpointer=checkpointer)


__all__ = ["build_atlas_agent"]
```

- [ ] **Step 3: Refactor `src/twaky/agents/chronos/agent.py`**

Apply the same shape as plume. Read the current file first to preserve the exact structure (TOOLS list, node function name, graph edges, `build_chronos_agent()` signature). Substitute:

- Remove `_SYSTEM` constant.
- `_make_llm()` → `_make_llm(cfg: AgentConfig)` per the plume template.
- In each node function: `cfg = load_agent_config("chronos")` first line, use `cfg.system_prompt` in SystemMessage.

- [ ] **Step 4: Refactor `src/twaky/agents/iris/agent.py`**

Same pattern as chronos. Substitute `"iris"` in the `load_agent_config` call.

- [ ] **Step 5: Update the four existing test files**

Each of `tests/agents/test_atlas_agent.py`, `test_chronos_agent.py`, `test_plume_agent.py`, `test_iris_agent.py` currently patches or stubs `_SYSTEM`/`_make_llm`. They now need to inject a fake `AgentConfig` via the registry.

Add a shared fixture in `tests/agents/_fakes.py` (existing file — read it first to see its current shape, then extend):

```python
# In tests/agents/_fakes.py, add:

from datetime import UTC, datetime
from unittest.mock import patch

from twaky.agents_config.models import AgentConfig


def make_fake_config(
    agent_id: str,
    system_prompt: str = "TEST SYSTEM PROMPT",
    model: str | None = None,
    temperature: float | None = None,
) -> AgentConfig:
    return AgentConfig(
        id=agent_id,
        display_name=agent_id.capitalize(),
        role="orchestrator" if agent_id == "atlas" else "specialist",
        system_prompt=system_prompt,
        model=model,
        temperature=temperature,
        updated_at=datetime.now(UTC),
    )


def stub_registry_for(agent_id: str, **cfg_kwargs):
    """Returns a context manager that stubs load_agent_config for one agent."""
    fake = make_fake_config(agent_id, **cfg_kwargs)
    return patch("twaky.agents.registry.load_agent_config", return_value=fake)
```

Then in each `test_<agent>_agent.py`, wherever the test invokes the node function or `build_<agent>_agent`, wrap the call:

```python
from tests.agents._fakes import stub_registry_for

def test_plume_node_uses_system_prompt():
    with stub_registry_for("plume", system_prompt="Custom test prompt"):
        # ...invoke the graph as before...
```

Read each test file and identify every test that depends on `_SYSTEM` behavior — wrap each in `stub_registry_for(...)` and adjust assertions if they check message contents.

- [ ] **Step 6: Run the agent tests**

```bash
uv run pytest tests/agents/ -v
```

Expected: all agent tests pass. If any fail because they inspected `_SYSTEM` directly (e.g., `from twaky.agents.plume.agent import _SYSTEM`), remove that import and rewrite the assertion to check the SystemMessage passed to the fake LLM instead.

- [ ] **Step 7: Full gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src/ && uv run pytest -q
```

- [ ] **Step 8: Commit**

```bash
git add src/twaky/agents/atlas/agent.py src/twaky/agents/chronos/agent.py src/twaky/agents/plume/agent.py src/twaky/agents/iris/agent.py tests/agents/
git commit -m "refactor(agents): pull system prompt + model + temperature from registry"
```

---

## Task 8: API — GET endpoints

**Files:**
- Create: `src/twaky/api/schemas/__init__.py` (if not present)
- Create: `src/twaky/api/schemas/agents.py`
- Create: `src/twaky/api/routers/agents.py`
- Modify: `src/twaky/api/main.py` — register the router
- Create: `tests/api/routers/test_agents.py`

**Interfaces:**
- Consumes: `require_owner` (existing), `agents_config.repository` (T2), `agents_config.service.effective_model` (T3), `agents.defaults.DEFAULT_PROMPTS` (T1).
- Produces: 3 GET endpoints:
  - `GET /api/agents` → `list[AgentSummary]`
  - `GET /api/agents/{id}` → `Agent`
  - `GET /api/agents/{id}/default_prompt` → `{"system_prompt": str}`

- [ ] **Step 1: Write `src/twaky/api/schemas/agents.py`**

```python
"""Pydantic models for the /api/agents surface."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Agent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    display_name: str
    role: Literal["orchestrator", "specialist"]
    system_prompt: str
    model: str | None
    temperature: float | None
    effective_model: str
    updated_at: datetime


class AgentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    display_name: str
    role: Literal["orchestrator", "specialist"]
    model: str | None
    temperature: float | None
    effective_model: str
    updated_at: datetime


class AgentUpdate(BaseModel):
    """Partial update. All fields optional; empty body → 422 (see router)."""
    model_config = ConfigDict(extra="forbid")
    system_prompt: str | None = Field(default=None, min_length=1, max_length=8000)
    model: str | None = Field(default=None)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class DefaultPromptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    system_prompt: str


__all__ = ["Agent", "AgentSummary", "AgentUpdate", "DefaultPromptResponse"]
```

- [ ] **Step 2: Write `src/twaky/api/routers/agents.py` (GET only)**

```python
"""Agent configuration routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from twaky.agents.defaults import DEFAULT_PROMPTS
from twaky.agents_config import repository
from twaky.agents_config.service import effective_model
from twaky.api.deps import require_owner
from twaky.api.errors import error_response
from twaky.api.schemas.agents import Agent, AgentSummary, DefaultPromptResponse

router = APIRouter(prefix="/agents", tags=["agents"])


def _to_summary(cfg) -> AgentSummary:
    return AgentSummary(
        id=cfg.id,
        display_name=cfg.display_name,
        role=cfg.role,
        model=cfg.model,
        temperature=cfg.temperature,
        effective_model=effective_model(cfg),
        updated_at=cfg.updated_at,
    )


def _to_full(cfg) -> Agent:
    return Agent(
        id=cfg.id,
        display_name=cfg.display_name,
        role=cfg.role,
        system_prompt=cfg.system_prompt,
        model=cfg.model,
        temperature=cfg.temperature,
        effective_model=effective_model(cfg),
        updated_at=cfg.updated_at,
    )


@router.get("", response_model=list[AgentSummary])
def list_agents(_email: str = Depends(require_owner)) -> list[AgentSummary]:
    return [_to_summary(c) for c in repository.list_all()]


@router.get("/{agent_id}", response_model=Agent)
def get_agent(agent_id: str, _email: str = Depends(require_owner)) -> Agent:
    cfg = repository.get(agent_id)
    if cfg is None:
        return error_response(
            code="agent_not_found",
            message=f"agent {agent_id!r} not found",
            status_code=404,
        )
    return _to_full(cfg)


@router.get("/{agent_id}/default_prompt", response_model=DefaultPromptResponse)
def get_default_prompt(
    agent_id: str, _email: str = Depends(require_owner)
) -> DefaultPromptResponse:
    if agent_id not in DEFAULT_PROMPTS:
        return error_response(
            code="agent_not_found",
            message=f"agent {agent_id!r} not found",
            status_code=404,
        )
    return DefaultPromptResponse(system_prompt=DEFAULT_PROMPTS[agent_id])


__all__ = ["router"]
```

- [ ] **Step 3: Register the router in `src/twaky/api/main.py`**

Find the existing import line `from twaky.api.routers import events, health, me, missions, oauth` and change to include `agents`:

```python
from twaky.api.routers import agents, events, health, me, missions, oauth
```

Then find the `app.include_router(...)` calls (search for "include_router") and add:

```python
app.include_router(agents.router, prefix="/api")
```

Matching the existing pattern (missions/me/events all mounted with `prefix="/api"`).

- [ ] **Step 4: Write GET tests in `tests/api/routers/test_agents.py`**

```python
"""GET /api/agents surface — auth + happy + 404 cases."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from twaky.agents_config.models import AgentConfig
from twaky.api.main import app
from twaky.api.session import SESSION_COOKIE_NAME, sign_session


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("API_SESSION_SECRET", "test-secret-32bytes-min-abcdefgh")
    monkeypatch.setenv("TWAKY_OWNER_EMAIL", "alice@x")
    monkeypatch.setenv("TWAKY_MODEL", "sentinel-default-model")


def _cookie() -> dict[str, str]:
    return {SESSION_COOKIE_NAME: sign_session("alice@x")}


def _fake_cfg(agent_id: str, model: str | None = None) -> AgentConfig:
    return AgentConfig(
        id=agent_id,
        display_name=agent_id.capitalize(),
        role="orchestrator" if agent_id == "atlas" else "specialist",
        system_prompt="you are " + agent_id,
        model=model,
        temperature=None,
        updated_at=datetime.now(UTC),
    )


class TestListAgents:
    def test_no_session_returns_401(self):
        r = TestClient(app).get("/api/agents")
        assert r.status_code == 401

    def test_happy_returns_summaries(self, monkeypatch):
        from twaky import config as _cfg
        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))

        rows = [_fake_cfg("atlas"), _fake_cfg("chronos"), _fake_cfg("plume"), _fake_cfg("iris")]
        with patch("twaky.api.routers.agents.repository.list_all", return_value=rows):
            r = TestClient(app).get("/api/agents", cookies=_cookie())
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 4
        assert {a["id"] for a in body} == {"atlas", "chronos", "plume", "iris"}
        assert "system_prompt" not in body[0]  # summary shape
        assert all("effective_model" in a for a in body)


class TestGetAgent:
    def test_happy(self, monkeypatch):
        from twaky import config as _cfg
        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))

        cfg = _fake_cfg("plume", model="openai/gpt-4o")
        with patch("twaky.api.routers.agents.repository.get", return_value=cfg):
            r = TestClient(app).get("/api/agents/plume", cookies=_cookie())
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "plume"
        assert body["model"] == "openai/gpt-4o"
        assert body["effective_model"] == "openai/gpt-4o"
        assert body["system_prompt"] == "you are plume"

    def test_effective_model_falls_back(self, monkeypatch):
        from twaky import config as _cfg
        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))

        # settings.model comes from the TWAKY_MODEL env fixture at module top.
        cfg = _fake_cfg("plume", model=None)
        with patch("twaky.api.routers.agents.repository.get", return_value=cfg):
            r = TestClient(app).get("/api/agents/plume", cookies=_cookie())
        assert r.json()["effective_model"] == "sentinel-default-model"

    def test_missing_returns_404(self, monkeypatch):
        from twaky import config as _cfg
        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))

        with patch("twaky.api.routers.agents.repository.get", return_value=None):
            r = TestClient(app).get("/api/agents/zeus", cookies=_cookie())
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "agent_not_found"


class TestDefaultPrompt:
    def test_happy(self, monkeypatch):
        from twaky import config as _cfg
        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))

        r = TestClient(app).get("/api/agents/plume/default_prompt", cookies=_cookie())
        assert r.status_code == 200
        body = r.json()
        assert "system_prompt" in body
        assert body["system_prompt"]  # non-empty

    def test_unknown_returns_404(self, monkeypatch):
        from twaky import config as _cfg
        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))

        r = TestClient(app).get("/api/agents/zeus/default_prompt", cookies=_cookie())
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "agent_not_found"
```

- [ ] **Step 5: Run the tests**

```bash
uv run pytest tests/api/routers/test_agents.py -v
```

Expected: 6 tests pass.

- [ ] **Step 6: Full gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src/ && uv run pytest -q
```

- [ ] **Step 7: Commit**

```bash
git add src/twaky/api/schemas/ src/twaky/api/routers/agents.py src/twaky/api/main.py tests/api/routers/test_agents.py
git commit -m "feat(api): GET /agents, GET /agents/{id}, GET /agents/{id}/default_prompt"
```

---

## Task 9: API — PATCH endpoint

**Files:**
- Modify: `src/twaky/api/routers/agents.py` — add PATCH handler
- Modify: `tests/api/routers/test_agents.py` — add PATCH test class

**Interfaces:**
- Consumes: `AgentUpdate` (T8), `service.validate_patch` (T3), `repository.update` (T2), `AgentConfigNotFound` (T2), `ValidationError` (T3).
- Produces: `PATCH /api/agents/{id}` → 200 `Agent` | 404 | 422.

- [ ] **Step 1: Extend `src/twaky/api/routers/agents.py`**

Add these imports at the top of the file:

```python
from twaky.agents_config.repository import AgentConfigNotFound
from twaky.agents_config.service import ValidationError, validate_patch
from twaky.api.schemas.agents import AgentUpdate
```

Add the PATCH handler after `get_default_prompt`:

```python
@router.patch("/{agent_id}", response_model=Agent)
def patch_agent(
    agent_id: str,
    body: AgentUpdate,
    _email: str = Depends(require_owner),
) -> Agent:
    # AgentUpdate accepts all-null; enforce the "at least one field required"
    # invariant here rather than in pydantic (which can't distinguish
    # "explicit null" from "field omitted" without model_fields_set).
    provided = {k: v for k, v in body.model_dump(exclude_unset=True).items()}
    try:
        patch = validate_patch(provided)
    except ValidationError as exc:
        return error_response(
            code="validation_failed",
            message=exc.message,
            status_code=422,
        )
    try:
        fresh = repository.update(agent_id, patch)
    except AgentConfigNotFound:
        return error_response(
            code="agent_not_found",
            message=f"agent {agent_id!r} not found",
            status_code=404,
        )
    return _to_full(fresh)
```

- [ ] **Step 2: Extend `tests/api/routers/test_agents.py` with the PATCH class**

Append to the existing test file:

```python
class TestPatchAgent:
    def test_no_session_returns_401(self):
        r = TestClient(app).patch("/api/agents/plume", json={"temperature": 0.3})
        assert r.status_code == 401

    def test_happy_temperature(self, monkeypatch):
        from twaky import config as _cfg
        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))

        updated_cfg = _fake_cfg("plume")
        # Mutate a mutable copy for the return_value; AgentConfig is frozen.
        from dataclasses import replace
        updated_cfg = replace(updated_cfg, temperature=0.3)

        with patch(
            "twaky.api.routers.agents.repository.update",
            return_value=updated_cfg,
        ) as up:
            r = TestClient(app).patch(
                "/api/agents/plume",
                json={"temperature": 0.3},
                cookies=_cookie(),
            )
        assert r.status_code == 200
        body = r.json()
        assert body["temperature"] == 0.3
        # repository.update was called with the validated payload
        up.assert_called_once_with("plume", {"temperature": 0.3})

    def test_happy_model_null(self, monkeypatch):
        from twaky import config as _cfg
        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))
        from dataclasses import replace
        cfg = replace(_fake_cfg("plume"), model=None)
        with patch("twaky.api.routers.agents.repository.update", return_value=cfg):
            r = TestClient(app).patch(
                "/api/agents/plume", json={"model": None}, cookies=_cookie()
            )
        assert r.status_code == 200
        assert r.json()["model"] is None

    def test_temperature_out_of_range_returns_422(self, monkeypatch):
        from twaky import config as _cfg
        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))
        r = TestClient(app).patch(
            "/api/agents/plume", json={"temperature": 3.0}, cookies=_cookie()
        )
        # Either pydantic (le=2.0) OR our service raises 422 — both acceptable
        assert r.status_code == 422

    def test_empty_prompt_returns_422(self, monkeypatch):
        from twaky import config as _cfg
        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))
        r = TestClient(app).patch(
            "/api/agents/plume", json={"system_prompt": "   "}, cookies=_cookie()
        )
        assert r.status_code == 422

    def test_empty_body_returns_422(self, monkeypatch):
        from twaky import config as _cfg
        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))
        r = TestClient(app).patch("/api/agents/plume", json={}, cookies=_cookie())
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_failed"

    def test_unknown_agent_returns_404(self, monkeypatch):
        from twaky import config as _cfg
        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))
        from twaky.agents_config.repository import AgentConfigNotFound
        with patch(
            "twaky.api.routers.agents.repository.update",
            side_effect=AgentConfigNotFound("no"),
        ):
            r = TestClient(app).patch(
                "/api/agents/zeus", json={"temperature": 0.5}, cookies=_cookie()
            )
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "agent_not_found"

    def test_unknown_field_returns_422(self, monkeypatch):
        from twaky import config as _cfg
        monkeypatch.setattr("twaky.api.deps.settings", _cfg.Settings(_env_file=None))
        r = TestClient(app).patch(
            "/api/agents/plume", json={"tools": ["read_email"]}, cookies=_cookie()
        )
        # pydantic extra="forbid" rejects unknown fields with 422
        assert r.status_code == 422
```

- [ ] **Step 3: Run the tests**

```bash
uv run pytest tests/api/routers/test_agents.py -v
```

Expected: 6 + 8 = 14 tests pass total (6 GET from T8, 8 PATCH added here).

- [ ] **Step 4: Full gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src/ && uv run pytest -q
```

- [ ] **Step 5: Commit**

```bash
git add src/twaky/api/routers/agents.py tests/api/routers/test_agents.py
git commit -m "feat(api): PATCH /agents/{id} with full validation matrix"
```

---

## Task 10: OpenAPI + regenerate types

**Files:**
- Modify: `docs/api/openapi.yaml`
- Modify: `frontend/src/lib/api-types.d.ts` (regenerated)

**Interfaces:**
- Produces: OpenAPI schemas for the frontend hooks in T11.

- [ ] **Step 1: Add schemas to `docs/api/openapi.yaml`**

Locate the `components:` → `schemas:` section (near the bottom of the file — check where existing schemas like `Mission` live). Add these three schemas as siblings:

```yaml
    Agent:
      type: object
      required: [id, display_name, role, system_prompt, effective_model, updated_at]
      additionalProperties: false
      properties:
        id:              { type: string, example: "plume" }
        display_name:    { type: string, example: "Plume" }
        role:            { type: string, enum: [orchestrator, specialist] }
        system_prompt:   { type: string, minLength: 1, maxLength: 8000 }
        model:           { type: string, nullable: true }
        temperature:     { type: number, format: float, minimum: 0.0, maximum: 2.0, nullable: true }
        effective_model: { type: string }
        updated_at:      { type: string, format: date-time }

    AgentSummary:
      type: object
      required: [id, display_name, role, effective_model, updated_at]
      additionalProperties: false
      properties:
        id:              { type: string }
        display_name:    { type: string }
        role:            { type: string, enum: [orchestrator, specialist] }
        model:           { type: string, nullable: true }
        temperature:     { type: number, format: float, nullable: true }
        effective_model: { type: string }
        updated_at:      { type: string, format: date-time }

    AgentUpdate:
      type: object
      additionalProperties: false
      properties:
        system_prompt: { type: string, minLength: 1, maxLength: 8000 }
        model:         { type: string, nullable: true }
        temperature:   { type: number, minimum: 0.0, maximum: 2.0, nullable: true }

    DefaultPromptResponse:
      type: object
      required: [system_prompt]
      additionalProperties: false
      properties:
        system_prompt: { type: string }
```

- [ ] **Step 2: Add the 4 paths to `docs/api/openapi.yaml`**

Locate the `paths:` section. Add these four paths under `paths:` (positioned alphabetically or near existing `/missions` paths — the file convention likely groups by resource):

```yaml
  /agents:
    get:
      summary: List agent configurations
      tags: [agents]
      security:
        - cookieAuth: []
      responses:
        '200':
          description: Ordered list of agent summaries (4 rows).
          content:
            application/json:
              schema:
                type: array
                items: { $ref: '#/components/schemas/AgentSummary' }
        '401': { $ref: '#/components/responses/Unauthorized' }

  /agents/{id}:
    get:
      summary: Get one agent's full configuration
      tags: [agents]
      security:
        - cookieAuth: []
      parameters:
        - name: id
          in: path
          required: true
          schema: { type: string }
      responses:
        '200':
          description: Agent config.
          content:
            application/json:
              schema: { $ref: '#/components/schemas/Agent' }
        '401': { $ref: '#/components/responses/Unauthorized' }
        '404': { $ref: '#/components/responses/NotFound' }
    patch:
      summary: Partially update an agent's config
      tags: [agents]
      security:
        - cookieAuth: []
      parameters:
        - name: id
          in: path
          required: true
          schema: { type: string }
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: '#/components/schemas/AgentUpdate' }
      responses:
        '200':
          description: Updated agent.
          content:
            application/json:
              schema: { $ref: '#/components/schemas/Agent' }
        '401': { $ref: '#/components/responses/Unauthorized' }
        '404': { $ref: '#/components/responses/NotFound' }
        '422': { $ref: '#/components/responses/ValidationError' }

  /agents/{id}/default_prompt:
    get:
      summary: Get the built-in default prompt for an agent
      tags: [agents]
      security:
        - cookieAuth: []
      parameters:
        - name: id
          in: path
          required: true
          schema: { type: string }
      responses:
        '200':
          description: Default prompt.
          content:
            application/json:
              schema: { $ref: '#/components/schemas/DefaultPromptResponse' }
        '401': { $ref: '#/components/responses/Unauthorized' }
        '404': { $ref: '#/components/responses/NotFound' }
```

Check the existing file to confirm the exact ref paths for `Unauthorized`, `NotFound`, `ValidationError`, and `cookieAuth`. If they differ (e.g., they're inlined instead of referenced), match the pattern used in the `/missions` paths.

- [ ] **Step 3: Regenerate frontend types**

```bash
cd frontend
make api-types
```

Expected output: `wrote frontend/src/lib/api-types.d.ts`. Inspect the diff — should show new types `Agent`, `AgentSummary`, `AgentUpdate`, `DefaultPromptResponse` plus 4 new path entries.

- [ ] **Step 4: Verify drift check clean**

```bash
cd frontend
make api-types && git diff --exit-code src/lib/api-types.d.ts
```

Expected: exit code 0 (types file is up to date after the regen).

- [ ] **Step 5: Full gates**

```bash
cd /home/mmaudet/work/twaky
uv run ruff check . && uv run ruff format --check . && uv run mypy src/ && uv run pytest -q
cd frontend
npm run typecheck && npm run lint && npm run test:unit && npm run build
```

- [ ] **Step 6: Commit**

```bash
cd /home/mmaudet/work/twaky
git add docs/api/openapi.yaml frontend/src/lib/api-types.d.ts
git commit -m "docs(api): OpenAPI schemas for /agents + regen frontend types"
```

---

## Task 11: Frontend hooks

**Files:**
- Create: `frontend/src/hooks/use-agents.ts`
- Create: `frontend/src/hooks/use-agents.test.ts`

**Interfaces:**
- Consumes: `api` singleton (existing `frontend/src/lib/api.ts`), `paths`/`components` types (regenerated in T10), `ApiError`/`isErrorEnvelope` (existing `frontend/src/lib/api-error.ts`).
- Produces:
  - `useAgents()` — TanStack useQuery, `queryKey: ['agents']`.
  - `useAgent(id: string)` — `queryKey: ['agent', id]`.
  - `useDefaultPrompt(id: string, options?: { enabled?: boolean })` — `queryKey: ['agent-default', id]`, defaults `enabled: false` (lazy fetch).
  - `useUpdateAgent(id: string)` — TanStack useMutation. On success invalidates `['agent', id]` and `['agents']`.

- [ ] **Step 1: Write `frontend/src/hooks/use-agents.ts`**

```typescript
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ApiError, isErrorEnvelope } from '@/lib/api-error'
import type { components } from '@/lib/api-types'

export type Agent = components['schemas']['Agent']
export type AgentSummary = components['schemas']['AgentSummary']
export type AgentUpdate = components['schemas']['AgentUpdate']

function toApiError(error: unknown): ApiError {
    return new ApiError(
        isErrorEnvelope(error)
            ? error
            : { error: { code: 'unknown', message: 'Unknown API error' } },
    )
}

export function useAgents() {
    return useQuery({
        queryKey: ['agents'],
        queryFn: async () => {
            const { data, error } = await api.GET('/agents')
            if (error) throw toApiError(error)
            return data
        },
    })
}

export function useAgent(id: string) {
    return useQuery({
        queryKey: ['agent', id],
        queryFn: async () => {
            const { data, error } = await api.GET('/agents/{id}', {
                params: { path: { id } },
            })
            if (error) throw toApiError(error)
            return data
        },
    })
}

export function useDefaultPrompt(id: string, options?: { enabled?: boolean }) {
    return useQuery({
        queryKey: ['agent-default', id],
        enabled: options?.enabled ?? false,
        queryFn: async () => {
            const { data, error } = await api.GET('/agents/{id}/default_prompt', {
                params: { path: { id } },
            })
            if (error) throw toApiError(error)
            return data
        },
    })
}

export function useUpdateAgent(id: string) {
    const qc = useQueryClient()
    return useMutation({
        mutationFn: async (patch: AgentUpdate) => {
            const { data, error } = await api.PATCH('/agents/{id}', {
                params: { path: { id } },
                body: patch,
            })
            if (error) throw toApiError(error)
            return data
        },
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['agent', id] })
            qc.invalidateQueries({ queryKey: ['agents'] })
        },
    })
}
```

- [ ] **Step 2: Write `frontend/src/hooks/use-agents.test.ts`**

```typescript
import { describe, it, expect, beforeAll, afterAll, afterEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { setupServer } from 'msw/node'
import { http, HttpResponse } from 'msw'
import { ReactNode } from 'react'
import { useAgents, useAgent, useUpdateAgent, useDefaultPrompt } from './use-agents'

const server = setupServer()

beforeAll(() => server.listen())
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function makeWrapper() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return function Wrapper({ children }: { children: ReactNode }) {
        return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    }
}

describe('useAgents', () => {
    it('returns 4 agent summaries on success', async () => {
        server.use(
            http.get('http://localhost:3000/api/agents', () =>
                HttpResponse.json([
                    { id: 'atlas', display_name: 'Atlas', role: 'orchestrator', model: null, temperature: null, effective_model: 'default', updated_at: '2026-01-01T00:00:00Z' },
                    { id: 'chronos', display_name: 'Chronos', role: 'specialist', model: null, temperature: null, effective_model: 'default', updated_at: '2026-01-01T00:00:00Z' },
                    { id: 'iris', display_name: 'Iris', role: 'specialist', model: null, temperature: null, effective_model: 'default', updated_at: '2026-01-01T00:00:00Z' },
                    { id: 'plume', display_name: 'Plume', role: 'specialist', model: null, temperature: null, effective_model: 'default', updated_at: '2026-01-01T00:00:00Z' },
                ]),
            ),
        )
        const { result } = renderHook(() => useAgents(), { wrapper: makeWrapper() })
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data).toHaveLength(4)
    })
})

describe('useAgent', () => {
    it('returns one agent', async () => {
        server.use(
            http.get('http://localhost:3000/api/agents/plume', () =>
                HttpResponse.json({
                    id: 'plume', display_name: 'Plume', role: 'specialist',
                    system_prompt: 'you are plume', model: null, temperature: null,
                    effective_model: 'default', updated_at: '2026-01-01T00:00:00Z',
                }),
            ),
        )
        const { result } = renderHook(() => useAgent('plume'), { wrapper: makeWrapper() })
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data?.system_prompt).toBe('you are plume')
    })

    it('surfaces 404 as ApiError', async () => {
        server.use(
            http.get('http://localhost:3000/api/agents/zeus', () =>
                HttpResponse.json({ error: { code: 'agent_not_found', message: 'no' } }, { status: 404 }),
            ),
        )
        const { result } = renderHook(() => useAgent('zeus'), { wrapper: makeWrapper() })
        await waitFor(() => expect(result.current.isError).toBe(true))
        expect((result.current.error as any).envelope.error.code).toBe('agent_not_found')
    })
})

describe('useDefaultPrompt', () => {
    it('does not fetch by default', () => {
        const { result } = renderHook(() => useDefaultPrompt('plume'), { wrapper: makeWrapper() })
        expect(result.current.fetchStatus).toBe('idle')
    })

    it('fetches when enabled=true', async () => {
        server.use(
            http.get('http://localhost:3000/api/agents/plume/default_prompt', () =>
                HttpResponse.json({ system_prompt: 'ORIGINAL PLUME PROMPT' }),
            ),
        )
        const { result } = renderHook(
            () => useDefaultPrompt('plume', { enabled: true }),
            { wrapper: makeWrapper() },
        )
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data?.system_prompt).toBe('ORIGINAL PLUME PROMPT')
    })
})

describe('useUpdateAgent', () => {
    it('sends PATCH and returns updated agent', async () => {
        server.use(
            http.patch('http://localhost:3000/api/agents/plume', async ({ request }) => {
                const body = await request.json()
                return HttpResponse.json({
                    id: 'plume', display_name: 'Plume', role: 'specialist',
                    system_prompt: 'you are plume', model: null,
                    temperature: (body as any).temperature,
                    effective_model: 'default', updated_at: '2026-01-01T00:00:00Z',
                })
            }),
        )
        const { result } = renderHook(() => useUpdateAgent('plume'), { wrapper: makeWrapper() })
        result.current.mutate({ temperature: 0.7 })
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data?.temperature).toBe(0.7)
    })

    it('surfaces 422 as ApiError', async () => {
        server.use(
            http.patch('http://localhost:3000/api/agents/plume', () =>
                HttpResponse.json(
                    { error: { code: 'validation_failed', message: 'bad' } },
                    { status: 422 },
                ),
            ),
        )
        const { result } = renderHook(() => useUpdateAgent('plume'), { wrapper: makeWrapper() })
        result.current.mutate({ temperature: 99 })
        await waitFor(() => expect(result.current.isError).toBe(true))
        expect((result.current.error as any).envelope.error.code).toBe('validation_failed')
    })
})
```

- [ ] **Step 3: Run the tests**

```bash
cd frontend
npm run test:unit -- use-agents
```

Expected: 6 tests pass.

- [ ] **Step 4: Full frontend gates**

```bash
cd frontend
npm run typecheck && npm run lint && npm run test:unit && npm run build
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/use-agents.ts frontend/src/hooks/use-agents.test.ts
git commit -m "feat(frontend): agents TanStack Query hooks + MSW tests"
```

---

## Task 12: Frontend list page + nav link

**Files:**
- Create: `frontend/src/app/agents/page.tsx`
- Modify: `frontend/src/components/layout/header.tsx`
- Install shadcn Table if not present (already installed per `frontend/src/components/ui/table.tsx` — verify)

**Interfaces:**
- Consumes: `useAgents` (T11), `RelativeTime` component (existing `frontend/src/components/missions/relative-time.tsx`), shadcn `Table`, `Badge`, `Button` (existing).
- Produces: `/agents` route + nav link "Agents".

- [ ] **Step 1: Write `frontend/src/app/agents/page.tsx`**

```tsx
'use client'

import Link from 'next/link'
import { RelativeTime } from '@/components/missions/relative-time'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table'
import { useAgents } from '@/hooks/use-agents'

export default function AgentsPage() {
    const { data: agents, isLoading, error } = useAgents()

    if (isLoading) return <p className="text-muted-foreground">Loading…</p>
    if (error) return <p className="text-red-600">Error: {error.message}</p>
    if (!agents) return <p>No agents.</p>

    return (
        <div className="space-y-4">
            <h1 className="text-2xl font-semibold">Agents</h1>
            <p className="text-sm text-muted-foreground">
                Edit the built-in agents' system prompt, model, or temperature.
                Changes apply on the next sub-agent invocation — no restart required.
            </p>
            <Table>
                <TableHeader>
                    <TableRow>
                        <TableHead>Name</TableHead>
                        <TableHead>Role</TableHead>
                        <TableHead>Model</TableHead>
                        <TableHead>Temperature</TableHead>
                        <TableHead>Updated</TableHead>
                        <TableHead className="w-16"></TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {agents.map((a) => (
                        <TableRow key={a.id}>
                            <TableCell className="font-medium">{a.display_name}</TableCell>
                            <TableCell>
                                <Badge variant={a.role === 'orchestrator' ? 'default' : 'secondary'}>
                                    {a.role}
                                </Badge>
                            </TableCell>
                            <TableCell>
                                {a.model
                                    ? <code className="text-xs">{a.effective_model}</code>
                                    : <span className="italic text-muted-foreground">
                                        {a.effective_model} (default)
                                    </span>
                                }
                            </TableCell>
                            <TableCell>
                                {a.temperature !== null
                                    ? <code className="text-xs">{a.temperature.toFixed(2)}</code>
                                    : <span className="italic text-muted-foreground">(default)</span>
                                }
                            </TableCell>
                            <TableCell className="text-sm text-muted-foreground">
                                <RelativeTime timestamp={a.updated_at} />
                            </TableCell>
                            <TableCell>
                                <Link href={`/agents/${a.id}`}>
                                    <Button variant="outline" size="sm">Edit</Button>
                                </Link>
                            </TableCell>
                        </TableRow>
                    ))}
                </TableBody>
            </Table>
        </div>
    )
}
```

- [ ] **Step 2: Add the "Agents" link to `frontend/src/components/layout/header.tsx`**

Read the current file first. It has nav links like Missions and Stats. Insert `<NavLink href="/agents">Agents</NavLink>` (or whatever the component's actual link syntax is) **between** Missions and Stats. Match the exact pattern already in place — same className, same active-highlight logic.

Example (adapt to the file's real shape):

```tsx
// existing: <NavLink href="/">Missions</NavLink>
<NavLink href="/agents">Agents</NavLink>
// existing: <NavLink href="/stats">Stats</NavLink>
```

- [ ] **Step 3: Update the header test**

Open `frontend/src/components/layout/header.test.tsx`. Add an assertion that the "Agents" link is present:

```tsx
it('shows the Agents nav link', () => {
    // (adapt to the file's existing render helper)
    render(<Header />)
    expect(screen.getByRole('link', { name: 'Agents' })).toBeInTheDocument()
})
```

- [ ] **Step 4: Run tests**

```bash
cd frontend
npm run test:unit -- header
```

Expected: existing header tests still pass + new one passes.

- [ ] **Step 5: Full gates**

```bash
cd frontend
npm run typecheck && npm run lint && npm run test:unit && npm run build
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/agents/page.tsx frontend/src/components/layout/header.tsx frontend/src/components/layout/header.test.tsx
git commit -m "feat(frontend): /agents list page + nav link"
```

---

## Task 13: Frontend edit page + inputs

**Files:**
- Create: `frontend/src/app/agents/[id]/page.tsx`
- Create: `frontend/src/components/agents/agent-prompt-input.tsx`
- Create: `frontend/src/components/agents/agent-prompt-input.test.tsx`
- Create: `frontend/src/components/agents/agent-model-input.tsx`
- Create: `frontend/src/components/agents/agent-model-input.test.tsx`
- Create: `frontend/src/components/agents/agent-temperature-input.tsx`
- Create: `frontend/src/components/agents/agent-temperature-input.test.tsx`
- Add shadcn components: Select, Slider, Checkbox, Input, Label (via `npx shadcn add`)
- Modify: `frontend/.env.example` — document `NEXT_PUBLIC_TWAKY_KNOWN_MODELS`

**Interfaces:**
- Consumes: `useAgent`, `useUpdateAgent` (T11), shadcn `Select`/`Slider`/`Checkbox`/`Input`/`Label`/`Textarea`/`Button` primitives, `toast` from sonner.
- Produces: `/agents/[id]` route with a working save.

- [ ] **Step 1: Install missing shadcn components**

```bash
cd frontend
npx shadcn@latest add select slider checkbox input label
```

Each command writes a file under `src/components/ui/`. If shadcn asks about which style or which package manager, accept defaults matching the existing `src/components/ui/*` files (Tailwind-based, same variants).

- [ ] **Step 2: Write `agent-prompt-input.tsx`**

Create `frontend/src/components/agents/agent-prompt-input.tsx`:

```tsx
'use client'

import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'

type Props = {
    value: string
    onChange: (v: string) => void
    max?: number
}

export function AgentPromptInput({ value, onChange, max = 8000 }: Props) {
    const length = value.length
    const overLimit = length > max
    return (
        <div className="space-y-2">
            <Label htmlFor="agent-prompt">System prompt</Label>
            <Textarea
                id="agent-prompt"
                value={value}
                onChange={(e) => onChange(e.target.value)}
                rows={15}
                className="font-mono text-sm resize-y"
            />
            <p className={`text-xs text-right ${overLimit ? 'text-red-600' : 'text-muted-foreground'}`}>
                {length.toLocaleString()} / {max.toLocaleString()}
            </p>
        </div>
    )
}
```

- [ ] **Step 3: Write `agent-prompt-input.test.tsx`**

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { AgentPromptInput } from './agent-prompt-input'

describe('AgentPromptInput', () => {
    it('renders textarea with the value', () => {
        render(<AgentPromptInput value="hello" onChange={() => {}} />)
        expect(screen.getByLabelText(/system prompt/i)).toHaveValue('hello')
    })

    it('calls onChange when typing', () => {
        const onChange = vi.fn()
        render(<AgentPromptInput value="" onChange={onChange} />)
        fireEvent.change(screen.getByLabelText(/system prompt/i), { target: { value: 'x' } })
        expect(onChange).toHaveBeenCalledWith('x')
    })

    it('shows counter in default color under limit', () => {
        render(<AgentPromptInput value="hello" onChange={() => {}} />)
        expect(screen.getByText(/5 \/ 8,000/)).toBeInTheDocument()
        expect(screen.getByText(/5 \/ 8,000/)).not.toHaveClass('text-red-600')
    })

    it('shows counter in red when over limit', () => {
        render(<AgentPromptInput value={'x'.repeat(8001)} onChange={() => {}} />)
        const counter = screen.getByText(/8,001 \/ 8,000/)
        expect(counter).toHaveClass('text-red-600')
    })
})
```

- [ ] **Step 4: Write `agent-model-input.tsx`**

```tsx
'use client'

import { useState, useEffect } from 'react'
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

const DEFAULT_KNOWN = 'claude-sonnet-4-5-20250929,openai/gpt-4o,openai/gpt-4o-mini,openrouter/moonshotai/kimi-k2-0905,ollama/llama3'
const KNOWN_MODELS = (process.env.NEXT_PUBLIC_TWAKY_KNOWN_MODELS ?? DEFAULT_KNOWN)
    .split(',').map(s => s.trim()).filter(Boolean)

type Props = {
    value: string | null
    onChange: (v: string | null) => void
    effectiveDefault: string   // shown in the "Use default" option label
}

const USE_DEFAULT = '__use_default__'
const CUSTOM = '__custom__'

export function AgentModelInput({ value, onChange, effectiveDefault }: Props) {
    const initialMode = value === null
        ? USE_DEFAULT
        : KNOWN_MODELS.includes(value)
            ? value
            : CUSTOM
    const [selectValue, setSelectValue] = useState<string>(initialMode)
    const [customText, setCustomText] = useState<string>(
        value !== null && !KNOWN_MODELS.includes(value) ? value : '',
    )

    // Keep local state in sync if the parent resets value (e.g., "Reset to defaults")
    useEffect(() => {
        if (value === null) setSelectValue(USE_DEFAULT)
        else if (KNOWN_MODELS.includes(value)) setSelectValue(value)
        else {
            setSelectValue(CUSTOM)
            setCustomText(value)
        }
    }, [value])

    const handleSelect = (next: string) => {
        setSelectValue(next)
        if (next === USE_DEFAULT) onChange(null)
        else if (next === CUSTOM) onChange(customText || '')
        else onChange(next)
    }

    const handleCustomText = (text: string) => {
        setCustomText(text)
        onChange(text)
    }

    return (
        <div className="space-y-2">
            <Label htmlFor="agent-model">Model</Label>
            <Select value={selectValue} onValueChange={handleSelect}>
                <SelectTrigger id="agent-model">
                    <SelectValue />
                </SelectTrigger>
                <SelectContent>
                    <SelectItem value={USE_DEFAULT}>
                        Use default ({effectiveDefault})
                    </SelectItem>
                    {KNOWN_MODELS.map((m) => (
                        <SelectItem key={m} value={m}>{m}</SelectItem>
                    ))}
                    <SelectItem value={CUSTOM}>Custom…</SelectItem>
                </SelectContent>
            </Select>
            {selectValue === CUSTOM && (
                <Input
                    id="agent-model-custom"
                    aria-label="Custom model string"
                    value={customText}
                    onChange={(e) => handleCustomText(e.target.value)}
                    placeholder="e.g. openrouter/moonshotai/kimi-k2-0905"
                />
            )}
        </div>
    )
}
```

- [ ] **Step 5: Write `agent-model-input.test.tsx`**

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { AgentModelInput } from './agent-model-input'

describe('AgentModelInput', () => {
    it('renders "Use default" when value is null', () => {
        render(<AgentModelInput value={null} onChange={() => {}} effectiveDefault="X" />)
        expect(screen.getByText(/Use default \(X\)/)).toBeInTheDocument()
    })

    it('does not render custom input when a known model is selected', () => {
        render(
            <AgentModelInput
                value="openai/gpt-4o"
                onChange={() => {}}
                effectiveDefault="X"
            />,
        )
        expect(screen.queryByLabelText(/custom model string/i)).not.toBeInTheDocument()
    })

    it('shows custom input when value is not in the known list', () => {
        render(
            <AgentModelInput
                value="exotic/private-model-v2"
                onChange={() => {}}
                effectiveDefault="X"
            />,
        )
        expect(screen.getByLabelText(/custom model string/i)).toHaveValue('exotic/private-model-v2')
    })

    it('calls onChange(null) when the user picks Use default', () => {
        const onChange = vi.fn()
        render(
            <AgentModelInput value="openai/gpt-4o" onChange={onChange} effectiveDefault="X" />,
        )
        // simulate via Select — Radix uses role="combobox"
        const combobox = screen.getByRole('combobox')
        fireEvent.click(combobox)
        fireEvent.click(screen.getByText(/Use default \(X\)/))
        expect(onChange).toHaveBeenLastCalledWith(null)
    })

    it('calls onChange with typed value when using Custom…', () => {
        const onChange = vi.fn()
        render(
            <AgentModelInput
                value="some/exotic-value"
                onChange={onChange}
                effectiveDefault="X"
            />,
        )
        const input = screen.getByLabelText(/custom model string/i)
        fireEvent.change(input, { target: { value: 'foo/bar' } })
        expect(onChange).toHaveBeenLastCalledWith('foo/bar')
    })
})
```

- [ ] **Step 6: Write `agent-temperature-input.tsx`**

```tsx
'use client'

import { Slider } from '@/components/ui/slider'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'

type Props = {
    value: number | null
    onChange: (v: number | null) => void
}

export function AgentTemperatureInput({ value, onChange }: Props) {
    const useDefault = value === null
    const sliderValue = value ?? 0.7

    return (
        <div className="space-y-2">
            <Label htmlFor="agent-temperature">Temperature</Label>
            <div className="flex items-center gap-4">
                <Slider
                    id="agent-temperature"
                    disabled={useDefault}
                    min={0.0}
                    max={2.0}
                    step={0.05}
                    value={[sliderValue]}
                    onValueChange={([v]) => onChange(v)}
                    className="flex-1"
                />
                <code className="font-mono text-sm w-16 text-right">
                    {useDefault ? '—' : sliderValue.toFixed(2)}
                </code>
            </div>
            <div className="flex items-center gap-2 pt-1">
                <Checkbox
                    id="temperature-use-default"
                    checked={useDefault}
                    onCheckedChange={(checked) => onChange(checked ? null : 0.7)}
                />
                <label htmlFor="temperature-use-default" className="text-sm">
                    Use LiteLLM default (varies by provider)
                </label>
            </div>
        </div>
    )
}
```

- [ ] **Step 7: Write `agent-temperature-input.test.tsx`**

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { AgentTemperatureInput } from './agent-temperature-input'

describe('AgentTemperatureInput', () => {
    it('disables slider and shows em-dash when value is null', () => {
        render(<AgentTemperatureInput value={null} onChange={() => {}} />)
        expect(screen.getByRole('checkbox')).toBeChecked()
        expect(screen.getByText('—')).toBeInTheDocument()
    })

    it('shows numeric readout when value is set', () => {
        render(<AgentTemperatureInput value={0.7} onChange={() => {}} />)
        expect(screen.getByText('0.70')).toBeInTheDocument()
        expect(screen.getByRole('checkbox')).not.toBeChecked()
    })

    it('calls onChange(null) when checkbox is checked', () => {
        const onChange = vi.fn()
        render(<AgentTemperatureInput value={0.7} onChange={onChange} />)
        fireEvent.click(screen.getByRole('checkbox'))
        expect(onChange).toHaveBeenCalledWith(null)
    })

    it('calls onChange(0.7) when checkbox is unchecked from null', () => {
        const onChange = vi.fn()
        render(<AgentTemperatureInput value={null} onChange={onChange} />)
        fireEvent.click(screen.getByRole('checkbox'))
        expect(onChange).toHaveBeenCalledWith(0.7)
    })
})
```

- [ ] **Step 8: Write `frontend/src/app/agents/[id]/page.tsx`**

```tsx
'use client'

import Link from 'next/link'
import { use, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { RelativeTime } from '@/components/missions/relative-time'
import { AgentPromptInput } from '@/components/agents/agent-prompt-input'
import { AgentModelInput } from '@/components/agents/agent-model-input'
import { AgentTemperatureInput } from '@/components/agents/agent-temperature-input'
import { useAgent, useUpdateAgent, type AgentUpdate } from '@/hooks/use-agents'

export default function AgentEditPage({
    params,
}: { params: Promise<{ id: string }> }) {
    const { id } = use(params)
    const router = useRouter()
    const { data: agent, isLoading, error } = useAgent(id)
    const update = useUpdateAgent(id)

    const [prompt, setPrompt] = useState('')
    const [model, setModel] = useState<string | null>(null)
    const [temperature, setTemperature] = useState<number | null>(null)

    // Hydrate local form state once the agent loads.
    useEffect(() => {
        if (agent) {
            setPrompt(agent.system_prompt)
            setModel(agent.model)
            setTemperature(agent.temperature)
        }
    }, [agent])

    if (isLoading) return <p className="text-muted-foreground">Loading…</p>
    if (error) return <p className="text-red-600">Error: {error.message}</p>
    if (!agent) return <p>Not found.</p>

    const trimmedPrompt = prompt.trim()
    const isDirty =
        trimmedPrompt !== agent.system_prompt.trim() ||
        model !== agent.model ||
        temperature !== agent.temperature
    const isValid = trimmedPrompt.length >= 1 && trimmedPrompt.length <= 8000

    const handleSave = () => {
        const patch: AgentUpdate = {}
        if (trimmedPrompt !== agent.system_prompt.trim()) patch.system_prompt = trimmedPrompt
        if (model !== agent.model) patch.model = model
        if (temperature !== agent.temperature) patch.temperature = temperature

        update.mutate(patch, {
            onSuccess: () => {
                toast.success('Saved. Changes apply to the next mission.')
                router.push('/agents')
            },
            onError: (err) => {
                toast.error(err.message || 'Save failed')
            },
        })
    }

    return (
        <div className="space-y-6">
            <div>
                <Link href="/agents" className="text-sm text-muted-foreground hover:underline">
                    ← Back to agents
                </Link>
            </div>

            <div className="space-y-2">
                <h1 className="text-2xl font-semibold">Edit {agent.display_name}</h1>
                <div className="flex items-center gap-3 text-sm text-muted-foreground">
                    <Badge variant={agent.role === 'orchestrator' ? 'default' : 'secondary'}>
                        {agent.role}
                    </Badge>
                    <span>·</span>
                    <span>updated <RelativeTime timestamp={agent.updated_at} /></span>
                </div>
            </div>

            <AgentPromptInput value={prompt} onChange={setPrompt} />

            <AgentModelInput
                value={model}
                onChange={setModel}
                effectiveDefault={agent.effective_model}
            />

            <AgentTemperatureInput value={temperature} onChange={setTemperature} />

            <div className="flex items-center justify-end gap-2 pt-4">
                <Link href="/agents">
                    <Button variant="ghost">Cancel</Button>
                </Link>
                <Button
                    onClick={handleSave}
                    disabled={!isDirty || !isValid || update.isPending}
                >
                    {update.isPending ? 'Saving…' : 'Save'}
                </Button>
            </div>
        </div>
    )
}
```

- [ ] **Step 9: Update `frontend/.env.example`**

Add near the other `NEXT_PUBLIC_*` entries:

```dotenv
# Comma-separated list of model strings offered in the /agents/[id] dropdown.
# Users can still enter arbitrary values via the "Custom…" option.
NEXT_PUBLIC_TWAKY_KNOWN_MODELS=claude-sonnet-4-5-20250929,openai/gpt-4o,openai/gpt-4o-mini,openrouter/moonshotai/kimi-k2-0905,ollama/llama3
```

- [ ] **Step 10: Run component tests**

```bash
cd frontend
npm run test:unit -- agents
```

Expected: 3 test files × 4 tests each ≈ 12 tests pass.

- [ ] **Step 11: Full frontend gates**

```bash
cd frontend
npm run typecheck && npm run lint && npm run test:unit && npm run build
```

- [ ] **Step 12: Commit**

```bash
git add frontend/src/app/agents/\[id\]/page.tsx frontend/src/components/agents/ frontend/src/components/ui/ frontend/.env.example
git commit -m "feat(frontend): /agents/[id] edit page with hybrid model + temperature inputs"
```

---

## Task 14: Reset-to-defaults dialog

**Files:**
- Create: `frontend/src/components/agents/reset-to-defaults-dialog.tsx`
- Create: `frontend/src/components/agents/reset-to-defaults-dialog.test.tsx`
- Modify: `frontend/src/app/agents/[id]/page.tsx` — add the button

**Interfaces:**
- Consumes: `useDefaultPrompt` (T11), shadcn `AlertDialog`.
- Produces: A component `<ResetToDefaultsDialog agentId displayName onReset={(prompt) => …}>` that renders the trigger button.

- [ ] **Step 1: Write `reset-to-defaults-dialog.tsx`**

```tsx
'use client'

import { useState } from 'react'
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
    AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { useDefaultPrompt } from '@/hooks/use-agents'

type Props = {
    agentId: string
    displayName: string
    /** Called with the default prompt string when the user confirms. */
    onReset: (defaultPrompt: string) => void
}

export function ResetToDefaultsDialog({ agentId, displayName, onReset }: Props) {
    const [open, setOpen] = useState(false)
    const { refetch, isFetching } = useDefaultPrompt(agentId)

    const handleConfirm = async () => {
        const { data } = await refetch()
        if (data?.system_prompt) {
            onReset(data.system_prompt)
        }
        setOpen(false)
    }

    return (
        <AlertDialog open={open} onOpenChange={setOpen}>
            <AlertDialogTrigger asChild>
                <Button variant="outline">Reset to defaults</Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
                <AlertDialogHeader>
                    <AlertDialogTitle>Reset {displayName}?</AlertDialogTitle>
                    <AlertDialogDescription>
                        This resets the system prompt to the built-in default and
                        clears the model and temperature overrides. You still need
                        to click Save to persist the change.
                    </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                    <AlertDialogAction onClick={handleConfirm} disabled={isFetching}>
                        {isFetching ? 'Loading…' : 'Reset'}
                    </AlertDialogAction>
                </AlertDialogFooter>
            </AlertDialogContent>
        </AlertDialog>
    )
}
```

- [ ] **Step 2: Write `reset-to-defaults-dialog.test.tsx`**

```tsx
import { describe, it, expect, vi, beforeAll, afterAll, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { setupServer } from 'msw/node'
import { http, HttpResponse } from 'msw'
import { ReactNode } from 'react'
import { ResetToDefaultsDialog } from './reset-to-defaults-dialog'

const server = setupServer(
    http.get('http://localhost:3000/api/agents/plume/default_prompt', () =>
        HttpResponse.json({ system_prompt: 'DEFAULT PLUME PROMPT' }),
    ),
)

beforeAll(() => server.listen())
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function wrap(node: ReactNode) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

describe('ResetToDefaultsDialog', () => {
    it('opens dialog and calls onReset with default prompt after confirmation', async () => {
        const onReset = vi.fn()
        wrap(<ResetToDefaultsDialog agentId="plume" displayName="Plume" onReset={onReset} />)

        fireEvent.click(screen.getByRole('button', { name: /reset to defaults/i }))
        expect(screen.getByRole('alertdialog')).toBeInTheDocument()

        fireEvent.click(screen.getByRole('button', { name: /^reset$/i }))

        await waitFor(() => expect(onReset).toHaveBeenCalledWith('DEFAULT PLUME PROMPT'))
    })

    it('closes without calling onReset on Cancel', async () => {
        const onReset = vi.fn()
        wrap(<ResetToDefaultsDialog agentId="plume" displayName="Plume" onReset={onReset} />)

        fireEvent.click(screen.getByRole('button', { name: /reset to defaults/i }))
        fireEvent.click(screen.getByRole('button', { name: /cancel/i }))

        await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
        expect(onReset).not.toHaveBeenCalled()
    })
})
```

- [ ] **Step 3: Wire the dialog into the edit page**

Open `frontend/src/app/agents/[id]/page.tsx`. Add the import:

```tsx
import { ResetToDefaultsDialog } from '@/components/agents/reset-to-defaults-dialog'
```

In the button row at the bottom, add the Reset button before the Cancel + Save pair:

```tsx
<div className="flex items-center justify-end gap-2 pt-4">
    <ResetToDefaultsDialog
        agentId={agent.id}
        displayName={agent.display_name}
        onReset={(defaultPrompt) => {
            setPrompt(defaultPrompt)
            setModel(null)
            setTemperature(null)
        }}
    />
    <div className="flex-1" />
    <Link href="/agents">
        <Button variant="ghost">Cancel</Button>
    </Link>
    <Button
        onClick={handleSave}
        disabled={!isDirty || !isValid || update.isPending}
    >
        {update.isPending ? 'Saving…' : 'Save'}
    </Button>
</div>
```

- [ ] **Step 4: Run component tests**

```bash
cd frontend
npm run test:unit -- reset-to-defaults
```

Expected: 2 tests pass.

- [ ] **Step 5: Full frontend gates**

```bash
cd frontend
npm run typecheck && npm run lint && npm run test:unit && npm run build
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/agents/reset-to-defaults-dialog.tsx frontend/src/components/agents/reset-to-defaults-dialog.test.tsx frontend/src/app/agents/\[id\]/page.tsx
git commit -m "feat(frontend): reset-to-defaults dialog on agent edit page"
```

---

## Task 15: Playwright E2E

**Files:**
- Create: `frontend/tests/e2e/agents-edit.spec.ts`
- Create: `frontend/tests/e2e/agents-validation.spec.ts`

**Interfaces:**
- Consumes: `signedInPage` fixture from `frontend/tests/e2e/fixtures.ts` (unchanged).

- [ ] **Step 1: Write `agents-edit.spec.ts`**

```typescript
import { test, expect } from './fixtures'

test('edit Plume temperature, save, verify in list', async ({ signedInPage: page }) => {
    // Navigate to /agents
    await page.goto('/agents')
    await expect(page.getByRole('heading', { name: 'Agents' })).toBeVisible()
    await expect(page.getByRole('cell', { name: 'Plume' })).toBeVisible()

    // Click Edit next to Plume.
    const plumeRow = page.getByRole('row').filter({ hasText: 'Plume' })
    await plumeRow.getByRole('link', { name: 'Edit' }).click()

    // On /agents/plume — change the temperature via the checkbox+slider.
    await expect(page.getByRole('heading', { name: /Edit Plume/i })).toBeVisible()

    // Ensure "Use LiteLLM default" is unchecked so the slider is active.
    const checkbox = page.getByLabel(/Use LiteLLM default/)
    if (await checkbox.isChecked()) await checkbox.click()

    // Interact with the slider via ARIA — set value to 0.3.
    // Radix Slider exposes role="slider" with aria-valuenow/min/max/step.
    const slider = page.getByRole('slider')
    await slider.focus()
    // Press Home to reach 0.0, then press ArrowRight 6 times (6 × 0.05 = 0.30).
    await slider.press('Home')
    for (let i = 0; i < 6; i++) await slider.press('ArrowRight')

    // Save.
    await page.getByRole('button', { name: 'Save' }).click()

    // Toast + redirect to /agents.
    await expect(page.getByText(/Saved\. Changes apply to the next mission\./)).toBeVisible()
    await expect(page).toHaveURL(/\/agents$/)

    // The Plume row now shows 0.30 in the Temperature column.
    const plumeRowAfter = page.getByRole('row').filter({ hasText: 'Plume' })
    await expect(plumeRowAfter).toContainText('0.30')

    // Cleanup: reset temperature back to null so subsequent runs are stable.
    await plumeRowAfter.getByRole('link', { name: 'Edit' }).click()
    await page.getByLabel(/Use LiteLLM default/).click()  // re-check
    await page.getByRole('button', { name: 'Save' }).click()
    await expect(page).toHaveURL(/\/agents$/)
})
```

- [ ] **Step 2: Write `agents-validation.spec.ts`**

```typescript
import { test, expect } from './fixtures'

test('clearing prompt disables Save and turns counter red', async ({ signedInPage: page }) => {
    await page.goto('/agents/plume')
    await expect(page.getByRole('heading', { name: /Edit Plume/i })).toBeVisible()

    // Clear the prompt textarea.
    const prompt = page.getByLabel(/System prompt/i)
    await prompt.fill('')

    // Save disabled.
    await expect(page.getByRole('button', { name: 'Save' })).toBeDisabled()

    // Type a very long prompt — counter should turn red.
    await prompt.fill('x'.repeat(8001))
    const counter = page.getByText(/8,001 \/ 8,000/)
    await expect(counter).toBeVisible()
    // Counter has the red class (test via CSS color computed style).
    const color = await counter.evaluate((el) => getComputedStyle(el).color)
    expect(color).toMatch(/rgb\(220, 38, 38\)|rgb\(185, 28, 28\)/)  // Tailwind red-600 or red-700

    // Save also disabled at this state.
    await expect(page.getByRole('button', { name: 'Save' })).toBeDisabled()
})
```

- [ ] **Step 3: Run E2E locally (requires docker compose + `signedInPage` fixture setup)**

```bash
cd frontend
docker compose -f ../docker-compose.yml -f ../docker-compose.ci.yml up -d --build
npx playwright test agents-edit agents-validation --reporter=list
docker compose -f ../docker-compose.yml -f ../docker-compose.ci.yml down -v
```

Expected: 2 specs pass. If the slider ARIA interaction differs from the assumed shape, inspect via `--debug` and adapt the key presses.

- [ ] **Step 4: Commit**

```bash
git add frontend/tests/e2e/agents-edit.spec.ts frontend/tests/e2e/agents-validation.spec.ts
git commit -m "test(e2e): agents edit happy path + validation errors"
```

---

## Task 16: README + final sweep

**Files:**
- Modify: `README.md`

**Interfaces:** none.

- [ ] **Step 1: Add the README section**

Locate the existing "Twaky Frontend (sub-project 3b)" section and add a new section right after it:

````markdown
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
````

- [ ] **Step 2: Run the full-repo gate sweep**

```bash
cd /home/mmaudet/work/twaky
uv run ruff check .
uv run ruff format --check .
uv run mypy src/
uv run pytest -q
cd frontend
npm run typecheck
npm run lint
npm run test:unit
npm run build
make api-types && git diff --exit-code src/lib/api-types.d.ts
```

Expected: every step green. Baseline: 194 Python pytest + new tests from T1-T9 (approximately 30 new: T1 5 + T2 8 + T3 17 + T4 8 + T5 1 + T6 1 + T8 6 + T9 8 = ~54, but some existing agent tests may have been updated in T7 without adding count). Frontend: 45 baseline + T11 6 + T12 1 + T13 12 + T14 2 = ~66.

- [ ] **Step 3: Commit README + final sweep**

```bash
git add README.md
git commit -m "docs: README section on agent configuration + final sweep"
```

---

## Global test count expectation

At branch-end HEAD, running `uv run pytest -q` and `cd frontend && npm run test:unit` should produce:

- Python: baseline 194 passed / 32 skipped, **plus ~54 new tests** = ~248 passed / ~30 skipped.
- Frontend: baseline 45, **plus ~21 new tests** = ~66 tests.

If final counts diverge substantially, investigate — a missing new test file, or an unnoticed regression.

---

**End of implementation plan.**
