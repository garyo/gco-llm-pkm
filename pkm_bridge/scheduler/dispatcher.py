"""Task dispatcher — 60-second tick that finds and runs due tasks.

Uses a threading Lock so only one task executes at a time.
Enforces daily global token budgets.
"""

import logging
import os
import threading
from datetime import datetime
from typing import Optional

from ..database import get_db
from ..events import event_manager
from .executor import TaskExecutor
from .heartbeat import load_heartbeat_prompt
from .repository import (
    DailyTokenUsageRepository,
    ScheduledTaskRepository,
    ScheduledTaskRunRepository,
)

DEFAULT_DAILY_INPUT_LIMIT = 2_000_000
DEFAULT_DAILY_OUTPUT_LIMIT = 200_000


class TaskDispatcher:
    """Finds due scheduled tasks and runs them serially."""

    def __init__(
        self,
        executor: TaskExecutor,
        logger: logging.Logger,
        org_dir: Optional[str] = None,
    ):
        self.executor = executor
        self.logger = logger
        self.org_dir = org_dir
        self._lock = threading.Lock()

    @property
    def _daily_input_limit(self) -> int:
        return int(os.environ.get('CRON_DAILY_INPUT_TOKEN_LIMIT', DEFAULT_DAILY_INPUT_LIMIT))

    @property
    def _daily_output_limit(self) -> int:
        return int(os.environ.get('CRON_DAILY_OUTPUT_TOKEN_LIMIT', DEFAULT_DAILY_OUTPUT_LIMIT))

    def _check_global_budget(self, db) -> bool:
        """Return True if daily budget still has room."""
        usage = DailyTokenUsageRepository.get_today(db)
        if usage.input_tokens >= self._daily_input_limit:
            self.logger.info("Scheduler: daily input token limit reached")
            return False
        if usage.output_tokens >= self._daily_output_limit:
            self.logger.info("Scheduler: daily output token limit reached")
            return False
        return True

    def _broadcast_budget_warning(self, db) -> None:
        """Broadcast SSE warning at 80% and 95% of daily budget."""
        usage = DailyTokenUsageRepository.get_today(db)
        input_pct = usage.input_tokens / max(self._daily_input_limit, 1)
        output_pct = usage.output_tokens / max(self._daily_output_limit, 1)
        pct = max(input_pct, output_pct)

        if pct >= 0.95:
            event_manager.broadcast('daily_budget_warning', {
                'level': 'critical',
                'percent': round(pct * 100),
                'input_tokens': usage.input_tokens,
                'output_tokens': usage.output_tokens,
            })
        elif pct >= 0.80:
            event_manager.broadcast('daily_budget_warning', {
                'level': 'warning',
                'percent': round(pct * 100),
                'input_tokens': usage.input_tokens,
                'output_tokens': usage.output_tokens,
            })

    def tick(self) -> None:
        """Called every 60 seconds by APScheduler. Finds and runs due tasks."""
        if not self._lock.acquire(blocking=False):
            self.logger.debug("Scheduler tick: already running, skipping")
            return

        try:
            self._run_due_tasks()
        except Exception as e:
            self.logger.error(f"Scheduler tick error: {e}", exc_info=True)
        finally:
            self._lock.release()

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

        # Create run log entry
        run = ScheduledTaskRunRepository.create(
            db,
            task_id=task.id,
            started_at=started_at,
            status='running',
        )

        # Broadcast start event
        event_manager.broadcast('scheduled_task_started', {
            'task_id': task.id,
            'task_name': task.name,
            'started_at': started_at.isoformat(),
        })

        self.logger.info(f"Scheduler: running '{task.name}'")

        # Execute through Claude tool loop
        result = self.executor.execute(
            prompt,
            max_turns=task.max_turns,
            max_input_tokens=task.max_input_tokens,
            max_output_tokens=task.max_output_tokens,
            tools_allowed=task.tools_allowed,
        )

        completed_at = datetime.utcnow()
        duration_s = (completed_at - started_at).total_seconds()
        error = result.get("error")
        status = 'failed' if error else 'completed'

        # Update run log
        ScheduledTaskRunRepository.update(
            db, run.id,
            completed_at=completed_at,
            status=status,
            turns_used=result["turns_used"],
            input_tokens=result["input_tokens"],
            output_tokens=result["output_tokens"],
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
        )

        # Broadcast completion/failure event
        if error:
            event_manager.broadcast('scheduled_task_failed', {
                'task_id': task.id,
                'task_name': task.name,
                'error': error[:500],
            })
            self.logger.error(f"Scheduler: '{task.name}' failed: {error[:200]}")
        else:
            event_manager.broadcast('scheduled_task_completed', {
                'task_id': task.id,
                'task_name': task.name,
                'summary': result.get("summary", "")[:500],
                'tokens_used': result["input_tokens"] + result["output_tokens"],
                'duration_s': round(duration_s, 1),
            })
            self.logger.info(
                f"Scheduler: '{task.name}' completed in {duration_s:.1f}s "
                f"({result['turns_used']} turns, "
                f"{result['input_tokens']}+{result['output_tokens']} tokens)"
            )

    def run_task_now(self, task_id: int) -> None:
        """Run a specific task immediately (called from API endpoint in a daemon thread)."""
        db = get_db()
        try:
            task = ScheduledTaskRepository.get_by_id(db, task_id)
            if not task:
                self.logger.error(f"Scheduler: run_task_now: task {task_id} not found")
                return
            self._run_one_task(db, task)
        except Exception as e:
            self.logger.error(f"Scheduler: run_task_now error: {e}", exc_info=True)
        finally:
            db.close()
