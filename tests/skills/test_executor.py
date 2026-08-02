"""Unit tests for skills.executor. No DB, no Postgres."""

from __future__ import annotations

import pytest

from twaky.skills.executor import (
    SkillCrashed,
    SkillError,
    SkillTimeout,
    _dispose_inherited_pool,
    run_skill,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def test_dispose_inherited_pool_closes_and_clears(monkeypatch):
    """The helper must call .close() on the inherited pool AND clear the module handle."""
    from twaky import db as _twaky_db

    class _FakePool:
        closed = False

        def close(self):
            self.closed = True

    fake = _FakePool()
    monkeypatch.setattr(_twaky_db, "_pool", fake)

    _dispose_inherited_pool()

    assert fake.closed is True
    assert _twaky_db._pool is None


def test_dispose_inherited_pool_no_op_when_pool_missing(monkeypatch):
    """Absent pool must not raise."""
    from twaky import db as _twaky_db

    monkeypatch.setattr(_twaky_db, "_pool", None)
    _dispose_inherited_pool()  # must not raise
    assert _twaky_db._pool is None


def test_dispose_inherited_pool_swallows_close_errors(monkeypatch):
    """A raising .close() must not propagate — child startup must remain robust."""
    from twaky import db as _twaky_db

    class _RaisingPool:
        def close(self):
            raise RuntimeError("cannot close")

    monkeypatch.setattr(_twaky_db, "_pool", _RaisingPool())
    _dispose_inherited_pool()  # must not raise
    assert _twaky_db._pool is None


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
    src = "import threading\ndef run(**kwargs):\n    return threading.Lock()"
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
