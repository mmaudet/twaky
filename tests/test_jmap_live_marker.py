"""Meta-tests for the jmap_live marker and auto-skip machinery.

Tests that the infrastructure wired in tests/conftest.py correctly gates
``@pytest.mark.jmap_live`` tests behind the TWAKY_JMAP_LIVE env flag.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

from tests.conftest import _jmap_live_enabled  # type: ignore[attr-defined]

# The sub-pytest must run from the repo root so its rootdir, conftest and
# relative test paths resolve. Derived from this file, never hardcoded: an
# absolute developer path fails everywhere else, CI included.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Unit tests on the helper — imported directly from conftest
# ---------------------------------------------------------------------------


def test_jmap_live_enabled_false_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """_jmap_live_enabled returns False when TWAKY_JMAP_LIVE is absent."""
    monkeypatch.delenv("TWAKY_JMAP_LIVE", raising=False)
    assert _jmap_live_enabled() is False


def test_jmap_live_enabled_false_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """_jmap_live_enabled returns False when TWAKY_JMAP_LIVE is empty string."""
    monkeypatch.setenv("TWAKY_JMAP_LIVE", "")
    assert _jmap_live_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "yes", "TRUE", "YES"])
def test_jmap_live_enabled_true_variants(
    val: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_jmap_live_enabled returns True for each accepted opt-in value."""
    monkeypatch.setenv("TWAKY_JMAP_LIVE", val)
    assert _jmap_live_enabled() is True


# ---------------------------------------------------------------------------
# Subprocess tests: verify the skip machinery works end-to-end via pytest
# ---------------------------------------------------------------------------


def _run_pytest(*args: str, env_overrides: dict[str, str] | None = None) -> str:
    """Run pytest in a subprocess and return stdout + stderr combined."""
    env = os.environ.copy()
    # Ensure TWAKY_JMAP_LIVE is unset by default unless overridden.
    env.pop("TWAKY_JMAP_LIVE", None)
    if env_overrides:
        for k, v in env_overrides.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *args,
            "-v",
            "--tb=short",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=_REPO_ROOT,
        check=False,
    )
    return result.stdout + result.stderr


def test_jmap_live_skips_when_env_unset() -> None:
    """Demo test reports SKIPPED (not FAILED) when TWAKY_JMAP_LIVE is unset."""
    output = _run_pytest(
        "tests/integration/test_jmap_roundtrip_live.py",
        env_overrides={"TWAKY_JMAP_LIVE": None},
    )
    assert "SKIPPED" in output, (
        f"Expected SKIPPED in output when TWAKY_JMAP_LIVE unset.\nOutput:\n{output}"
    )
    assert "FAILED" not in output, f"Unexpected FAILED in output.\nOutput:\n{output}"


def test_jmap_live_would_fail_without_env_vars_when_enabled() -> None:
    """When TWAKY_JMAP_LIVE=1 but JMAP_ENDPOINT is unset, fixture calls pytest.fail.

    pytest.fail inside a fixture surfaces as ERROR (not FAILED), so we check
    that the run does NOT report PASSED and does report an error state.
    """
    output = _run_pytest(
        "tests/integration/test_jmap_roundtrip_live.py",
        env_overrides={
            "TWAKY_JMAP_LIVE": "1",
            "JMAP_ENDPOINT": None,
            "JMAP_ACCOUNT_ID": None,
            "JMAP_TOKEN": None,
        },
    )
    # pytest.fail in a fixture appears as ERROR in the output
    assert "ERROR" in output or "FAILED" in output, (
        f"Expected ERROR or FAILED when JMAP_ENDPOINT missing with TWAKY_JMAP_LIVE=1.\n"
        f"Output:\n{output}"
    )
    assert "passed" not in output.lower() or "0 passed" in output, (
        f"Test should not have passed.\nOutput:\n{output}"
    )
