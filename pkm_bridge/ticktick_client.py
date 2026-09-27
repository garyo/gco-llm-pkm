"""TickTick API client for task management."""

import os
import time
from datetime import date, datetime, tzinfo
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

from .http_session import HttpSession
from .timezones import resolve_timezone


def task_due_time(task: Dict[str, Any], tz: tzinfo) -> Optional[datetime]:
    """A task's due time as local wall time, or None if it has none.

    TickTick stores due times in UTC; an all-day task's is midnight in the
    task's own zone, so it's read there to keep its date from shifting.
    """
    due = task.get("dueDate")
    if not due:
        return None
    try:
        dt = datetime.fromisoformat(due.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if task.get("isAllDay") and task.get("timeZone"):
        try:
            tz = ZoneInfo(task["timeZone"])
        except Exception:
            pass
    return dt.astimezone(tz)


def task_due_date(task: Dict[str, Any], tz: tzinfo) -> Optional[date]:
    """A task's due date in the user's zone, or None if it has none."""
    due = task_due_time(task, tz)
    return due.date() if due else None


def _describe_error(e: Exception) -> str:
    """Short reason for a failed request: the HTTP status when there is one."""
    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        return f"HTTP {e.response.status_code}"
    return type(e).__name__


class TickTickClient:
    """Client for TickTick Open API."""

    BASE_URL = "https://api.ticktick.com/open/v1"
    _cache_ttl = 10.0  # seconds

    def __init__(self, access_token: str):
        """Initialize TickTick client.

        Args:
            access_token: OAuth access token
        """
        self.access_token = access_token
        self.session = HttpSession()
        self.session.headers.update(
            {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        )
        self._projects_cache: Optional[Tuple[float, List[Dict[str, Any]]]] = None
        self._tasks_cache: Optional[Tuple[float, List[Dict[str, Any]]]] = None
        # Projects the last all-tasks fetch couldn't load, as "name: error".
        self.failed_projects: List[str] = []

    def _invalidate_cache(self) -> None:
        """Invalidate all caches."""
        self._projects_cache = None
        self._tasks_cache = None

    def list_projects(self) -> List[Dict[str, Any]]:
        """Get all projects (cached for _cache_ttl seconds).

        Returns:
            List of project dictionaries with id, name, etc.

        Raises:
            Exception: If fetching projects fails
        """
        now = time.monotonic()
        if self._projects_cache is not None:
            ts, data = self._projects_cache
            if now - ts < self._cache_ttl:
                return data

        try:
            response = self.session.get(f"{self.BASE_URL}/project")
            response.raise_for_status()
            result = response.json()
            self._projects_cache = (now, result)
            return result
        except requests.exceptions.HTTPError as e:
            raise Exception(
                f"Failed to list projects: {e.response.status_code} - {e.response.text}"
            )
        except Exception as e:
            raise Exception(f"Failed to list projects: {str(e)}")

    def list_tasks(self, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all tasks, optionally filtered by project (cached for _cache_ttl seconds).

        Args:
            project_id: Optional project ID to filter tasks

        Returns:
            List of task dictionaries
        """
        try:
            if not project_id:
                # Check cache for all-tasks fetch
                now = time.monotonic()
                if self._tasks_cache is not None:
                    ts, data = self._tasks_cache
                    if now - ts < self._cache_ttl:
                        return data

                # Get all tasks from all projects; the inbox isn't among them
                sources = [(p.get("id"), p.get("name", "?")) for p in self.list_projects()]
                inbox_id = os.getenv("TICKTICK_INBOX_ID")
                if inbox_id:
                    sources.insert(0, (inbox_id, "Inbox"))

                all_tasks = []
                failed = []
                for proj_id, proj_name in sources:
                    if not proj_id:
                        continue
                    try:
                        response = self.session.get(f"{self.BASE_URL}/project/{proj_id}/data")
                        response.raise_for_status()
                        all_tasks.extend(response.json().get("tasks", []))
                    except Exception as e:
                        failed.append(f"{proj_name}: {_describe_error(e)}")

                self.failed_projects = failed
                if not failed:  # don't cache a partial list
                    self._tasks_cache = (time.monotonic(), all_tasks)
                return all_tasks
            else:
                # Get tasks for specific project (not cached — specific project fetch is cheap)
                response = self.session.get(f"{self.BASE_URL}/project/{project_id}/data")
                response.raise_for_status()
                data = response.json()
                return data.get("tasks", [])

        except Exception as e:
            raise Exception(f"TickTick API Error: {e}")

    def get_today_tasks(self, user_timezone: str = None) -> List[Dict[str, Any]]:
        """Get tasks due today or overdue.

        Args:
            user_timezone: User's timezone string (e.g., 'America/New_York').
                If None, uses the configured timezone.

        Returns:
            List of tasks due today or overdue
        """
        tz = resolve_timezone(user_timezone)
        today = datetime.now(tz).date()
        return [
            task
            for task in self.list_tasks()
            if (due := task_due_date(task, tz)) is not None and due <= today
        ]

    def create_task(
        self,
        title: str,
        content: Optional[str] = None,
        due_date: Optional[datetime] = None,
        priority: int = 0,
        project_id: Optional[str] = None,
        user_timezone: str = None,
        reminders: Optional[List[str]] = None,
        is_all_day: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Create a new task.

        Args:
            title: Task title
            content: Task description/content
            due_date: Due date for the task (datetime object or None)
            priority: Priority level (0=None, 1=Low, 3=Medium, 5=High)
            project_id: Project ID to add task to
            user_timezone: User's timezone (e.g., 'America/New_York').
                Used for timezone-aware tasks.
            reminders: List of reminder trigger strings in ISO 8601 duration format.
                      Examples: ['TRIGGER:-PT30M'] (30 min before),
                      ['TRIGGER:-PT1H'] (1 hour before)
            is_all_day: Whether this is an all-day task. If None, auto-detected from due_date time.

        Returns:
            Created task dictionary

        Raises:
            Exception: If task creation fails
        """
        self._invalidate_cache()
        try:
            task_data = {"title": title}

            if content:
                task_data["content"] = content

            if due_date:
                # Auto-detect if this is an all-day task based on time component
                # If time is midnight (00:00:00), treat as all-day unless explicitly specified
                if is_all_day is None:
                    is_all_day = (
                        due_date.hour == 0 and due_date.minute == 0 and due_date.second == 0
                    )

                if is_all_day:
                    # All-day task: convert to midnight in user's timezone, then to UTC
                    if user_timezone:
                        try:
                            tz = ZoneInfo(user_timezone)
                            # Ensure due_date is timezone-aware at midnight in user's timezone
                            if due_date.tzinfo is None:
                                due_date_local = due_date.replace(
                                    hour=0, minute=0, second=0, microsecond=0, tzinfo=tz
                                )
                            else:
                                due_date_local = due_date.astimezone(tz).replace(
                                    hour=0, minute=0, second=0, microsecond=0
                                )
                            # Convert to UTC
                            due_date_utc = due_date_local.astimezone(ZoneInfo("UTC"))
                            # Format with milliseconds as TickTick expects
                            task_data["dueDate"] = due_date_utc.strftime(
                                "%Y-%m-%dT%H:%M:%S.000+0000"
                            )
                            task_data["startDate"] = due_date_utc.strftime(
                                "%Y-%m-%dT%H:%M:%S.000+0000"
                            )
                            task_data["timeZone"] = user_timezone
                        except Exception:
                            # Fallback to simple format
                            task_data["dueDate"] = due_date.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
                            task_data["startDate"] = due_date.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
                    else:
                        # No timezone provided, use UTC
                        task_data["dueDate"] = due_date.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
                        task_data["startDate"] = due_date.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
                else:
                    # Timed task: preserve the time component
                    if user_timezone:
                        try:
                            tz = ZoneInfo(user_timezone)
                            # Ensure due_date is timezone-aware in user's timezone
                            if due_date.tzinfo is None:
                                due_date_local = due_date.replace(tzinfo=tz)
                            else:
                                due_date_local = due_date.astimezone(tz)
                            # Convert to UTC
                            due_date_utc = due_date_local.astimezone(ZoneInfo("UTC"))
                            # Format with milliseconds as TickTick expects
                            task_data["dueDate"] = due_date_utc.strftime(
                                "%Y-%m-%dT%H:%M:%S.000+0000"
                            )
                            task_data["startDate"] = due_date_utc.strftime(
                                "%Y-%m-%dT%H:%M:%S.000+0000"
                            )
                            task_data["timeZone"] = user_timezone
                        except Exception:
                            # Fallback to simple format
                            task_data["dueDate"] = due_date.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
                            task_data["startDate"] = due_date.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
                    else:
                        # No timezone provided, assume UTC
                        if due_date.tzinfo is None:
                            due_date = due_date.replace(tzinfo=ZoneInfo("UTC"))
                        due_date_utc = due_date.astimezone(ZoneInfo("UTC"))
                        task_data["dueDate"] = due_date_utc.strftime("%Y-%m-%dT%H:%M:%S.000+0000")
                        task_data["startDate"] = due_date_utc.strftime("%Y-%m-%dT%H:%M:%S.000+0000")

                task_data["isAllDay"] = is_all_day

            if priority:
                task_data["priority"] = priority

            if project_id:
                task_data["projectId"] = project_id

            if reminders:
                task_data["reminders"] = reminders

            response = self.session.post(f"{self.BASE_URL}/task", json=task_data)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            raise Exception(
                f"Failed to create task '{title}': {e.response.status_code} - {e.response.text}"
            )
        except Exception as e:
            raise Exception(f"Failed to create task '{title}': {str(e)}")

    def complete_task(self, task_id: str, project_id: Optional[str] = None) -> Dict[str, Any]:
        """Mark a task as complete.

        Args:
            task_id: ID of task to complete
            project_id: Optional project ID (will be looked up if not provided)

        Returns:
            Success status dictionary

        Raises:
            Exception: If completion fails
        """
        self._invalidate_cache()
        try:
            # If project_id not provided, find it by searching all projects
            if not project_id:
                projects = self.list_projects()
                inbox_id = os.getenv("TICKTICK_INBOX_ID")

                # Check inbox first if configured
                if inbox_id:
                    try:
                        response = self.session.get(f"{self.BASE_URL}/project/{inbox_id}/data")
                        if response.ok:
                            data = response.json()
                            for task in data.get("tasks", []):
                                if task.get("id") == task_id:
                                    project_id = inbox_id
                                    break
                    except Exception:
                        pass

                # Check regular projects if not found in inbox
                if not project_id:
                    for project in projects:
                        proj_id = project.get("id")
                        if proj_id:
                            try:
                                response = self.session.get(
                                    f"{self.BASE_URL}/project/{proj_id}/data"
                                )
                                if response.ok:
                                    data = response.json()
                                    for task in data.get("tasks", []):
                                        if task.get("id") == task_id:
                                            project_id = proj_id
                                            break
                            except Exception:
                                continue
                        if project_id:
                            break

                if not project_id:
                    raise Exception(f"Could not find project for task {task_id}")

            # Complete the task using the correct endpoint
            response = self.session.post(
                f"{self.BASE_URL}/project/{project_id}/task/{task_id}/complete"
            )
            response.raise_for_status()

            # API returns empty response on success
            return {"success": True, "task_id": task_id}
        except requests.exceptions.HTTPError as e:
            raise Exception(
                f"Failed to complete task {task_id}: {e.response.status_code} - {e.response.text}"
            )
        except Exception as e:
            if "Could not find project" in str(e):
                raise
            raise Exception(f"Failed to complete task {task_id}: {str(e)}")

    def update_task(self, task_id: str, **updates) -> Dict[str, Any]:
        """Update a task.

        Args:
            task_id: ID of task to update
            **updates: Fields to update (title, content, priority, etc.)

        Returns:
            Updated task dictionary

        Raises:
            Exception: If the update fails
        """
        self._invalidate_cache()
        try:
            # TickTick API requires the full task object for updates
            # First, fetch the existing task
            all_tasks = self.list_tasks()
            existing_task = None
            for task in all_tasks:
                if task.get("id") == task_id:
                    existing_task = task
                    break

            if not existing_task:
                unloaded = (
                    f" (projects that failed to load: {'; '.join(self.failed_projects)})"
                    if self.failed_projects
                    else ""
                )
                raise Exception(f"Task {task_id} not found{unloaded}")

            # Merge updates into existing task
            updated_task = {**existing_task, **updates}

            # TickTick API uses POST for updates, not PUT
            response = self.session.post(f"{self.BASE_URL}/task/{task_id}", json=updated_task)
            response.raise_for_status()

            # Handle empty response (TickTick sometimes returns empty string or no content)
            if not response.text or response.text.strip() == "":
                # Return the merged task
                return updated_task

            try:
                result = response.json()
                return result
            except ValueError:
                # JSON parsing failed, but request succeeded - return merged task
                return updated_task
        except requests.exceptions.HTTPError as e:
            raise Exception(
                f"Failed to update task {task_id}: {e.response.status_code} - {e.response.text}"
            )
        except Exception as e:
            raise Exception(f"Failed to update task {task_id}: {str(e)}")

    def delete_task(self, task_id: str) -> None:
        """Delete a task.

        Args:
            task_id: ID of task to delete

        Raises:
            Exception: If the deletion fails
        """
        self._invalidate_cache()
        try:
            response = self.session.delete(f"{self.BASE_URL}/task/{task_id}")
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            raise Exception(
                f"Failed to delete task {task_id}: {e.response.status_code} - {e.response.text}"
            )
        except Exception as e:
            raise Exception(f"Failed to delete task {task_id}: {str(e)}")

    def get_completed_tasks(
        self, start_date: str, end_date: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get completed tasks within a date range.

        NOTE: This endpoint is available in the old TickTick API (v2) but requires
        cookie-based authentication, not OAuth tokens. The Open API (v1) does not
        provide a completed tasks endpoint.

        WORKAROUND: Use list_tasks() and filter results by status == 2 (completed).

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            limit: Maximum number of tasks to return (default 100, max 100)

        Returns:
            List of completed task dictionaries

        Raises:
            Exception: This method is not available with OAuth authentication
        """
        raise Exception(
            "Get completed tasks endpoint requires cookie-based authentication (not OAuth). "
            "The Open API v1 does not provide this endpoint. "
            "Workaround: Use list_tasks() and filter by status == 2 for completed tasks."
        )

    def move_task(self, task_id: str, from_project_id: str, to_project_id: str) -> Dict[str, Any]:
        """Move a task from one project to another.

        Args:
            task_id: ID of task to move
            from_project_id: Current project ID
            to_project_id: Destination project ID

        Returns:
            Updated task dictionary

        Raises:
            Exception: If the move fails
        """
        self._invalidate_cache()
        try:
            endpoint = f"{self.BASE_URL}/project/{from_project_id}/task/{task_id}/move"
            data = {"taskId": task_id, "projectId": to_project_id}
            response = self.session.post(endpoint, json=data)
            response.raise_for_status()

            result = response.json()
            if not result or result == "":
                return {"success": True, "task_id": task_id, "new_project": to_project_id}

            return result
        except requests.exceptions.HTTPError as e:
            raise Exception(
                f"Failed to move task {task_id}: {e.response.status_code} - {e.response.text}"
            )
        except Exception as e:
            raise Exception(f"Failed to move task {task_id}: {str(e)}")

    def make_subtask(
        self, parent_task_id: str, child_task_id: str, project_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Make a task a subtask of another task.

        Args:
            parent_task_id: ID of the parent task
            child_task_id: ID of the task to make a subtask
            project_id: Optional project ID (will be looked up if not provided)

        Returns:
            Updated task dictionary

        Raises:
            Exception: If creating subtask relationship fails
        """
        self._invalidate_cache()
        try:
            # If project_id not provided, find it from the parent task
            if not project_id:
                all_tasks = self.list_tasks()
                parent_task = next((t for t in all_tasks if t["id"] == parent_task_id), None)
                if not parent_task:
                    raise Exception(f"Parent task {parent_task_id} not found")
                project_id = parent_task.get("projectId")

            if not project_id:
                raise Exception(f"Could not determine project ID for parent task {parent_task_id}")

            # Update the child task to set its parent
            endpoint = f"{self.BASE_URL}/task/{child_task_id}"
            data = {"parentId": parent_task_id, "projectId": project_id}
            response = self.session.post(endpoint, json=data)
            response.raise_for_status()

            result = response.json()
            if not result or result == "":
                return {"success": True, "child_id": child_task_id, "parent_id": parent_task_id}

            return result
        except requests.exceptions.HTTPError as e:
            raise Exception(
                f"Failed to create subtask relationship: "
                f"{e.response.status_code} - {e.response.text}"
            )
        except Exception as e:
            raise Exception(f"Failed to create subtask relationship: {str(e)}")

    def search_tasks(self, query: str) -> List[Dict[str, Any]]:
        """Search tasks by title or content.

        Args:
            query: Search query string

        Returns:
            List of matching tasks
        """
        all_tasks = self.list_tasks()
        query_lower = query.lower()

        matching_tasks = []
        for task in all_tasks:
            title = task.get("title", "").lower()
            content = task.get("content", "").lower()

            if query_lower in title or query_lower in content:
                matching_tasks.append(task)

        return matching_tasks

    def format_task_summary(
        self,
        task: Dict[str, Any],
        include_id: bool = False,
        project_name: Optional[str] = None,
        user_timezone: Optional[str] = None,
    ) -> str:
        """Format a task into a human-readable summary.

        Args:
            task: Task dictionary
            include_id: Whether to include task ID in output (default: False)
            project_name: Optional project name to show as [ProjectName] prefix
            user_timezone: Zone to show the due date/time in (default: configured)

        Returns:
            Formatted task summary string
        """
        title = task.get("title", "Untitled")
        task_id = task.get("id", "")
        priority = task.get("priority", 0)

        # Priority mapping
        priority_map = {0: "", 1: "(Low)", 3: "(Medium)", 5: "(High)"}
        priority_str = priority_map.get(priority, "")

        due_str = ""
        due = task_due_time(task, resolve_timezone(user_timezone))
        if due:
            due_format = "%Y-%m-%d" if task.get("isAllDay") else "%Y-%m-%d %H:%M"
            due_str = f" - Due: {due.strftime(due_format)}"

        # Project prefix
        proj_str = f"[{project_name}] " if project_name else ""

        # Include ID as ticktick marker for interactive checkboxes
        id_str = f" {{ticktick:{task_id}}}" if include_id and task_id else ""

        return f"{proj_str}{title} {priority_str}{due_str}{id_str}".strip()
