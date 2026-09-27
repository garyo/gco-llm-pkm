"""Tests for the health watchdog: checks on a fixed clock, dedup, and gathering."""

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pkm_bridge import watchdog
from pkm_bridge.database import (
    AgentRunLog,
    Base,
    NoteProposal,
    OAuthToken,
    OpsState,
    ScheduledTask,
    ScheduledTaskRun,
)
from pkm_bridge.watchdog import (
    Alert,
    JobStatus,
    RunRecord,
    Snapshot,
    TokenStatus,
    due_alerts,
    evaluate,
)

NOW = datetime(2026, 9, 27, 18, 0)  # naive UTC, like the DB
STARTED = NOW - timedelta(days=30)
NY = ZoneInfo("America/New_York")


def keys(alerts: list[Alert]) -> set[str]:
    return {a.key for a in alerts}


def snap(**kwargs) -> Snapshot:
    return Snapshot(now=NOW, timezone=NY, **kwargs)


# ---------------------------------------------------------------------------
# Stale jobs
# ---------------------------------------------------------------------------


def interval_job(last_success: datetime | None, expr: str = "12h") -> JobStatus:
    return JobStatus("heartbeat", "interval", expr, last_success, STARTED)


def test_interval_job_within_two_periods_is_fine():
    assert evaluate(snap(jobs=[interval_job(NOW - timedelta(hours=24))])) == []


def test_interval_job_missing_two_periods_is_stale():
    alerts = evaluate(snap(jobs=[interval_job(NOW - timedelta(hours=26))]))
    assert keys(alerts) == {"stale:heartbeat"}


def test_job_is_judged_from_server_start_at_the_earliest():
    job = JobStatus(
        "heartbeat", "interval", "12h", NOW - timedelta(days=5), NOW - timedelta(hours=2)
    )
    assert evaluate(snap(jobs=[job])) == []


def test_never_run_job_goes_stale_after_two_periods_from_since():
    job = JobStatus("new", "interval", "1d", None, NOW - timedelta(days=3))
    alerts = evaluate(snap(jobs=[job]))
    assert keys(alerts) == {"stale:new"}
    assert "never ran" in alerts[0].message


def test_weekly_cron_is_stale_only_after_missing_two_runs():
    # Sundays at 21:00 New York time; NOW is Sunday 14:00 there, before this week's run.
    ok = JobStatus("review", "cron", "0 21 * * 0", datetime(2026, 9, 21, 1, 5), STARTED)
    assert evaluate(snap(jobs=[ok])) == []
    missed_one = JobStatus("review", "cron", "0 21 * * 0", datetime(2026, 9, 14, 1, 5), STARTED)
    assert evaluate(snap(jobs=[missed_one])) == []
    stale = JobStatus("review", "cron", "0 21 * * 0", datetime(2026, 9, 7, 1, 5), STARTED)
    assert keys(evaluate(snap(jobs=[stale]))) == {"stale:review"}


def test_weekday_cron_is_not_stale_over_a_weekend():
    # Weekdays at 9:00 New York; last success Friday; NOW is Sunday.
    job = JobStatus("brief", "cron", "0 9 * * 1-5", datetime(2026, 9, 25, 13, 2), STARTED)
    assert evaluate(snap(jobs=[job])) == []


def test_bad_schedule_is_reported_not_raised():
    job = JobStatus("broken", "interval", "every day", None, STARTED)
    assert keys(evaluate(snap(jobs=[job]))) == {"bad_schedule:broken"}


# ---------------------------------------------------------------------------
# Failed, truncated and hung runs
# ---------------------------------------------------------------------------


def test_failures_group_per_job_and_name_truncation():
    runs = [
        RunRecord("curator", NOW - timedelta(hours=5), "failed", "Error code: 529 overloaded"),
        RunRecord(
            "curator",
            NOW - timedelta(hours=1),
            "failed",
            "Run ended with its last response cut off at max_tokens",
        ),
    ]
    alerts = evaluate(snap(unsuccessful_runs=runs))
    assert keys(alerts) == {"run_failed:curator"}
    assert "2 times" in alerts[0].message
    assert "cut off at max_tokens" in alerts[0].message


def test_long_running_run_is_hung_but_recent_one_is_not():
    runs = [
        RunRecord("slow", NOW - timedelta(hours=4), "running"),
        RunRecord("fresh", NOW - timedelta(minutes=10), "running"),
    ]
    assert keys(evaluate(snap(unsuccessful_runs=runs))) == {"hung:slow"}


# ---------------------------------------------------------------------------
# OAuth tokens
# ---------------------------------------------------------------------------


def test_token_without_refresh_warns_at_14_days_then_urgently_at_3():
    far = TokenStatus("ticktick", NOW + timedelta(days=28), False)
    assert evaluate(snap(tokens=[far])) == []

    soon = evaluate(snap(tokens=[TokenStatus("ticktick", NOW + timedelta(days=10), False)]))
    assert keys(soon) == {"oauth_expiring:ticktick:14d"}
    assert soon[0].renotify_after == timedelta(days=7)
    assert "TickTick" in soon[0].message

    urgent = evaluate(snap(tokens=[TokenStatus("ticktick", NOW + timedelta(days=2), False)]))
    assert keys(urgent) == {"oauth_expiring:ticktick:3d"}
    assert urgent[0].renotify_after == timedelta(days=1)

    gone = evaluate(snap(tokens=[TokenStatus("ticktick", NOW - timedelta(hours=1), False)]))
    assert keys(gone) == {"oauth_expired:ticktick"}


def test_refreshable_token_expiry_is_ignored_but_rejected_refresh_is_not():
    hourly = TokenStatus("google_calendar", NOW - timedelta(minutes=5), True)
    assert evaluate(snap(tokens=[hourly])) == []
    rejected = TokenStatus("google_calendar", NOW - timedelta(minutes=5), True, "HTTP 400")
    assert keys(evaluate(snap(tokens=[rejected]))) == {"oauth_rejected:google_calendar"}


# ---------------------------------------------------------------------------
# Curator, embedding, disk
# ---------------------------------------------------------------------------


def curator_runs(n: int) -> list[datetime]:
    return [NOW - timedelta(days=3 * i + 1) for i in range(n)]  # newest first


def test_curator_idle_after_n_runs_without_proposals():
    assert evaluate(snap(curator_run_starts=curator_runs(2))) == []
    alerts = evaluate(
        snap(curator_run_starts=curator_runs(3), last_curator_proposal=NOW - timedelta(days=30))
    )
    assert keys(alerts) == {"curator_idle"}


def test_curator_with_a_recent_proposal_is_fine():
    runs = curator_runs(3)
    assert evaluate(snap(curator_run_starts=runs, last_curator_proposal=runs[1])) == []


def test_embedding_stale_errors_and_fresh():
    base = {"embedding_expected": True, "embedding_since": STARTED}
    stale = evaluate(snap(embedding_last_run=NOW - timedelta(hours=4), **base))
    assert keys(stale) == {"embedding_stale"}
    errors = evaluate(
        snap(embedding_last_run=NOW - timedelta(minutes=30), embedding_last_errors=2, **base)
    )
    assert keys(errors) == {"embedding_errors"}
    assert evaluate(snap(embedding_last_run=NOW - timedelta(minutes=30), **base)) == []


def test_embedding_not_judged_before_first_run_after_startup():
    recent_start = {"embedding_expected": True, "embedding_since": NOW - timedelta(minutes=30)}
    assert evaluate(snap(embedding_last_run=NOW - timedelta(days=2), **recent_start)) == []
    assert evaluate(snap(embedding_since=STARTED)) == []  # embedding not configured


def test_disk_over_limit():
    alerts = evaluate(snap(disk_used={"/data/org": 0.85, "/data/logseq": 0.5}))
    assert keys(alerts) == {"disk:/data/org"}


# ---------------------------------------------------------------------------
# Deduplication and pushing
# ---------------------------------------------------------------------------


def test_due_alerts_respects_each_renotify_window():
    daily = Alert("a", "daily")
    weekly = Alert("b", "weekly", renotify_after=timedelta(days=7))
    last = {"a": NOW - timedelta(hours=23), "b": NOW - timedelta(days=2)}
    assert due_alerts([daily, weekly], last, NOW) == []
    last = {"a": NOW - timedelta(hours=24), "b": NOW - timedelta(days=7)}
    assert due_alerts([daily, weekly], last, NOW) == [daily, weekly]
    assert due_alerts([daily], {}, NOW) == [daily]


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[
            t.__table__
            for t in (
                ScheduledTask,
                ScheduledTaskRun,
                NoteProposal,
                AgentRunLog,
                OAuthToken,
                OpsState,
            )
        ],
    )
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_push_sends_once_then_waits_a_day(db, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "pkm-test")
    alerts = [Alert("disk:/data", "Disk full"), Alert("curator_idle", "Curator idle")]
    with patch("pkm_bridge.watchdog.ntfy.send") as send:
        assert watchdog.push_new_alerts(db, alerts, NOW) == alerts
        send.assert_called_once()
        assert "Disk full" in send.call_args.args[0] and "Curator idle" in send.call_args.args[0]

        assert watchdog.push_new_alerts(db, alerts, NOW + timedelta(hours=3)) == []
        assert watchdog.push_new_alerts(db, alerts, NOW + timedelta(days=1)) == alerts
        assert send.call_count == 2


def test_push_without_ntfy_only_logs_and_records_nothing(db, monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    with patch("pkm_bridge.watchdog.ntfy.send") as send:
        assert watchdog.push_new_alerts(db, [Alert("x", "problem")], NOW) == []
        send.assert_not_called()
    assert db.query(OpsState).count() == 0


def test_failed_push_is_retried_next_time(db, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "pkm-test")
    alert = Alert("x", "problem")
    with patch("pkm_bridge.watchdog.ntfy.send", side_effect=watchdog.ntfy.NtfyError("down")):
        assert watchdog.push_new_alerts(db, [alert], NOW) == []
    with patch("pkm_bridge.watchdog.ntfy.send") as send:
        assert watchdog.push_new_alerts(db, [alert], NOW + timedelta(minutes=5)) == [alert]
        send.assert_called_once()


def test_dispatcher_push_and_watchdog_share_the_failure_key(db, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "pkm-test")
    with (
        patch("pkm_bridge.watchdog.get_db", return_value=db),
        patch.object(db, "close"),
        patch("pkm_bridge.watchdog.ntfy.send") as send,
    ):
        watchdog.notify_run_failure("heartbeat", "failed", "Error code: 529")
        failure = RunRecord("heartbeat", NOW, "failed", "Error code: 529")
        later = evaluate(snap(unsuccessful_runs=[failure]))
        assert watchdog.push_new_alerts(db, later, datetime.utcnow()) == []
        send.assert_called_once()


# ---------------------------------------------------------------------------
# Gathering from the database
# ---------------------------------------------------------------------------


def test_gather_snapshot_reads_rows(db, tmp_path):
    db.add_all(
        [
            ScheduledTask(
                id=1,
                name="heartbeat",
                prompt="p",
                schedule_type="interval",
                schedule_expr="12h",
                enabled=True,
                created_at=STARTED,
            ),
            ScheduledTask(
                id=2,
                name="note_curation",
                prompt="p",
                schedule_type="interval",
                schedule_expr="3d",
                enabled=True,
                created_at=STARTED,
            ),
            ScheduledTask(
                id=3,
                name="off",
                prompt="p",
                schedule_type="interval",
                schedule_expr="1h",
                enabled=False,
                created_at=STARTED,
            ),
        ]
    )
    db.add_all(
        [
            ScheduledTaskRun(
                task_id=1,
                started_at=NOW - timedelta(hours=13),
                completed_at=NOW - timedelta(hours=12),
                status="completed",
            ),
            ScheduledTaskRun(
                task_id=1, started_at=NOW - timedelta(hours=1), status="failed", error="boom"
            ),
            *[
                ScheduledTaskRun(task_id=2, started_at=start, status="completed")
                for start in curator_runs(4)
            ],
            NoteProposal(
                kind="insight",
                title="t",
                rationale="r",
                payload={},
                source="curator",
                created_at=NOW - timedelta(days=40),
            ),
            AgentRunLog(started_at=NOW - timedelta(hours=2), trigger="scheduled", error="cut off"),
            OAuthToken(
                service="ticktick",
                access_token="a",
                expires_at=NOW + timedelta(days=10),
            ),
            OpsState(
                key="job:embedding", at=NOW - timedelta(minutes=20), detail='{"error_count": 1}'
            ),
        ]
    )
    db.commit()

    snapshot = watchdog.gather_snapshot(
        db,
        NOW,
        tz=NY,
        started_at=STARTED,
        scheduled_tasks=True,
        self_improvement=True,
        embedding=True,
        disk_paths=[str(tmp_path), str(tmp_path)],
    )

    assert {j.name for j in snapshot.jobs} == {"heartbeat", "note_curation", "self-improvement"}
    heartbeat = next(j for j in snapshot.jobs if j.name == "heartbeat")
    assert heartbeat.last_success == NOW - timedelta(hours=12)
    assert {(r.job, r.error) for r in snapshot.unsuccessful_runs} == {
        ("heartbeat", "boom"),
        ("self-improvement", "cut off"),
    }
    assert len(snapshot.curator_run_starts) == watchdog.CURATOR_IDLE_RUNS
    assert snapshot.tokens == [TokenStatus("ticktick", NOW + timedelta(days=10), False, None)]
    assert snapshot.embedding_last_errors == 1
    assert list(snapshot.disk_used) == [str(tmp_path)]

    assert keys(evaluate(snapshot)) >= {
        "run_failed:heartbeat",
        "run_failed:self-improvement",
        "curator_idle",
        "oauth_expiring:ticktick:14d",
        "embedding_errors",
    }
