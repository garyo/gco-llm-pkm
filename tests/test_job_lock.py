"""Tests for single-flight job locks and manual task triggers."""

import logging
import threading
from datetime import datetime
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pkm_bridge.database import Base, ScheduledTask, ScheduledTaskRun
from pkm_bridge.job_lock import job_lock, run_exclusive, start_exclusive
from pkm_bridge.scheduler.dispatcher import JOB_NAME, TaskDispatcher
from pkm_bridge.scheduler.repository import ScheduledTaskRunRepository


def _wait_until_unlocked(name: str) -> bool:
    """start_exclusive releases in its worker thread, just after fn returns."""
    for _ in range(500):
        if not job_lock(name).locked():
            return True
        threading.Event().wait(0.01)
    return False


def test_job_lock_is_shared_by_name():
    assert job_lock("a-job") is job_lock("a-job")
    assert job_lock("a-job") is not job_lock("another-job")


def test_run_exclusive_skips_while_busy():
    calls = []
    lock = job_lock("busy-job")
    with lock:
        assert run_exclusive("busy-job", calls.append, 1) is False
    assert calls == []
    assert run_exclusive("busy-job", calls.append, 2) is True
    assert calls == [2]
    assert not lock.locked()


def test_run_exclusive_releases_on_error():
    def boom():
        raise RuntimeError("x")

    try:
        run_exclusive("error-job", boom)
    except RuntimeError:
        pass
    assert not job_lock("error-job").locked()


def test_start_exclusive_refuses_second_start_until_first_finishes():
    release = threading.Event()
    done = threading.Event()

    def work():
        release.wait(5)
        done.set()

    assert start_exclusive("thread-job", work) is True
    assert start_exclusive("thread-job", work) is False
    release.set()
    assert done.wait(5)
    assert _wait_until_unlocked("thread-job")


def test_start_exclusive_releases_after_error():
    finished = threading.Event()

    def boom():
        try:
            raise RuntimeError("x")
        finally:
            finished.set()

    assert start_exclusive("thread-error-job", boom, logger=logging.getLogger("t"))
    assert finished.wait(5)
    assert _wait_until_unlocked("thread-error-job")


# ---------------------------------------------------------------------------
# Manual scheduled-task runs share the dispatcher's lock and rules
# ---------------------------------------------------------------------------


def _dispatcher() -> TaskDispatcher:
    return TaskDispatcher(MagicMock(), logging.getLogger("test"))


def _task(enabled: bool = True) -> MagicMock:
    task = MagicMock()
    task.id = 7
    task.name = "demo"
    task.enabled = enabled
    return task


def test_start_task_now_refuses_disabled_task():
    reason = _dispatcher().start_task_now(_task(enabled=False))
    assert reason and "disabled" in reason


@patch("pkm_bridge.scheduler.dispatcher.get_db")
def test_start_task_now_refuses_when_budget_spent(mock_get_db):
    dispatcher = _dispatcher()
    with patch.object(dispatcher, "_check_global_budget", return_value=False):
        reason = dispatcher.start_task_now(_task())
    assert reason and "budget" in reason


@patch("pkm_bridge.scheduler.dispatcher.get_db")
def test_start_task_now_refuses_while_a_task_runs(mock_get_db):
    dispatcher = _dispatcher()
    with patch.object(dispatcher, "_check_global_budget", return_value=True):
        with job_lock(JOB_NAME):
            reason = dispatcher.start_task_now(_task())
    assert reason and "running" in reason


@patch("pkm_bridge.scheduler.dispatcher.get_db")
def test_tick_skips_while_manual_run_holds_lock(mock_get_db):
    dispatcher = _dispatcher()
    with patch.object(dispatcher, "_run_due_tasks") as run_due:
        with job_lock(JOB_NAME):
            dispatcher.tick()
        run_due.assert_not_called()
        dispatcher.tick()
        run_due.assert_called_once()


def test_fail_interrupted_marks_only_runs_from_before_startup():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[ScheduledTask.__table__, ScheduledTaskRun.__table__])
    db = sessionmaker(bind=engine)()
    startup = datetime(2026, 9, 27, 12, 0)
    db.add(ScheduledTask(id=1, name="t", prompt="p", schedule_type="interval", schedule_expr="1h"))
    db.add_all(
        [
            ScheduledTaskRun(task_id=1, started_at=datetime(2026, 9, 27, 11), status="running"),
            ScheduledTaskRun(task_id=1, started_at=datetime(2026, 9, 27, 10), status="completed"),
            ScheduledTaskRun(task_id=1, started_at=datetime(2026, 9, 27, 12, 1), status="running"),
        ]
    )
    db.commit()

    assert ScheduledTaskRunRepository.fail_interrupted(db, startup) == 1

    statuses = {r.started_at.hour: (r.status, r.error) for r in db.query(ScheduledTaskRun)}
    assert statuses[11] == ("failed", "Interrupted by a server restart")
    assert statuses[10] == ("completed", None)
    assert statuses[12] == ("running", None)
