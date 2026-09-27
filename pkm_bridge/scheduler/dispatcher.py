"""Task dispatcher — 60-second tick that finds and runs due tasks.

Scheduled and manual runs share one job lock, so only one task executes at a
time. Enforces a daily global budget in dollars and output tokens.
"""

import logging
import os
from datetime import datetime, tzinfo
from typing import Optional

from ..database import get_db
from ..events import event_manager
from ..job_lock import job_lock, start_exclusive
from ..watchdog import notify_run_failure
from .executor import TaskExecutor
from .heartbeat import load_heartbeat_prompt
from .repository import (
    DailyTokenUsageRepository,
    ScheduledTaskRepository,
    ScheduledTaskRunRepository,
)

DEFAULT_DAILY_COST_LIMIT_USD = 5.0
DEFAULT_DAILY_OUTPUT_LIMIT = 200_000

JOB_NAME = "scheduled_tasks"


def daily_cost_limit_usd() -> float:
    return float(os.environ.get("CRON_DAILY_COST_LIMIT_USD", DEFAULT_DAILY_COST_LIMIT_USD))


def daily_output_limit() -> int:
    return int(os.environ.get("CRON_DAILY_OUTPUT_TOKEN_LIMIT", DEFAULT_DAILY_OUTPUT_LIMIT))


def daily_budget_fraction(usage) -> float:
    """How much of today's budget a DailyTokenUsage row has used (1.0 = exhausted)."""
    return max(
        usage.cost_usd / max(daily_cost_limit_usd(), 0.01),
        usage.output_tokens / max(daily_output_limit(), 1),
    )


def prompt_with_date(prompt: str, tz: Optional[tzinfo]) -> str:
    """Prefix a task prompt with the current local date and time.

    Scheduled tasks run with no chat context, so without this the model
    guesses the date from tool output (and often gets the year wrong).
    """
    now = datetime.now(tz) if tz else datetime.now().astimezone()
    date_line = now.strftime("Current date and time: %A, %B %d, %Y, %H:%M %Z.")
    return f"{date_line}\n\n{prompt}"


class TaskDispatcher:
    """Finds due scheduled tasks and runs them serially."""

    def __init__(
        self,
        executor: TaskExecutor,
        logger: logging.Logger,
        org_dir: Optional[str] = None,
        timezone: Optional[tzinfo] = None,
    ):
        self.executor = executor
        self.logger = logger
        self.org_dir = org_dir
        self.timezone = timezone

    def _check_global_budget(self, db) -> bool:
        """Return True if daily budget still has room."""
        usage = DailyTokenUsageRepository.get_today(db)
        if daily_budget_fraction(usage) >= 1.0:
            self.logger.info(
                f"Scheduler: daily budget reached (${usage.cost_usd:.2f} of "
                f"${daily_cost_limit_usd():.2f}, {usage.output_tokens} of "
                f"{daily_output_limit()} output tokens)"
            )
            return False
        return True

    def _broadcast_budget_warning(self, db) -> None:
        """Broadcast SSE warning at 80% and 95% of daily budget."""
        usage = DailyTokenUsageRepository.get_today(db)
        pct = daily_budget_fraction(usage)

        if pct >= 0.95:
            event_manager.broadcast(
                "daily_budget_warning",
                {
                    "level": "critical",
                    "percent": round(pct * 100),
                    "cost_usd": round(usage.cost_usd, 4),
                    "output_tokens": usage.output_tokens,
                },
            )
        elif pct >= 0.80:
            event_manager.broadcast(
                "daily_budget_warning",
                {
                    "level": "warning",
                    "percent": round(pct * 100),
                    "cost_usd": round(usage.cost_usd, 4),
                    "output_tokens": usage.output_tokens,
                },
            )

    def tick(self) -> None:
        """Called every 60 seconds by APScheduler. Finds and runs due tasks."""
        lock = job_lock(JOB_NAME)
        if not lock.acquire(blocking=False):
            self.logger.debug("Scheduler tick: already running, skipping")
            return

        try:
            self._run_due_tasks()
        except Exception as e:
            self.logger.error(f"Scheduler tick error: {e}", exc_info=True)
        finally:
            lock.release()

    def _run_due_tasks(self) -> None:
        db = get_db()
        try:
            if not self._check_global_budget(db):
                return

            due_tasks = ScheduledTaskRepository.get_due(db)
            if not due_tasks:
                return

            self.logger.info(f"Scheduler: {len(due_tasks)} due task(s)")

            for task in due_tasks:
                # Re-check budget before each task
                if not self._check_global_budget(db):
                    break

                self._run_one_task(db, task)
                self._broadcast_budget_warning(db)

        finally:
            db.close()

    def _run_one_task(self, db, task) -> None:
        """Execute a single scheduled task and log the run."""
        started_at = datetime.utcnow()

        # For heartbeat tasks, load prompt from .pkm/heartbeat.md
        prompt = task.prompt
        if task.is_heartbeat and self.org_dir:
            loaded = load_heartbeat_prompt(self.org_dir)
            if loaded:
                prompt = loaded
        prompt = prompt_with_date(prompt, self.timezone)

        # Create run log entry
        run = ScheduledTaskRunRepository.create(
            db,
            task_id=task.id,
            started_at=started_at,
            status="running",
        )

        # Broadcast start event
        event_manager.broadcast(
            "scheduled_task_started",
            {
                "task_id": task.id,
                "task_name": task.name,
                "started_at": started_at.isoformat(),
            },
        )

        self.logger.info(f"Scheduler: running '{task.name}'")

        # Execute through Claude tool loop
        result = self.executor.execute(
            prompt,
            max_turns=task.max_turns,
            max_input_tokens=task.max_input_tokens,
            max_output_tokens=task.max_output_tokens,
            tools_allowed=task.tools_allowed,
            model=task.model,
        )

        completed_at = datetime.utcnow()
        duration_s = (completed_at - started_at).total_seconds()
        error = result.get("error")
        status = "failed" if error else "completed"

        # Update run log
        ScheduledTaskRunRepository.update(
            db,
            run.id,
            completed_at=completed_at,
            status=status,
            turns_used=result["turns_used"],
            input_tokens=result["input_tokens"],
            output_tokens=result["output_tokens"],
            cache_write_tokens=result["cache_write_tokens"],
            cache_read_tokens=result["cache_read_tokens"],
            cost_usd=result["cost_usd"],
            summary=result.get("summary", ""),
            error=error,
        )

        # Update task's last_run_at and advance next_run_at
        ScheduledTaskRepository.mark_run(db, task)

        # Record daily token usage
        DailyTokenUsageRepository.record_usage(
            db,
            input_tokens=result["input_tokens"],
            output_tokens=result["output_tokens"],
            cache_write_tokens=result["cache_write_tokens"],
            cache_read_tokens=result["cache_read_tokens"],
            cost_usd=result["cost_usd"],
        )

        # Broadcast completion/failure event
        if error:
            event_manager.broadcast(
                "scheduled_task_failed",
                {
                    "task_id": task.id,
                    "task_name": task.name,
                    "error": error[:500],
                },
            )
            self.logger.error(f"Scheduler: '{task.name}' failed: {error[:200]}")
            notify_run_failure(task.name, status, error)
        else:
            event_manager.broadcast(
                "scheduled_task_completed",
                {
                    "task_id": task.id,
                    "task_name": task.name,
                    "summary": result.get("summary", "")[:500],
                    "tokens_used": result["input_tokens"] + result["output_tokens"],
                    "cost_usd": round(result["cost_usd"], 4),
                    "duration_s": round(duration_s, 1),
                },
            )
            self.logger.info(
                f"Scheduler: '{task.name}' completed in {duration_s:.1f}s "
                f"({result['turns_used']} turns, "
                f"{result['input_tokens']}+{result['output_tokens']} tokens, "
                f"${result['cost_usd']:.4f})"
            )

    def start_task_now(self, task) -> Optional[str]:
        """Start a task immediately in a background thread (manual trigger).

        Returns None when started, or the reason it was refused: manual runs
        honor the enabled flag, the daily budget, and the one-task-at-a-time
        lock just as scheduled runs do.
        """
        if not task.enabled:
            return f"Task '{task.name}' is disabled; enable it first"
        db = get_db()
        try:
            if not self._check_global_budget(db):
                return "The daily scheduled-task token budget is used up"
        finally:
            db.close()
        if not start_exclusive(JOB_NAME, self._run_task_by_id, task.id, logger=self.logger):
            return "Another scheduled task is running; try again when it finishes"
        return None

    def _run_task_by_id(self, task_id: int) -> None:
        db = get_db()
        try:
            task = ScheduledTaskRepository.get_by_id(db, task_id)
            if not task:
                self.logger.error(f"Scheduler: run now: task {task_id} not found")
                return
            self._run_one_task(db, task)
        finally:
            db.close()
