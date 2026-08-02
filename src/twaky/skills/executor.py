"""Isolated subprocess executor for user-authored skills.

Each invocation forks a fresh multiprocessing.Process, applies rlimits
inside the child, execs the user source into a fresh namespace, calls
run(**args, **config), and pipes the pickled result back.

Isolation trade-offs are documented in the module docstring AND the README:
this is a SAFETY boundary (catches accidents), not a SECURITY boundary
(against a hostile owner — see spec §9.2).
"""

from __future__ import annotations

import contextlib
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


def _set_rlimits(memory_limit_mb: int, cpu_seconds: int) -> None:
    """Apply resource caps inside the child. Linux-only for RLIMIT_NPROC."""
    resource.setrlimit(
        resource.RLIMIT_AS, (memory_limit_mb * _MB, memory_limit_mb * _MB)
    )
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    # RLIMIT_NPROC on macOS counts parent's threads — setting it to 0 kills
    # the whole test harness. Only apply on Linux.
    if platform.system() == "Linux":
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))


def _dispose_inherited_pool() -> None:
    """Close the parent's psycopg pool in the child and clear the module handle.

    Fork inherits open TCP sockets — using them from the child would corrupt
    the parent's connection state. Closing releases the child's copies of
    those fds; clearing ``twaky.db._pool`` ensures any legitimate re-use in
    the child (via ``twaky.db.get_pool``) opens a fresh child-owned pool.
    """
    try:
        from twaky import db as _twaky_db
    except Exception:  # noqa: BLE001
        return
    pool = getattr(_twaky_db, "_pool", None)
    if pool is None:
        return
    with contextlib.suppress(Exception):
        pool.close()
    _twaky_db._pool = None


def _worker(
    pipe: mp.connection.Connection,
    python_source: str,
    args: dict,
    config: dict,
    memory_limit_mb: int,
    cpu_seconds: int,
) -> None:
    _dispose_inherited_pool()
    try:
        _set_rlimits(memory_limit_mb, cpu_seconds)
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
        raise SkillCrashed(f"skill exited with code {proc.exitcode} and sent no result")

    tag, value = payload
    if tag == "ok":
        return value
    if tag == "error":
        raise SkillError(str(value))
    raise SkillCrashed(f"unknown payload tag: {tag!r}")


__all__ = [
    "SkillCrashed",
    "SkillError",
    "SkillTimeout",
    "run_skill",
]
