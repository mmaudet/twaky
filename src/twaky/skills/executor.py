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
import time
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
    """Clear the inherited psycopg pool handle in the child, WITHOUT closing it.

    Fork inherits open TCP sockets AND the ConnectionPool Python object. We
    want the child to not use those sockets (they're shared with parent —
    using them would corrupt parent state). But calling ``pool.close()`` in
    the child sends PQfinish to Postgres, which server-side CLOSES the
    sessions — killing the parent's pool too. Observed in prod as
    'OperationalError: consuming input failed: server closed the connection'
    on the next parent DB call after a skill invocation.

    Correct approach: only NULL the module-level ``_pool`` reference so any
    legitimate re-use of ``twaky.db.get_pool()`` in the child opens a fresh
    child-owned pool. The child's inherited fds become unreferenced and are
    reclaimed when the child exits seconds later — leaking a handful of fds
    for the child's brief lifetime is safer than closing the shared sockets.
    """
    try:
        from twaky import db as _twaky_db
    except Exception:  # noqa: BLE001
        return
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

    deadline = time.monotonic() + timeout_s

    # Drain the pipe WHILE the child runs, never after joining it. ``pipe.send``
    # blocks in the child once the OS pipe buffer fills (64 KiB on Linux) and
    # only unblocks when the parent reads. Joining first therefore deadlocks on
    # any result larger than the buffer: the child waits for a reader that is
    # itself waiting for the child, until the timeout fires and reports a
    # perfectly successful skill as SkillTimeout.
    #
    # ``poll`` also returns True on EOF (child died without sending), which the
    # EOFError branch below turns into the SkillCrashed path.
    payload: tuple[str, Any] | None = None
    try:
        if parent_conn.poll(max(0.0, deadline - time.monotonic())):
            try:
                payload = parent_conn.recv()
            except EOFError:
                payload = None
    finally:
        parent_conn.close()

    # Reap. With a payload in hand the work is already done, so a child that
    # lingers past the deadline gets killed without discarding its result.
    proc.join(timeout=max(0.0, deadline - time.monotonic()))
    if proc.is_alive():
        proc.terminate()
        proc.join(3)
        if proc.is_alive():
            proc.kill()
            proc.join(3)
        if payload is None:
            raise SkillTimeout(f"skill timed out after {timeout_s}s")
        log.warning(
            "skill sent a result but did not exit within %ss; killed it and "
            "kept the result",
            timeout_s,
        )

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
