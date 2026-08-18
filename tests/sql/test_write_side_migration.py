"""Static assertions on the SP5b write-side migration script."""

from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "sql" / "012_init_write_side.sh"


def test_script_exists_and_is_executable():
    assert SCRIPT.exists(), f"{SCRIPT} missing"
    assert SCRIPT.stat().st_mode & 0o111, f"{SCRIPT} not executable"


def test_alters_mail_sentinel_memory_with_four_columns():
    text = SCRIPT.read_text()
    for expected in (
        "ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'manual'",
        "ADD COLUMN IF NOT EXISTS sender_email TEXT",
        "ADD COLUMN IF NOT EXISTS mission_id UUID",
        "ADD COLUMN IF NOT EXISTS confidence NUMERIC(3,2)",
    ):
        assert expected in text, f"missing: {expected!r}"


def test_drops_not_null_on_expires_at():
    """`expires_at` must be nullable so 'Keep permanent' can set it to NULL."""
    text = SCRIPT.read_text()
    assert "ALTER COLUMN expires_at DROP NOT NULL" in text


def test_source_check_constraint():
    text = SCRIPT.read_text()
    assert "source IN ('manual','auto_diff','auto_reclass','auto_move')" in text


def test_creates_mailbox_state_table():
    text = SCRIPT.read_text()
    assert "CREATE TABLE IF NOT EXISTS public.mail_sentinel_mailbox_state" in text
    assert "mailbox_id  TEXT PRIMARY KEY" in text
    assert "jmap_state  TEXT NOT NULL" in text


def test_creates_observation_table_with_unique():
    text = SCRIPT.read_text()
    assert "CREATE TABLE IF NOT EXISTS public.mail_sentinel_observation" in text
    assert "observation_type   TEXT NOT NULL" in text
    assert "extraction_outcome TEXT NOT NULL" in text
    assert "UNIQUE (email_id, mailbox_id, observation_type)" in text


def test_observation_outcome_check():
    text = SCRIPT.read_text()
    assert (
        "extraction_outcome IN ('extracted','skipped_trivial','skipped_no_match','error')"
        in text
    )


def test_mission_id_fk_on_delete_set_null():
    text = SCRIPT.read_text()
    assert "REFERENCES public.mission(id) ON DELETE SET NULL" in text
