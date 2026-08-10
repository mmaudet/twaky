"""Static assertions on the sentinels migration script.

Runs without a live Postgres. Full DB behaviour is exercised in integration
tests once the container is available.
"""

from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "sql" / "008_init_sentinels.sh"


def test_script_exists_and_is_executable():
    assert SCRIPT.exists(), f"{SCRIPT} missing"
    assert SCRIPT.stat().st_mode & 0o111, f"{SCRIPT} not executable"


def test_creates_all_five_tables():
    text = SCRIPT.read_text()
    tables = [
        "CREATE TABLE IF NOT EXISTS public.sentinel",
        "CREATE TABLE IF NOT EXISTS public.sentinel_run",
        "CREATE TABLE IF NOT EXISTS public.mail_sentinel_rule",
        "CREATE TABLE IF NOT EXISTS public.mail_sentinel_memory",
        "CREATE TABLE IF NOT EXISTS public.mail_sentinel_learned_pattern",
    ]
    for table_ddl in tables:
        assert table_ddl in text, f"missing: {table_ddl!r}"


def test_sentinel_name_check_regex():
    text = SCRIPT.read_text()
    assert "name ~ '^[a-z][a-z0-9_-]{0,63}$'" in text


def test_notify_uses_pg_notify_function_form():
    """Regression guard for commit 1b7b58d: must use pg_notify() not NOTIFY."""
    text = SCRIPT.read_text()
    assert "pg_notify('sentinel_changed'" in text


def test_sentinel_run_outcome_check():
    text = SCRIPT.read_text()
    assert (
        "outcome IN ('ignored','processed','mission_created','delegated','error')"
        in text
    )


def test_mail_rule_conditions_jsonb_array_check():
    text = SCRIPT.read_text()
    assert "jsonb_typeof(conditions) = 'array'" in text


def test_memory_ttl_default():
    text = SCRIPT.read_text()
    assert "(now() + INTERVAL '7 days')" in text


def test_learned_pattern_confidence_numeric():
    text = SCRIPT.read_text()
    assert "NUMERIC(3,2)" in text
    assert "confidence >= 0" in text
    assert "confidence <= 1" in text


def test_seed_row_name_and_display_name():
    text = SCRIPT.read_text()
    assert "'mail'" in text
    assert "'Mail sentinel'" in text


def test_seed_row_pattern_confidence_threshold():
    text = SCRIPT.read_text()
    assert '"pattern_confidence_threshold": 0.9' in text


def test_seed_row_event_source():
    text = SCRIPT.read_text()
    assert '"event_source": "jmap_poll"' in text


def test_seed_row_memory_candidate_pool():
    text = SCRIPT.read_text()
    assert '"memory_candidate_pool":' in text


def test_seed_row_on_conflict_do_nothing():
    text = SCRIPT.read_text()
    assert "ON CONFLICT (name) DO NOTHING" in text


def test_indexes_present():
    text = SCRIPT.read_text()
    indexes = [
        "sentinel_run_by_sentinel_started",
        "sentinel_run_by_mission",
        "mail_sentinel_rule_priority",
        "mail_sentinel_memory_scope_lookup",
        "mail_sentinel_memory_ttl",
        "mail_sentinel_pattern_by_sender",
    ]
    for idx in indexes:
        assert idx in text, f"missing index: {idx!r}"


def test_triggers_present():
    text = SCRIPT.read_text()
    assert "sentinel_notify" in text
    assert "sentinel_touch_updated_at" in text
    assert "AFTER UPDATE ON public.sentinel" in text
    assert "BEFORE UPDATE ON public.sentinel" in text


def test_updated_at_trigger_body():
    text = SCRIPT.read_text()
    assert "NEW.updated_at := now()" in text
