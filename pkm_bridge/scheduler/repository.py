"""Database repositories for scheduled task models."""

import re
from datetime import datetime, timedelta, tzinfo
from typing import List, Optional

from sqlalchemy.orm import Session

from ..database import DailyTokenUsage, ScheduledTask, ScheduledTaskRun
from ..timezones import UTC, configured_timezone


def _parse_interval(expr: str) -> timedelta:
    """Parse an interval expression like '4h', '30m', '1d' into a timedelta."""
    match = re.match(r"^(\d+)\s*([smhd])$", expr.strip().lower())
    if not match:
        raise ValueError(f"Invalid interval expression: '{expr}'. Use e.g. '4h', '30m', '1d'.")
    value, unit = int(match.group(1)), match.group(2)
    units = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
    return timedelta(**{units[unit]: value})


def compute_next_run(
    task: ScheduledTask,
    after: datetime | None = None,
    anchor: datetime | None = None,
    tz: tzinfo | None = None,
) -> datetime:
    """Compute the next run time for a task based on its schedule.

    All times are naive UTC, as stored in the database.

    Args:
        task: The scheduled task.
        after: Compute next run after this time (defaults to now).
        anchor: For interval tasks, the time the interval ticks from (defaults
            to the last run, then the creation time).
        tz: Zone cron expressions are read in (defaults to the configured
            timezone), so '0 9 * * *' means 9am local across DST changes.

    Returns:
        The next datetime when this task should fire.
    """
    after = after or datetime.utcnow()

    if task.schedule_type == "interval":
        delta = _parse_interval(task.schedule_expr)
        base = anchor or task.last_run_at or task.created_at or after
        # Find the next interval tick after `after`
        if base >= after:
            return base + delta
        # Skip forward to the next tick
        elapsed = (after - base).total_seconds()
        interval_s = delta.total_seconds()
        ticks = int(elapsed / interval_s) + 1
        return base + timedelta(seconds=ticks * interval_s)

    elif task.schedule_type == "cron":
        from croniter import croniter

        # croniter steps aware datetimes with a fixed UTC offset, so across a DST
        # change it lands an hour off. Step in naive wall time and attach the zone
        # afterwards; the loop skips a slot that maps to the past in the repeated
        # fall-back hour.
        tz = tz or configured_timezone() or UTC
        local_after = after.replace(tzinfo=UTC).astimezone(tz).replace(tzinfo=None)
        cron = croniter(task.schedule_expr, local_after)
        while True:
            local_next = cron.get_next(datetime).replace(tzinfo=tz)
            next_utc = local_next.astimezone(UTC).replace(tzinfo=None)
            if next_utc > after:
                return next_utc

    else:
        raise ValueError(f"Unknown schedule_type: '{task.schedule_type}'")


class ScheduledTaskRepository:
    """CRUD operations for ScheduledTask."""

    @staticmethod
    def create(db: Session, **kwargs) -> ScheduledTask:
        task = ScheduledTask(**kwargs)
        db.add(task)
        db.flush()  # get ID before computing next_run
        task.next_run_at = compute_next_run(task)
        db.commit()
        db.refresh(task)
        return task

    @staticmethod
    def get_by_id(db: Session, task_id: int) -> Optional[ScheduledTask]:
        return db.query(ScheduledTask).filter_by(id=task_id).first()

    @staticmethod
    def get_by_name(db: Session, name: str) -> Optional[ScheduledTask]:
        return db.query(ScheduledTask).filter_by(name=name).first()

    @staticmethod
    def get_all(db: Session) -> List[ScheduledTask]:
        return db.query(ScheduledTask).order_by(ScheduledTask.id).all()

    @staticmethod
    def get_due(db: Session, now: datetime | None = None) -> List[ScheduledTask]:
        """Get enabled tasks whose next_run_at <= now."""
        now = now or datetime.utcnow()
        return (
            db.query(ScheduledTask)
            .filter(ScheduledTask.enabled.is_(True))
            .filter(ScheduledTask.next_run_at <= now)
            .order_by(ScheduledTask.next_run_at)
            .all()
        )

    @staticmethod
    def get_heartbeat(db: Session) -> Optional[ScheduledTask]:
        return db.query(ScheduledTask).filter_by(is_heartbeat=True).first()

    @staticmethod
    def update(db: Session, task_id: int, **kwargs) -> Optional[ScheduledTask]:
        task = db.query(ScheduledTask).filter_by(id=task_id).first()
        if not task:
            return None
        for key, value in kwargs.items():
            if hasattr(task, key):
                setattr(task, key, value)
        # Recompute next_run if schedule changed
        if "schedule_type" in kwargs or "schedule_expr" in kwargs:
            task.next_run_at = compute_next_run(task)
        task.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(task)
        return task

    @staticmethod
    def delete(db: Session, task_id: int) -> bool:
        task = db.query(ScheduledTask).filter_by(id=task_id).first()
        if not task:
            return False
        db.delete(task)
        db.commit()
        return True

    @staticmethod
    def mark_run(db: Session, task: ScheduledTask) -> None:
        """Update last_run_at and advance next_run_at after a run.

        Interval tasks tick from the slot that just fired rather than from when
        the run finished, so they keep their time of day instead of drifting
        later by the run's duration each time. A manual run before the slot
        leaves the schedule alone.
        """
        now = datetime.utcnow()
        task.last_run_at = now
        if task.next_run_at is None or task.next_run_at <= now:
            task.next_run_at = compute_next_run(task, after=now, anchor=task.next_run_at)
        db.commit()


class ScheduledTaskRunRepository:
    """CRUD for run logs."""

    @staticmethod
    def create(db: Session, **kwargs) -> ScheduledTaskRun:
        run = ScheduledTaskRun(**kwargs)
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    @staticmethod
    def update(db: Session, run_id: int, **kwargs) -> Optional[ScheduledTaskRun]:
        run = db.query(ScheduledTaskRun).filter_by(id=run_id).first()
        if not run:
            return None
        for key, value in kwargs.items():
            if hasattr(run, key):
                setattr(run, key, value)
        db.commit()
        db.refresh(run)
        return run

    @staticmethod
    def get_recent(
        db: Session, limit: int = 20, task_id: int | None = None
    ) -> List[ScheduledTaskRun]:
        q = db.query(ScheduledTaskRun)
        if task_id is not None:
            q = q.filter_by(task_id=task_id)
        return q.order_by(ScheduledTaskRun.started_at.desc()).limit(limit).all()

    @staticmethod
    def fail_interrupted(db: Session, started_before: datetime) -> int:
        """Mark runs still 'running' that began before `started_before` as failed.

        A run row is finished by the process that started it, so rows left
        'running' from before the server started were cut off by a crash or
        restart.
        """
        count = (
            db.query(ScheduledTaskRun)
            .filter(ScheduledTaskRun.status == "running")
            .filter(ScheduledTaskRun.started_at < started_before)
            .update(
                {
                    ScheduledTaskRun.status: "failed",
                    ScheduledTaskRun.completed_at: datetime.utcnow(),
                    ScheduledTaskRun.error: "Interrupted by a server restart",
                },
                synchronize_session=False,
            )
        )
        db.commit()
        return count


class DailyTokenUsageRepository:
    """Track aggregate daily token usage for budget enforcement."""

    @staticmethod
    def get_today(db: Session) -> DailyTokenUsage:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        row = db.query(DailyTokenUsage).filter_by(date=today).first()
        if not row:
            row = DailyTokenUsage(
                date=today,
                input_tokens=0,
                output_tokens=0,
                cache_write_tokens=0,
                cache_read_tokens=0,
                cost_usd=0.0,
                task_runs=0,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
        return row

    @staticmethod
    def record_usage(
        db: Session,
        input_tokens: int,
        output_tokens: int,
        cache_write_tokens: int = 0,
        cache_read_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> DailyTokenUsage:
        row = DailyTokenUsageRepository.get_today(db)
        row.input_tokens += input_tokens
        row.output_tokens += output_tokens
        row.cache_write_tokens += cache_write_tokens
        row.cache_read_tokens += cache_read_tokens
        row.cost_usd += cost_usd
        row.task_runs += 1
        db.commit()
        db.refresh(row)
        return row
