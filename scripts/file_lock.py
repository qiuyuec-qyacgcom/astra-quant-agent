"""Shared non-blocking flock cycle guard for AstraQuant daemon entry points.

Single-cycle daemons (AI brain, factor trader, self-evolution) all guard their
entry point with the same open/flock/write-pid/unlock pattern. This module
provides the common pieces so each daemon only keeps its own skip handling.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from functools import wraps

import fcntl_compat as fcntl


class LockContended(Exception):
    """Raised when another process already holds the cycle lock."""


def write_pid_to_lock(lock_handle) -> None:
    """Record the current PID in the lock file (truncate + rewrite)."""
    lock_handle.seek(0)
    lock_handle.truncate()
    lock_handle.write(str(os.getpid()))
    lock_handle.flush()


@contextmanager
def cycle_lock(lock_path: str, ensure_dir: bool = True, write_pid: bool = True):
    """Hold an exclusive non-blocking flock on ``lock_path`` for one cycle.

    Raises ``LockContended`` immediately when another cycle is still running;
    the lock is always released and the handle closed on exit.
    """
    if ensure_dir:
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    lock_handle = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_handle.close()
        raise LockContended(lock_path) from None
    try:
        if write_pid:
            write_pid_to_lock(lock_handle)
        yield lock_handle
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def single_cycle(lock_path, on_skip=None, ensure_dir: bool = True, write_pid: bool = True):
    """Decorator: run ``func`` under ``cycle_lock``; skip (return None) on contention.

    ``lock_path`` may be a string or a zero-argument callable returning the
    path; pass a callable (e.g. ``lambda: LOCK_FILE``) when the path must be
    resolved at call time rather than captured at decoration time.
    ``on_skip`` is an optional zero-argument callback invoked when the cycle is
    skipped because another process holds the lock.
    """
    def decorator(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            path = lock_path() if callable(lock_path) else lock_path
            try:
                with cycle_lock(path, ensure_dir=ensure_dir, write_pid=write_pid):
                    return func(*args, **kwargs)
            except LockContended:
                if on_skip is not None:
                    on_skip()
                return None
        return wrapped
    return decorator
