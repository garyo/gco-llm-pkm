"""Chat-accessible tool for managing scheduled tasks."""

from typing import Any, Dict, Optional

from .base import BaseTool


class ScheduleTaskTool(BaseTool):
    """Create, list, update, or delete scheduled tasks via chat."""

    def __init__(self, logger):
        super().__init__(logger)

    @property
    def name(self) -> str:
        return "schedule_task"

    @property
    def description(self) -> str:
        return (
            "Manage scheduled/cron tasks that run automatically. "
            "Actions: 'create' (new task), 'list' (show all), "
            "'update' (modify existing), 'delete' (remove), "
            "'toggle' (enable/disable).\n\n"
            "Schedule types:\n"
            "- cron: standard cron expression, e.g. '0 9 * * 1-5' (weekdays at 9am)\n"
            "- interval: simple interval, e.g. '4h', '30m', '1d'\n\n"
            "Example: create a task to check calendar every weekday morning."
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "list", "update", "delete", "toggle"],
                    "description": "The action to perform"
                },
                "name": {
                    "type": "string",
                    "description": "Task name (required for create, used to identify for update/delete/toggle)"
                },
                "prompt": {
                    "type": "string",
                    "description": "The prompt sent to Claude when the task runs (required for create)"
                },
                "schedule_type": {
                    "type": "string",
                    "enum": ["cron", "interval"],
                    "description": "Schedule type (required for create)"
                },
                "schedule_expr": {
                    "type": "string",
                    "description": "Cron expression or interval (e.g. '0 9 * * 1-5' or '4h')"
                },
                "description": {
                    "type": "string",
                    "description": "Optional human-readable description"
                },
                "task_id": {
                    "type": "integer",
                    "description": "Task ID (alternative to name for update/delete/toggle)"
                },
                "updates": {
                    "type": "object",
                    "description": "Fields to update (for 'update' action): prompt, schedule_type, schedule_expr, description, max_turns, max_input_tokens, max_output_tokens"
                },
            },
            "required": ["action"]
        }

    def execute(self, params: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> str:
        action = params.get("action", "")

        try:
            if action == "create":
                return self._create(params)
            elif action == "list":
                return self._list()
            elif action == "update":
                return self._update(params)
            elif action == "delete":
                return self._delete(params)
            elif action == "toggle":
                return self._toggle(params)
            else:
                return f"Unknown action: '{action}'. Use create, list, update, delete, or toggle."
        except Exception as e:
            self.logger.error(f"schedule_task error: {e}")
            return f"Error: {e}"

    def _get_task(self, params: Dict[str, Any]):
        """Find a task by ID or name."""
        from ..database import get_db
        from ..scheduler.repository import ScheduledTaskRepository

        db = get_db()
        task_id = params.get("task_id")
        name = params.get("name")

        if task_id:
            task = ScheduledTaskRepository.get_by_id(db, task_id)
        elif name:
            task = ScheduledTaskRepository.get_by_name(db, name)
        else:
            db.close()
            return None, None
        return db, task

    def _create(self, params: Dict[str, Any]) -> str:
        from ..database import get_db
        from ..scheduler.repository import ScheduledTaskRepository

        name = params.get("name")
        prompt = params.get("prompt")
        schedule_type = params.get("schedule_type")
        schedule_expr = params.get("schedule_expr")

        if not all([name, prompt, schedule_type, schedule_expr]):
            return "Error: 'create' requires name, prompt, schedule_type, and schedule_expr."

        db = get_db()
        try:
            existing = ScheduledTaskRepository.get_by_name(db, name)
            if existing:
                return f"Error: A task named '{name}' already exists (id={existing.id})."

            task = ScheduledTaskRepository.create(
                db,
                name=name,
                description=params.get("description", ""),
                prompt=prompt,
                schedule_type=schedule_type,
                schedule_expr=schedule_expr,
                created_by="chat",
            )
            return (
                f"Created scheduled task '{task.name}' (id={task.id}).\n"
                f"Schedule: {task.schedule_type} {task.schedule_expr}\n"
                f"Next run: {task.next_run_at.isoformat() if task.next_run_at else 'pending'}"
            )
        finally:
            db.close()

    def _list(self) -> str:
        from ..database import get_db
        from ..scheduler.repository import ScheduledTaskRepository

        db = get_db()
        try:
            tasks = ScheduledTaskRepository.get_all(db)
            if not tasks:
                return "No scheduled tasks configured."

            lines = [f"**{len(tasks)} scheduled task(s):**\n"]
            for t in tasks:
                status = "enabled" if t.enabled else "DISABLED"
                hb = " [heartbeat]" if t.is_heartbeat else ""
                last = t.last_run_at.strftime("%Y-%m-%d %H:%M") if t.last_run_at else "never"
                nxt = t.next_run_at.strftime("%Y-%m-%d %H:%M") if t.next_run_at else "—"
                lines.append(
                    f"- **{t.name}** (id={t.id}, {status}{hb}): "
                    f"{t.schedule_type} `{t.schedule_expr}` | "
                    f"last: {last} | next: {nxt}"
                )
                if t.description:
                    lines.append(f"  {t.description}")
            return "\n".join(lines)
        finally:
            db.close()

    def _update(self, params: Dict[str, Any]) -> str:
        from ..scheduler.repository import ScheduledTaskRepository

        db, task = self._get_task(params)
        if not db:
            return "Error: provide 'task_id' or 'name' to identify the task."
        if not task:
            db.close()
            return "Error: task not found."

        try:
            updates = params.get("updates", {})
            if not updates:
                return "Error: provide 'updates' dict with fields to change."

            allowed_fields = {
                'prompt', 'schedule_type', 'schedule_expr', 'description',
                'max_turns', 'max_input_tokens', 'max_output_tokens', 'name',
            }
            filtered = {k: v for k, v in updates.items() if k in allowed_fields}
            if not filtered:
                return f"Error: no valid fields to update. Allowed: {', '.join(sorted(allowed_fields))}"

            updated = ScheduledTaskRepository.update(db, task.id, **filtered)
            return f"Updated task '{updated.name}' (id={updated.id})."
        finally:
            db.close()

    def _delete(self, params: Dict[str, Any]) -> str:
        from ..scheduler.repository import ScheduledTaskRepository

        db, task = self._get_task(params)
        if not db:
            return "Error: provide 'task_id' or 'name' to identify the task."
        if not task:
            db.close()
            return "Error: task not found."

        try:
            if task.is_heartbeat:
                return "Error: cannot delete the heartbeat task. Disable it instead."
            name = task.name
            ScheduledTaskRepository.delete(db, task.id)
            return f"Deleted task '{name}'."
        finally:
            db.close()

    def _toggle(self, params: Dict[str, Any]) -> str:
        from ..scheduler.repository import ScheduledTaskRepository

        db, task = self._get_task(params)
        if not db:
            return "Error: provide 'task_id' or 'name' to identify the task."
        if not task:
            db.close()
            return "Error: task not found."

        try:
            new_state = not task.enabled
            ScheduledTaskRepository.update(db, task.id, enabled=new_state)
            state_str = "enabled" if new_state else "disabled"
            return f"Task '{task.name}' is now {state_str}."
        finally:
            db.close()
