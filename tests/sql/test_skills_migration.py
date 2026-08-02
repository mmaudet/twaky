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
