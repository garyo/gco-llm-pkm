"""TickTick API client for task management."""

import os
from datetime import datetime
from typing import List, Dict, Optional, Any
import requests


class TickTickClient:
    """Client for TickTick Open API."""

    BASE_URL = "https://api.ticktick.com/open/v1"

    def __init__(self, access_token: str):
        """Initialize TickTick client.

        Args:
            access_token: OAuth access token
        """
        self.access_token = access_token
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        })


    def list_projects(self) -> List[Dict[str, Any]]:
        """Get all projects.

        Returns:
            List of project dictionaries with id, name, etc.
        """
        response = self.session.get(f"{self.BASE_URL}/project")
        response.raise_for_status()
        return response.json()

    def list_tasks(self, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all tasks, optionally filtered by project.

        Args:
            project_id: Optional project ID to filter tasks

        Returns:
            List of task dictionaries
        """
        try:
            if not project_id:
                # Get all tasks from all projects
                projects = self.list_projects()
                all_tasks = []

                # Try to get inbox tasks if configured
                inbox_id = os.getenv('TICKTICK_INBOX_ID')
                if inbox_id:
                    try:
                        response = self.session.get(f"{self.BASE_URL}/project/{inbox_id}/data")
                        response.raise_for_status()
                        data = response.json()
                        inbox_tasks = data.get('tasks', [])
                        all_tasks.extend(inbox_tasks)
                    except Exception:
                        # Inbox access failed, continue without it
                        pass

                # Get tasks from all regular projects
                for project in projects:
                    proj_id = project.get('id')
                    if proj_id:
                        try:
                            response = self.session.get(f"{self.BASE_URL}/project/{proj_id}/data")
                            response.raise_for_status()
                            data = response.json()
                            tasks = data.get('tasks', [])
                            all_tasks.extend(tasks)
                        except Exception:
                            # Skip projects that fail to load
                            continue

                return all_tasks
            else:
                # Get tasks for specific project
                response = self.session.get(f"{self.BASE_URL}/project/{project_id}/data")
                response.raise_for_status()
                data = response.json()
                return data.get('tasks', [])

        except Exception as e:
            raise Exception(f"TickTick API Error: {e}")

    def get_today_tasks(self) -> List[Dict[str, Any]]:
        """Get tasks due today or overdue.

        Returns:
            List of tasks due today or overdue
        """
        all_tasks = self.list_tasks()
        today = datetime.now().date()

        today_tasks = []
        for task in all_tasks:
            # Check if task has a due date
            due_date = task.get('dueDate')
            if not due_date:
                continue

            # Parse due date (format: 2025-10-29T00:00:00+0000)
            try:
                task_date = datetime.fromisoformat(due_date.replace('Z', '+00:00')).date()
                if task_date <= today:
                    today_tasks.append(task)
            except (ValueError, AttributeError):
                continue

        return today_tasks

    def create_task(
        self,
        title: str,
        content: Optional[str] = None,
        due_date: Optional[datetime] = None,
        priority: int = 0,
        project_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new task.

        Args:
            title: Task title
            content: Task description/content
            due_date: Due date for the task
            priority: Priority level (0=None, 1=Low, 3=Medium, 5=High)
            project_id: Project ID to add task to

        Returns:
            Created task dictionary
        """
        task_data = {
            'title': title
        }

        if content:
            task_data['content'] = content

        if due_date:
            # Format: YYYY-MM-DDTHH:MM:SS+0000
            task_data['dueDate'] = due_date.strftime('%Y-%m-%dT%H:%M:%S+0000')

        if priority:
            task_data['priority'] = priority

        if project_id:
            task_data['projectId'] = project_id

        response = self.session.post(f"{self.BASE_URL}/task", json=task_data)
        response.raise_for_status()
        return response.json()

    def complete_task(self, task_id: str, project_id: Optional[str] = None) -> Dict[str, Any]:
        """Mark a task as complete.

        Args:
            task_id: ID of task to complete
            project_id: Optional project ID (will be looked up if not provided)

        Returns:
            Success status dictionary
        """
        # If project_id not provided, find it by searching all projects
        if not project_id:
            projects = self.list_projects()
            inbox_id = os.getenv('TICKTICK_INBOX_ID')

            # Check inbox first if configured
            if inbox_id:
                try:
                    response = self.session.get(f"{self.BASE_URL}/project/{inbox_id}/data")
                    if response.ok:
                        data = response.json()
                        for task in data.get('tasks', []):
                            if task.get('id') == task_id:
                                project_id = inbox_id
                                break
                except Exception:
                    pass

            # Check regular projects if not found in inbox
            if not project_id:
                for project in projects:
                    proj_id = project.get('id')
                    if proj_id:
                        try:
                            response = self.session.get(f"{self.BASE_URL}/project/{proj_id}/data")
                            if response.ok:
                                data = response.json()
                                for task in data.get('tasks', []):
                                    if task.get('id') == task_id:
                                        project_id = proj_id
                                        break
                        except Exception:
                            continue
                    if project_id:
                        break

            if not project_id:
                raise Exception(f"Could not find project for task {task_id}")

        # Complete the task using the correct endpoint
        response = self.session.post(f"{self.BASE_URL}/project/{project_id}/task/{task_id}/complete")
        response.raise_for_status()

        # API returns empty response on success
        return {"success": True, "task_id": task_id}

    def update_task(self, task_id: str, **updates) -> Dict[str, Any]:
        """Update a task.

        Args:
            task_id: ID of task to update
            **updates: Fields to update (title, content, priority, etc.)

        Returns:
            Updated task dictionary
        """
        response = self.session.put(f"{self.BASE_URL}/task/{task_id}", json=updates)
        response.raise_for_status()
        return response.json()

    def delete_task(self, task_id: str) -> None:
        """Delete a task.

        Args:
            task_id: ID of task to delete
        """
        response = self.session.delete(f"{self.BASE_URL}/task/{task_id}")
        response.raise_for_status()

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
            title = task.get('title', '').lower()
            content = task.get('content', '').lower()

            if query_lower in title or query_lower in content:
                matching_tasks.append(task)

        return matching_tasks

    def format_task_summary(self, task: Dict[str, Any]) -> str:
        """Format a task into a human-readable summary.

        Args:
            task: Task dictionary

        Returns:
            Formatted task summary string
        """
        title = task.get('title', 'Untitled')
        due_date = task.get('dueDate', '')
        priority = task.get('priority', 0)

        # Priority mapping
        priority_map = {0: '', 1: '(Low)', 3: '(Medium)', 5: '(High)'}
        priority_str = priority_map.get(priority, '')

        # Format due date
        due_str = ''
        if due_date:
            try:
                dt = datetime.fromisoformat(due_date.replace('Z', '+00:00'))
                due_str = f" - Due: {dt.strftime('%Y-%m-%d')}"
            except (ValueError, AttributeError):
                pass

        return f"{title} {priority_str}{due_str}".strip()
