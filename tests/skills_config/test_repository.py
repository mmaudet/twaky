"""CRUD tests for skills_config.repository. Uses a real Postgres connection."""

from __future__ import annotations

import os

import psycopg
import pytest

from twaky.config import settings
from twaky.skills_config import repository as repo
from twaky.skills_config.repository import SkillNameConflict, SkillNotFound


def _dsn() -> str:
    return os.environ.get("TWAKY_TEST_DSN") or settings.pg_dsn


def _reachable() -> bool:
    try:
        with psycopg.connect(_dsn(), connect_timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _reachable(), reason="twaky-pg not reachable"),
]


@pytest.fixture(autouse=True)
def _clean_skills():
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM skill")
    yield
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM skill")


def _mk(**overrides):
    defaults = {
        "name": "echo",
        "description": "Echo tool",
        "python_source": "def run(**kwargs):\n    return str(kwargs)",
        "config_schema": {},
        "config_values": {},
        "bound_agents": ["atlas"],
        "enabled": True,
    }
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
