"""Health watchdog: notices quiet failures and pushes them via ntfy.

Runs a few times a day from APScheduler and makes no model calls. It reads
the database and the disk, evaluates a fixed set of checks, and pushes one
ntfy message listing the problems that haven't already been pushed recently.

Checks are pure functions over a Snapshot so they can be tested with fake
rows and a fixed clock; gather_snapshot() is the only part that reads state.
"""

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import ntfy
from .database import (
    AgentRunLog,
    NoteProposal,
    OAuthToken,
    OpsState,
    ScheduledTask,
    ScheduledTaskRun,
    get_db,
)
from .scheduler.repository import _parse_interval

logger = logging.getLogger("pkm_bridge.watchdog")

# A job is stale once it has missed two consecutive scheduled runs; GRACE
# gives a run that is due right now time to finish.
GRACE = timedelta(hours=1)
RUN_LOOKBACK = timedelta(hours=24)
HUNG_AFTER = timedelta(hours=3)
TOKEN_WARN_BEFORE = timedelta(days=14)
TOKEN_URGENT_BEFORE = timedelta(days=3)
CURATOR_IDLE_RUNS = 3
EMBEDDING_STALE_AFTER = timedelta(hours=3)
DISK_USED_LIMIT = 0.80
RENOTIFY_AFTER = timedelta(days=1)

SELF_IMPROVEMENT_JOB = "self-improvement"
SELF_IMPROVEMENT_SCHEDULE = "2d"
EMBEDDING_JOB = "embedding"

SERVICE_LABELS = {
    "ticktick": "TickTick",
    "google_calendar": "Google Calendar",
    "google_gmail": "Gmail",
}


@dataclass(frozen=True)
class Alert:
    key: str  # identifies the condition, for deduplication
    message: str
    renotify_after: timedelta = RENOTIFY_AFTER


@dataclass
class JobStatus:
    name: str
    schedule_type: str  # 'interval' or 'cron'
    schedule_expr: str
    last_success: datetime | None
    # Server start (or task creation, if later): downtime isn't the job's fault,
    # so a job is judged from here at the earliest.
    since: datetime


@dataclass
class RunRecord:
    job: str
    started_at: datetime
    status: str  # 'running', 'failed', or anything else that isn't 'completed'
    error: str | None = None


@dataclass
class TokenStatus:
    service: str
    expires_at: datetime | None
    has_refresh_token: bool
    refresh_error: str | None = None


@dataclass
class Snapshot:
    """Everything the checks look at. Datetimes are naive UTC, like the DB."""

    now: datetime
    timezone: tzinfo | None = None  # for cron expressions
    jobs: list[JobStatus] = field(default_factory=list)
    unsuccessful_runs: list[RunRecord] = field(default_factory=list)
    tokens: list[TokenStatus] = field(default_factory=list)
    curator_run_starts: list[datetime] = field(default_factory=list)  # newest first
    last_curator_proposal: datetime | None = None
    embedding_expected: bool = False
    embedding_last_run: datetime | None = None
    embedding_last_errors: int = 0
    embedding_since: datetime | None = None  # server start
    disk_used: dict[str, float] = field(default_factory=dict)  # path -> fraction used


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _fmt(when: datetime) -> str:
    return when.strftime("%Y-%m-%d %H:%M UTC")


def describe_failure(status: str, error: str | None) -> str:
    """A short reason for a run that didn't complete."""
    if error and "max_tokens" in error:
        return "cut off at max_tokens"
    if error:
        return error.strip().splitlines()[0][:160]
    return status


def success_deadline(job: JobStatus, now: datetime, tz: tzinfo | None) -> datetime:
    """A last success before this time means the job missed two scheduled runs."""
    if job.schedule_type == "cron":
        from croniter import croniter

        local_now = (now - GRACE).replace(tzinfo=timezone.utc).astimezone(tz)
        cron = croniter(job.schedule_expr, local_now)
        cron.get_prev(datetime)
        two_back = cron.get_prev(datetime)
        return two_back.astimezone(timezone.utc).replace(tzinfo=None)
    return now - GRACE - 2 * _parse_interval(job.schedule_expr)


def check_stale_jobs(snap: Snapshot) -> list[Alert]:
    alerts = []
    for job in snap.jobs:
        baseline = max(job.last_success or job.since, job.since)
        try:
            deadline = success_deadline(job, snap.now, snap.timezone)
        except Exception as e:
            alerts.append(Alert(f"bad_schedule:{job.name}", f"{job.name}: bad schedule ({e})"))
            continue
        if baseline < deadline:
            what = f"last success {_fmt(job.last_success)}" if job.last_success else "never ran"
            alerts.append(
                Alert(
                    f"stale:{job.name}",
                    f"{job.name} has missed its last two runs ({job.schedule_expr}; {what})",
                )
            )
    return alerts


def failure_key(job: str) -> str:
    """Shared with the dispatcher's immediate push, so a failure is pushed once."""
    return f"run_failed:{job}"


def check_runs(snap: Snapshot) -> list[Alert]:
    """Failed, cut-off and hung runs, one alert per job."""
    failures: dict[str, list[RunRecord]] = {}
    alerts = []
    for run in snap.unsuccessful_runs:
        if run.status == "running":
            if snap.now - run.started_at > HUNG_AFTER:
                alerts.append(
                    Alert(
                        f"hung:{run.job}",
                        f"{run.job} has been running since {_fmt(run.started_at)}",
                    )
                )
        else:
            failures.setdefault(run.job, []).append(run)
    for job, runs in failures.items():
        latest = max(runs, key=lambda r: r.started_at)
        times = f" {len(runs)} times" if len(runs) > 1 else ""
        reason = describe_failure(latest.status, latest.error)
        alerts.append(Alert(failure_key(job), f"{job} failed{times} in the last day: {reason}"))
    return alerts


def check_tokens(snap: Snapshot) -> list[Alert]:
    alerts = []
    for token in snap.tokens:
        label = SERVICE_LABELS.get(token.service, token.service)
        if token.refresh_error:
            alerts.append(
                Alert(
                    f"oauth_rejected:{token.service}",
                    f"{label} refused to refresh its token; re-authorize it in Settings",
                )
            )
            continue
        if token.has_refresh_token or token.expires_at is None:
            continue
        left = token.expires_at - snap.now
        if left <= timedelta(0):
            alerts.append(
                Alert(
                    f"oauth_expired:{token.service}",
                    f"{label} access expired {_fmt(token.expires_at)} and cannot refresh; "
                    "re-authorize it in Settings",
                )
            )
        elif left <= TOKEN_WARN_BEFORE:
            urgent = left <= TOKEN_URGENT_BEFORE
            alerts.append(
                Alert(
                    f"oauth_expiring:{token.service}:{'3d' if urgent else '14d'}",
                    f"{label} access expires {_fmt(token.expires_at)} ({left.days} days) "
                    "and cannot refresh; re-authorize it in Settings",
                    renotify_after=RENOTIFY_AFTER if urgent else timedelta(days=7),
                )
            )
    return alerts


def check_curator(snap: Snapshot) -> list[Alert]:
    runs = snap.curator_run_starts
    if len(runs) < CURATOR_IDLE_RUNS:
        return []
    window_start = runs[CURATOR_IDLE_RUNS - 1]
    if snap.last_curator_proposal and snap.last_curator_proposal >= window_start:
        return []
    last = _fmt(snap.last_curator_proposal) if snap.last_curator_proposal else "never"
    return [
        Alert(
            "curator_idle",
            f"The note curator filed no proposals in its last {CURATOR_IDLE_RUNS} runs "
            f"(last proposal: {last})",
        )
    ]


def check_embedding(snap: Snapshot) -> list[Alert]:
    if not snap.embedding_expected:
        return []
    baseline = max(filter(None, (snap.embedding_last_run, snap.embedding_since)), default=None)
    if baseline and snap.now - baseline > EMBEDDING_STALE_AFTER:
        last = _fmt(snap.embedding_last_run) if snap.embedding_last_run else "not since startup"
        return [Alert("embedding_stale", f"Embedding hasn't finished a run recently ({last})")]
    if snap.embedding_last_errors:
        return [
            Alert(
                "embedding_errors",
                f"The last embedding run had {snap.embedding_last_errors} file errors",
            )
        ]
    return []


def check_disks(snap: Snapshot) -> list[Alert]:
    return [
        Alert(f"disk:{path}", f"Disk holding {path} is {used:.0%} full")
        for path, used in snap.disk_used.items()
        if used > DISK_USED_LIMIT
    ]


CHECKS = (check_stale_jobs, check_runs, check_tokens, check_curator, check_embedding, check_disks)


def evaluate(snap: Snapshot) -> list[Alert]:
    return [alert for check in CHECKS for alert in check(snap)]


def due_alerts(
    alerts: Iterable[Alert], last_pushed: dict[str, datetime], now: datetime
) -> list[Alert]:
    """The alerts not already pushed within their renotify window."""
    return [
        a
        for a in alerts
        if a.key not in last_pushed or now - last_pushed[a.key] >= a.renotify_after
    ]


# ---------------------------------------------------------------------------
# State: alert ledger and job heartbeats (ops_state table)
# ---------------------------------------------------------------------------

ALERT_PREFIX = "alert:"
JOB_PREFIX = "job:"


def _put_state(db: Session, key: str, at: datetime, detail: str | None = None) -> None:
    row = db.get(OpsState, key)
    if row:
        row.at, row.detail = at, detail
    else:
        db.add(OpsState(key=key, at=at, detail=detail))


def record_job_finished(name: str, detail: dict | None = None) -> None:
    """Note that a background job just finished a run (read by the watchdog)."""
    db = get_db()
    try:
        _put_state(db, JOB_PREFIX + name, datetime.utcnow(), json.dumps(detail or {}))
        db.commit()
    finally:
        db.close()


def push_new_alerts(db: Session, alerts: list[Alert], now: datetime) -> list[Alert]:
    """Push alerts not pushed recently as one ntfy message; return what was pushed.

    Without ntfy configured the alerts are only logged, and not recorded as
    pushed, so they go out once ntfy is set up.
    """
    rows = db.query(OpsState).filter(OpsState.key.startswith(ALERT_PREFIX)).all()
    last_pushed = {row.key[len(ALERT_PREFIX) :]: row.at for row in rows}
    due = due_alerts(alerts, last_pushed, now)
    if not due:
        return []

    body = "\n".join(f"• {a.message}" for a in due)
    if not os.getenv("NTFY_TOPIC", "").strip():
        logger.warning(f"Watchdog (ntfy not configured):\n{body}")
        return []
    try:
        ntfy.send(body, title="PKM needs attention", tags=["warning"])
    except ntfy.NtfyError as e:
        logger.error(f"Watchdog could not push alerts: {e}\n{body}")
        return []

    for alert in due:
        _put_state(db, ALERT_PREFIX + alert.key, now, alert.message)
    db.commit()
    return due


def notify_run_failure(job: str, status: str, error: str | None) -> None:
    """Push a failed scheduled run right away (deduplicated with the watchdog)."""
    alert = Alert(failure_key(job), f"{job} failed: {describe_failure(status, error)}")
    try:
        db = get_db()
        try:
            push_new_alerts(db, [alert], datetime.utcnow())
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Failed to push run failure for {job}: {e}")


# ---------------------------------------------------------------------------
# Gathering
# ---------------------------------------------------------------------------


def gather_snapshot(
    db: Session,
    now: datetime,
    *,
    tz: tzinfo | None,
    started_at: datetime,
    scheduled_tasks: bool,
    self_improvement: bool,
    embedding: bool,
    disk_paths: Iterable[str],
) -> Snapshot:
    """Read everything the checks need. `started_at` is when this server started."""
    from .curation.task import CURATION_TASK_NAME

    snap = Snapshot(now=now, timezone=tz)
    since = now - RUN_LOOKBACK

    if scheduled_tasks:
        last_success = dict(
            db.query(ScheduledTaskRun.task_id, func.max(ScheduledTaskRun.completed_at))
            .filter(ScheduledTaskRun.status == "completed")
            .group_by(ScheduledTaskRun.task_id)
            .all()
        )
        for task in db.query(ScheduledTask).filter(ScheduledTask.enabled.is_(True)):
            snap.jobs.append(
                JobStatus(
                    name=task.name,
                    schedule_type=task.schedule_type,
                    schedule_expr=task.schedule_expr,
                    last_success=last_success.get(task.id),
                    since=max(task.created_at or started_at, started_at),
                )
            )
        rows = (
            db.query(ScheduledTask.name, ScheduledTaskRun)
            .join(ScheduledTask, ScheduledTask.id == ScheduledTaskRun.task_id)
            .filter(ScheduledTaskRun.status != "completed")
            .filter(ScheduledTaskRun.started_at >= since)
            .all()
        )
        snap.unsuccessful_runs += [
            RunRecord(name, run.started_at, run.status or "unknown", run.error)
            for name, run in rows
        ]
        curator = db.query(ScheduledTask).filter_by(name=CURATION_TASK_NAME).first()
        if curator and curator.enabled:
            snap.curator_run_starts = [
                started
                for (started,) in db.query(ScheduledTaskRun.started_at)
                .filter(ScheduledTaskRun.task_id == curator.id)
                .filter(ScheduledTaskRun.status == "completed")
                .order_by(ScheduledTaskRun.started_at.desc())
                .limit(CURATOR_IDLE_RUNS)
            ]
            snap.last_curator_proposal = (
                db.query(func.max(NoteProposal.created_at))
                .filter(NoteProposal.source == "curator")
                .scalar()
            )

    if self_improvement:
        last_ok = (
            db.query(func.max(AgentRunLog.started_at)).filter(AgentRunLog.error.is_(None)).scalar()
        )
        snap.jobs.append(
            JobStatus(
                SELF_IMPROVEMENT_JOB, "interval", SELF_IMPROVEMENT_SCHEDULE, last_ok, started_at
            )
        )
        snap.unsuccessful_runs += [
            RunRecord(SELF_IMPROVEMENT_JOB, run.started_at, "failed", run.error)
            for run in db.query(AgentRunLog)
            .filter(AgentRunLog.error.is_not(None))
            .filter(AgentRunLog.started_at >= since)
        ]

    for token in db.query(OAuthToken):
        snap.tokens.append(
            TokenStatus(
                token.service, token.expires_at, bool(token.refresh_token), token.refresh_error
            )
        )

    if embedding:
        snap.embedding_expected = True
        snap.embedding_since = started_at
        row = db.get(OpsState, JOB_PREFIX + EMBEDDING_JOB)
        if row:
            snap.embedding_last_run = row.at
            snap.embedding_last_errors = json.loads(row.detail or "{}").get("error_count", 0)

    for path in dict.fromkeys(str(p) for p in disk_paths):
        try:
            usage = shutil.disk_usage(path)
            # As df reports it: blocks reserved for root count as unavailable.
            snap.disk_used[path] = usage.used / (usage.used + usage.free)
        except OSError as e:
            logger.warning(f"Watchdog could not stat {path}: {e}")

    return snap


def run_watchdog(**gather_kwargs) -> list[Alert]:
    """Check health and push new problems. Takes gather_snapshot's keyword args."""
    now = datetime.utcnow()
    db = get_db()
    try:
        alerts = evaluate(gather_snapshot(db, now, **gather_kwargs))
        pushed = push_new_alerts(db, alerts, now)
    finally:
        db.close()
    logger.info(
        f"Watchdog: {len(alerts)} problem(s), {len(pushed)} pushed"
        + "".join(f"\n  - {a.message}" for a in alerts)
    )
    return pushed
