"""Single-flight locks for background jobs.

A job (embedding, self-improvement, scheduled tasks) can start from its
schedule or from an admin trigger. Both paths take the job's named lock
without blocking, so a second start while one is running is skipped or
refused instead of running the same work twice in parallel.
"""

import logging
import threading
from typing import Any, Callable

_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def job_lock(name: str) -> threading.Lock:
    """The process-wide lock for the job called `name`."""
    with _guard:
        return _locks.setdefault(name, threading.Lock())


def run_exclusive(
    name: str, fn: Callable[..., Any], *args: Any, logger: logging.Logger | None = None
) -> bool:
    """Run fn(*args) in this thread unless job `name` is already running.

    Returns False (without running fn) when the job was busy.
    """
    lock = job_lock(name)
    if not lock.acquire(blocking=False):
        if logger:
            logger.info(f"Skipping {name}: already running")
        return False
    try:
        fn(*args)
    finally:
        lock.release()
    return True


def start_exclusive(
    name: str, fn: Callable[..., Any], *args: Any, logger: logging.Logger | None = None
) -> bool:
    """Start fn(*args) in a daemon thread unless job `name` is already running.

    The lock is taken here, in the caller's thread, so the caller learns
    immediately whether the job started (e.g. to answer 409 Busy).
    """
    lock = job_lock(name)
    if not lock.acquire(blocking=False):
        return False

    def run() -> None:
        try:
            fn(*args)
        except Exception as e:
            if logger:
                logger.error(f"{name} failed: {e}", exc_info=True)
        finally:
            lock.release()

    try:
        threading.Thread(target=run, name=f"job-{name}", daemon=True).start()
    except Exception:
        lock.release()
        raise
    return True
