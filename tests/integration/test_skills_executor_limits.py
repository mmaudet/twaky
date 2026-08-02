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


@pytest.mark.skipif(
    platform.system() != "Linux", reason="RLIMIT_AS reliable on Linux only"
)
def test_memory_limit_kills_allocator():
    # Try to allocate ~1 GB inside a 64 MB cap. Python raises MemoryError,
    # which the worker catches and reports as SkillError.
    src = "def run(**kwargs):\n    x = bytearray(1024 * 1024 * 1024)\n    return len(x)"
    with pytest.raises((SkillError, SkillTimeout)):
        run_skill(src, args={}, config={}, memory_limit_mb=64, timeout_s=5)


@pytest.mark.skipif(
    platform.system() != "Linux", reason="RLIMIT_NPROC not applied on macOS"
)
def test_fork_denied_by_nproc_limit():
    src = (
        "import subprocess\n"
        "def run(**kwargs):\n"
        "    return subprocess.check_output(['/bin/echo', 'hi'])"
    )
    with pytest.raises(SkillError) as exc:
        run_skill(src, args={}, config={}, timeout_s=5)
    # subprocess.Popen → fork() → EAGAIN when NPROC=0
    assert (
        "BlockingIOError" in str(exc.value)
        or "Resource" in str(exc.value)
        or "OSError" in str(exc.value)
    )
