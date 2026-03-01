"""TickTick integration tool for Claude."""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from zoneinfo import ZoneInfo
import logging

from pkm_bridge.tools.base import BaseTool
from pkm_bridge.database import get_db
from pkm_bridge.db_repository import OAuthRepository
from pkm_bridge.ticktick_oauth import TickTickOAuth
from pkm_bridge.ticktick_client import TickTickClient


class TickTickTool(BaseTool):
    """Tool for querying and managing TickTick tasks."""

    def __init__(self, logger: logging.Logger, oauth_handler: Optional[TickTickOAuth] = None):
        """Initialize TickTick tool.

        Args:
            logger: Logger instance
            oauth_handler: Optional TickTickOAuth instance
        """
        super().__init__(logger)
        self.oauth_handler = oauth_handler

    @property
    def name(self) -> str:
        """Tool name for Claude API."""
        return "ticktick_query"

    @property
    def description(self) -> str:
        """Tool description for Claude API."""
        return """Query and manage TickTick tasks.

Actions:
- list_today: Tasks due today or overdue
- list_all: All tasks (grouped by project)
- list_projects: Show all projects with IDs
- list_upcoming: Tasks due in the next N days (default 7)
- list_overdue: Past-due incomplete tasks
- search: Multi-word AND search across title+content
- create: Create a new task
- update: Update an existing task
- complete: Mark a task as complete

Filters (apply to list_today, list_all, list_upcoming, list_overdue, search):
- project: Filter by project name or ID
- due_before / due_after: Date range filter (YYYY-MM-DD)
- priority_min: Minimum priority (0=None, 1=Low, 3=Medium, 5=High)
- include_completed: Include completed tasks (default false)

Other params:
- days: Number of days for list_upcoming (default 7)
- title, content, due_date, priority, reminders: For create/update
- task_id: For update/complete
- query: For search

IMPORTANT: To update a task, first search for it to get its task_id.
Search results include {ticktick:xxx} which is the task_id needed for updates.

Date/Time Support:
- All-day tasks: Use date only (YYYY-MM-DD)
- Timed tasks: Use datetime with specific time (YYYY-MM-DDTHH:MM:SS)
- Reminders: ISO 8601 duration format - "TRIGGER:-PT30M" (30 min before)

Connection status: Check /auth/ticktick/status. If not connected, user needs to visit /auth/ticktick/authorize."""

    @property
    def input_schema(self) -> Dict[str, Any]:
        """JSON schema for tool input."""
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "list_today", "list_all", "list_projects",
                        "list_upcoming", "list_overdue",
                        "create", "update", "complete", "search",
                    ],
                    "description": "Action to perform"
                },
                "title": {
                    "type": "string",
                    "description": "Task title (for create or complete actions)"
                },
                "content": {
                    "type": "string",
                    "description": "Task description (optional, for create)"
                },
                "due_date": {
                    "type": "string",
                    "description": "Due date/time in ISO format. YYYY-MM-DD for all-day, YYYY-MM-DDTHH:MM:SS for timed. Use 'none' to clear the due date."
                },
                "priority": {
                    "type": "integer",
                    "description": "Priority: 0=None, 1=Low, 3=Medium, 5=High (for create/update)"
                },
                "reminders": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Reminder triggers in ISO 8601 duration format (e.g. 'TRIGGER:-PT30M')"
                },
                "task_id": {
                    "type": "string",
                    "description": "Task ID (for update or complete)"
                },
                "query": {
                    "type": "string",
                    "description": "Search query (for search action)"
                },
                "project": {
                    "type": "string",
                    "description": "Filter by project name or ID"
                },
                "due_before": {
                    "type": "string",
                    "description": "Only tasks due before this date (YYYY-MM-DD)"
                },
                "due_after": {
                    "type": "string",
                    "description": "Only tasks due after this date (YYYY-MM-DD)"
                },
                "priority_min": {
                    "type": "integer",
                    "description": "Minimum priority level (0, 1, 3, or 5)"
                },
                "include_completed": {
                    "type": "boolean",
                    "description": "Include completed tasks (default false)"
                },
                "days": {
                    "type": "integer",
                    "description": "Number of days for list_upcoming (default 7)"
                },
            },
            "required": ["action"]
        }

    # --- Helper methods ---

    @staticmethod
    def _build_project_map(
        client: TickTickClient,
    ) -> Tuple[Dict[str, str], Dict[str, str]]:
        """Build project lookup dicts.

        Returns:
            (id_to_name, name_to_id) mapping dicts
        """
        projects = client.list_projects()
        id_to_name: Dict[str, str] = {}
        name_to_id: Dict[str, str] = {}
        for p in projects:
            pid = p.get("id", "")
            pname = p.get("name", "")
            if pid and pname:
                id_to_name[pid] = pname
                name_to_id[pname.lower()] = pid
        return id_to_name, name_to_id

    @staticmethod
    def _resolve_project_id(
        project_param: str,
        name_to_id: Dict[str, str],
        id_to_name: Dict[str, str],
    ) -> str:
        """Resolve a project name or ID to a project ID.

        Raises:
            ValueError: If the project is not found
        """
        # Check if it's already a known ID
        if project_param in id_to_name:
            return project_param
        # Try case-insensitive name lookup
        pid = name_to_id.get(project_param.lower())
        if pid:
            return pid
        available = ", ".join(sorted(id_to_name.values()))
        raise ValueError(f"Project '{project_param}' not found. Available: {available}")

    @staticmethod
    def _apply_filters(
        tasks: List[Dict[str, Any]],
        project_id_filter: Optional[str] = None,
        due_before: Optional[str] = None,
        due_after: Optional[str] = None,
        priority_min: Optional[int] = None,
        include_completed: bool = False,
    ) -> List[Dict[str, Any]]:
        """Apply filters to a task list."""
        result = []

        # Parse date filters once
        due_before_date = None
        due_after_date = None
        if due_before:
            due_before_date = datetime.fromisoformat(due_before).date()
        if due_after:
            due_after_date = datetime.fromisoformat(due_after).date()

        for task in tasks:
            # Filter by completion status
            if not include_completed:
                status = task.get("status", 0)
                if status == 2:  # completed
                    continue

            # Filter by project
            if project_id_filter and task.get("projectId") != project_id_filter:
                continue

            # Filter by priority
            if priority_min is not None and task.get("priority", 0) < priority_min:
                continue

            # Filter by due date
            due_date_str = task.get("dueDate")
            if due_before_date or due_after_date:
                if not due_date_str:
                    continue  # Exclude tasks without due date when date filter is set
                try:
                    task_date = datetime.fromisoformat(
                        due_date_str.replace("Z", "+00:00")
                    ).date()
                except (ValueError, AttributeError):
                    continue
                if due_before_date and task_date >= due_before_date:
                    continue
                if due_after_date and task_date < due_after_date:
                    continue

            result.append(task)
        return result

    @staticmethod
    def _format_tasks_grouped(
        client: TickTickClient,
        tasks: List[Dict[str, Any]],
        id_to_name: Dict[str, str],
        header: str,
    ) -> str:
        """Format tasks grouped by project as markdown."""
        if not tasks:
            return f"{header}: No tasks found."

        # Group by project
        by_project: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for task in tasks:
            proj_id = task.get("projectId", "")
            proj_name = id_to_name.get(proj_id, "Inbox")
            by_project[proj_name].append(task)

        lines = [f"{header} ({len(tasks)} tasks):"]
        for proj_name in sorted(by_project.keys()):
            proj_tasks = by_project[proj_name]
            lines.append(f"\n### {proj_name}")
            for t in proj_tasks:
                summary = client.format_task_summary(t, include_id=True)
                lines.append(f"- [ ] {summary}")

        return "\n".join(lines)

    # --- Client access ---

    def get_client(self) -> Optional[TickTickClient]:
        """Get authenticated TickTick client.

        Returns:
            TickTickClient if authenticated, None otherwise
        """
        if not self.oauth_handler:
            return None

        try:
            db = get_db()
            token = OAuthRepository.get_token(db, 'ticktick')

            if not token:
                return None

            # Check if token needs refresh
            if OAuthRepository.is_token_expired(token):
                self.logger.info("TickTick token expired, refreshing...")
                try:
                    new_token_data = self.oauth_handler.refresh_token(token.refresh_token)

                    # Update token in database
                    OAuthRepository.save_token(
                        db=db,
                        service='ticktick',
                        access_token=new_token_data['access_token'],
                        refresh_token=new_token_data.get('refresh_token'),
                        expires_at=new_token_data['expires_at'],
                        scope=new_token_data.get('scope')
                    )

                    token = OAuthRepository.get_token(db, 'ticktick')
                    self.logger.info("TickTick token refreshed successfully")

                except Exception as e:
                    self.logger.error(f"Failed to refresh TickTick token: {e}")
                    return None
                finally:
                    db.close()
            else:
                db.close()

            return TickTickClient(token.access_token)

        except Exception as e:
            self.logger.error(f"Error getting TickTick client: {e}")
            return None

    # --- Main execute ---

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        """Execute TickTick action.

        Args:
            params: Tool parameters with 'action' and action-specific params

        Returns:
            Execution result as string
        """
        action = params.get('action')
        if not action:
            return "Error: 'action' parameter is required"

        client = self.get_client()

        if not client:
            return "TickTick not connected. Please connect via /auth/ticktick/authorize"

        try:
            user_timezone = context.get('user_timezone') if context else None

            # Extract common filter params
            project_param = params.get("project")
            due_before = params.get("due_before")
            due_after = params.get("due_after")
            priority_min = params.get("priority_min")
            include_completed = params.get("include_completed", False)

            # Resolve project filter if specified
            project_id_filter: Optional[str] = None
            id_to_name: Dict[str, str] = {}
            name_to_id: Dict[str, str] = {}

            if action in (
                "list_today", "list_all", "list_upcoming", "list_overdue",
                "search", "list_projects",
            ):
                id_to_name, name_to_id = self._build_project_map(client)

            if project_param:
                if not id_to_name:
                    id_to_name, name_to_id = self._build_project_map(client)
                project_id_filter = self._resolve_project_id(
                    project_param, name_to_id, id_to_name
                )

            # --- Actions ---

            if action == "list_projects":
                projects = client.list_projects()
                if not projects:
                    return "No projects found."
                lines = [f"Projects ({len(projects)}):"]
                for p in projects:
                    name = p.get("name", "?")
                    pid = p.get("id", "?")
                    closed = p.get("closed", False)
                    status = " (closed)" if closed else ""
                    lines.append(f"- {name} [ID: {pid}]{status}")
                return "\n".join(lines)

            elif action == "list_today":
                tasks = client.get_today_tasks(user_timezone=user_timezone)
                tasks = self._apply_filters(
                    tasks, project_id_filter, due_before, due_after,
                    priority_min, include_completed,
                )
                return self._format_tasks_grouped(
                    client, tasks, id_to_name, "Tasks due today or overdue"
                )

            elif action == "list_all":
                tasks = client.list_tasks()
                tasks = self._apply_filters(
                    tasks, project_id_filter, due_before, due_after,
                    priority_min, include_completed,
                )
                return self._format_tasks_grouped(
                    client, tasks, id_to_name, "All tasks"
                )

            elif action == "list_upcoming":
                days = params.get("days", 7)
                if user_timezone:
                    try:
                        tz = ZoneInfo(user_timezone)
                        today = datetime.now(tz).date()
                    except Exception:
                        today = datetime.now().date()
                else:
                    today = datetime.now().date()

                end_date = today + timedelta(days=days)
                tasks = client.list_tasks()
                # Filter to tasks due between today and end_date (inclusive)
                upcoming = []
                for task in tasks:
                    due = task.get("dueDate")
                    if not due:
                        continue
                    try:
                        task_date = datetime.fromisoformat(
                            due.replace("Z", "+00:00")
                        ).date()
                    except (ValueError, AttributeError):
                        continue
                    if today <= task_date <= end_date:
                        upcoming.append(task)

                # Apply additional filters
                upcoming = self._apply_filters(
                    upcoming, project_id_filter, due_before, due_after,
                    priority_min, include_completed,
                )
                # Sort by due date
                upcoming.sort(
                    key=lambda t: t.get("dueDate", "9999")
                )
                return self._format_tasks_grouped(
                    client, upcoming, id_to_name,
                    f"Tasks due in the next {days} days",
                )

            elif action == "list_overdue":
                if user_timezone:
                    try:
                        tz = ZoneInfo(user_timezone)
                        today = datetime.now(tz).date()
                    except Exception:
                        today = datetime.now().date()
                else:
                    today = datetime.now().date()

                tasks = client.list_tasks()
                overdue = []
                for task in tasks:
                    due = task.get("dueDate")
                    if not due:
                        continue
                    try:
                        task_date = datetime.fromisoformat(
                            due.replace("Z", "+00:00")
                        ).date()
                    except (ValueError, AttributeError):
                        continue
                    if task_date < today:
                        overdue.append(task)

                # Hardcode include_completed=False for overdue
                overdue = self._apply_filters(
                    overdue, project_id_filter, due_before, due_after,
                    priority_min, include_completed=False,
                )
                # Sort oldest first
                overdue.sort(
                    key=lambda t: t.get("dueDate", "9999")
                )
                return self._format_tasks_grouped(
                    client, overdue, id_to_name, "Overdue tasks"
                )

            elif action == "create":
                title = params.get('title')
                if not title:
                    return "Error: Title is required for creating a task"

                content = params.get('content')
                due_date = params.get('due_date')  # ISO format string
                priority = params.get('priority', 0)
                reminders = params.get('reminders')

                # Parse due date if provided
                due_dt = None
                is_all_day = None
                if due_date:
                    try:
                        if 'T' in due_date:
                            due_dt = datetime.fromisoformat(due_date)
                            is_all_day = (due_dt.hour == 0 and due_dt.minute == 0 and due_dt.second == 0)
                        else:
                            due_dt = datetime.fromisoformat(due_date)
                            is_all_day = True
                    except (ValueError, TypeError):
                        return f"Error: Invalid due_date format: {due_date}. Use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS"

                task = client.create_task(
                    title=title,
                    content=content,
                    due_date=due_dt,
                    priority=priority,
                    user_timezone=user_timezone,
                    reminders=reminders,
                    is_all_day=is_all_day
                )

                task_type = "all-day" if is_all_day else "timed"
                reminder_info = f" with {len(reminders)} reminder(s)" if reminders else ""
                return f"Created {task_type} task: {title}{reminder_info}"

            elif action == "update":
                task_id = params.get('task_id')
                if not task_id:
                    return "Error: task_id is required for updating a task"

                # Build updates dict from provided parameters
                updates: Dict[str, Any] = {}
                if 'title' in params:
                    updates['title'] = params['title']
                if 'content' in params:
                    updates['content'] = params['content']
                if 'due_date' in params:
                    due_date_str = params['due_date']

                    # Clear due date if sentinel value
                    if not due_date_str or due_date_str.lower() == 'none':
                        updates['dueDate'] = None
                        updates['startDate'] = None
                        updates['isAllDay'] = False
                    # Determine if it's a timed task or all-day task
                    elif 'T' not in due_date_str:
                        due_date_obj = datetime.fromisoformat(due_date_str)
                        if user_timezone:
                            try:
                                tz = ZoneInfo(user_timezone)
                                due_date_local = due_date_obj.replace(
                                    hour=0, minute=0, second=0, microsecond=0, tzinfo=tz
                                )
                                due_date_utc = due_date_local.astimezone(ZoneInfo('UTC'))
                                due_date_formatted = due_date_utc.strftime('%Y-%m-%dT%H:%M:%S.000+0000')
                                updates['timeZone'] = user_timezone
                            except Exception as e:
                                self.logger.warning(f"Error converting timezone: {e}, using UTC")
                                due_date_formatted = f"{due_date_str}T00:00:00.000+0000"
                        else:
                            due_date_formatted = f"{due_date_str}T00:00:00.000+0000"

                        updates['dueDate'] = due_date_formatted
                        updates['startDate'] = due_date_formatted
                        updates['isAllDay'] = True
                    else:
                        due_date_obj = datetime.fromisoformat(due_date_str)
                        is_all_day = (due_date_obj.hour == 0 and due_date_obj.minute == 0 and due_date_obj.second == 0)

                        if user_timezone:
                            try:
                                tz = ZoneInfo(user_timezone)
                                if due_date_obj.tzinfo is None:
                                    due_date_local = due_date_obj.replace(tzinfo=tz)
                                else:
                                    due_date_local = due_date_obj.astimezone(tz)
                                due_date_utc = due_date_local.astimezone(ZoneInfo('UTC'))
                                due_date_formatted = due_date_utc.strftime('%Y-%m-%dT%H:%M:%S.000+0000')
                                updates['timeZone'] = user_timezone
                            except Exception as e:
                                self.logger.warning(f"Error converting timezone: {e}, using as-is")
                                due_date_formatted = due_date_str
                        else:
                            if due_date_obj.tzinfo is None:
                                due_date_obj = due_date_obj.replace(tzinfo=ZoneInfo('UTC'))
                            due_date_utc = due_date_obj.astimezone(ZoneInfo('UTC'))
                            due_date_formatted = due_date_utc.strftime('%Y-%m-%dT%H:%M:%S.000+0000')

                        updates['dueDate'] = due_date_formatted
                        updates['startDate'] = due_date_formatted
                        updates['isAllDay'] = is_all_day

                if 'priority' in params:
                    updates['priority'] = params['priority']

                if 'reminders' in params:
                    updates['reminders'] = params['reminders']

                if not updates:
                    return "Error: No fields to update provided"

                task = client.update_task(task_id, **updates)
                task_type = "all-day" if updates.get('isAllDay') else "timed"
                return f"Updated {task_type} task: {task.get('title', task_id)}"

            elif action == "complete":
                task_id = params.get('task_id')
                if not task_id:
                    title = params.get('title')
                    if not title:
                        return "Error: Task ID or title is required"

                    tasks = client.search_tasks(title)
                    if not tasks:
                        return f"Error: Task not found: {title}"
                    if len(tasks) > 1:
                        matches = [client.format_task_summary(t) for t in tasks]
                        return f"Error: Multiple tasks found matching '{title}':\n" + "\n".join(f"- {m}" for m in matches)

                    task_id = tasks[0]['id']

                project_id = params.get('project_id')
                task = client.complete_task(task_id, project_id=project_id)
                return f"Completed task: {task.get('title', task_id)}"

            elif action == "search":
                query = params.get('query')
                if not query:
                    return "Error: Query is required for searching"

                # Multi-word AND matching: all words must appear in title+content
                words = query.lower().split()
                all_tasks = client.list_tasks()
                matched = []
                for task in all_tasks:
                    title = (task.get("title") or "").lower()
                    content = (task.get("content") or "").lower()
                    combined = title + " " + content
                    if all(w in combined for w in words):
                        matched.append(task)

                # Apply filters
                matched = self._apply_filters(
                    matched, project_id_filter, due_before, due_after,
                    priority_min, include_completed,
                )

                if not matched:
                    return f"No tasks found matching '{query}'."

                # Flat output with project name per task
                lines = [f"Tasks matching '{query}' ({len(matched)}):"]
                for t in matched:
                    proj_name = id_to_name.get(t.get("projectId", ""), "Inbox")
                    summary = client.format_task_summary(
                        t, include_id=True, project_name=proj_name
                    )
                    lines.append(f"- {summary}")
                return "\n".join(lines)

            else:
                return f"Error: Unknown action: {action}"

        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            self.logger.error(f"TickTick error ({action}): {e}", exc_info=True)
            return f"TickTick '{action}' failed: {type(e).__name__}: {e}. Try 'list_today' or 'search' to verify connectivity."
