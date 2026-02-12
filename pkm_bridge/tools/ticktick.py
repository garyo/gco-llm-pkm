"""TickTick integration tool for Claude."""

from typing import Dict, Any, Optional
from datetime import datetime
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
        return """Query and manage TickTick tasks. Use this to:
- List today's tasks or all tasks (including Inbox if configured)
- Create new todos with optional timed reminders
- Update existing tasks (change title, due date, priority, etc.)
- Mark tasks as complete
- Search for specific tasks

IMPORTANT: To update a task, first search for it to get its task_id.
The search results include [ID: xxx] which is the task_id needed for updates.

Date/Time Support:
- All-day tasks: Use date only (YYYY-MM-DD) or datetime at midnight (YYYY-MM-DDT00:00:00)
- Timed tasks: Use datetime with specific time (YYYY-MM-DDTHH:MM:SS)
- Reminders: Use ISO 8601 duration format - "TRIGGER:-PT30M" (30 min before), "TRIGGER:-PT1H" (1 hour before), etc.

Note: Inbox tasks are included if TICKTICK_INBOX_ID is configured in .env

Connection status: Check /auth/ticktick/status. If not connected, user needs to visit /auth/ticktick/authorize."""

    @property
    def input_schema(self) -> Dict[str, Any]:
        """JSON schema for tool input."""
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list_today", "list_all", "create", "update", "complete", "search"],
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
                    "description": "Due date/time in ISO format. Use YYYY-MM-DD for all-day tasks, or YYYY-MM-DDTHH:MM:SS for timed tasks with reminders (optional, for create/update)"
                },
                "priority": {
                    "type": "integer",
                    "description": "Priority: 0=None, 1=Low, 3=Medium, 5=High (optional, for create/update)"
                },
                "reminders": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of reminder triggers in ISO 8601 duration format (optional, for create/update). Examples: 'TRIGGER:-PT30M' (30 min before), 'TRIGGER:-PT1H' (1 hour before), 'TRIGGER:-PT2H' (2 hours before)"
                },
                "task_id": {
                    "type": "string",
                    "description": "Task ID (for update or complete). Get this from search results - look for [ID: xxx]"
                },
                "query": {
                    "type": "string",
                    "description": "Search query (for search action)"
                }
            },
            "required": ["action"]
        }

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

            if action == "list_today":
                tasks = client.get_today_tasks(user_timezone=user_timezone)
                if not tasks:
                    return "No tasks due today or overdue."

                task_summaries = [client.format_task_summary(t, include_id=True) for t in tasks]
                return f"Tasks due today or overdue ({len(tasks)}):\n" + "\n".join(f"- [ ] {s}" for s in task_summaries)

            elif action == "list_all":
                tasks = client.list_tasks()
                if not tasks:
                    return "No tasks found."

                task_summaries = [client.format_task_summary(t, include_id=True) for t in tasks]
                return f"All tasks ({len(tasks)}):\n" + "\n".join(f"- [ ] {s}" for s in task_summaries)

            elif action == "create":
                title = params.get('title')
                if not title:
                    return "Error: Title is required for creating a task"

                content = params.get('content')
                due_date = params.get('due_date')  # ISO format string
                priority = params.get('priority', 0)
                reminders = params.get('reminders')  # List of reminder trigger strings

                # Parse due date if provided
                due_dt = None
                is_all_day = None
                if due_date:
                    try:
                        # Try to parse as datetime (with time component)
                        if 'T' in due_date:
                            due_dt = datetime.fromisoformat(due_date)
                            # Determine if it's all-day based on time component
                            is_all_day = (due_dt.hour == 0 and due_dt.minute == 0 and due_dt.second == 0)
                        else:
                            # Date only - treat as all-day task
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
                return f"✓ Created {task_type} task: {title}{reminder_info}"

            elif action == "update":
                task_id = params.get('task_id')
                if not task_id:
                    return "Error: task_id is required for updating a task"

                # Build updates dict from provided parameters
                updates = {}
                if 'title' in params:
                    updates['title'] = params['title']
                if 'content' in params:
                    updates['content'] = params['content']
                if 'due_date' in params:
                    due_date_str = params['due_date']

                    # Determine if it's a timed task or all-day task
                    if 'T' not in due_date_str:
                        # All-day task: Convert date to midnight in user's timezone, then to UTC
                        due_date_obj = datetime.fromisoformat(due_date_str)

                        if user_timezone:
                            try:
                                tz = ZoneInfo(user_timezone)
                                # Create timezone-aware datetime at midnight in user's timezone
                                due_date_local = due_date_obj.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=tz)
                                # Convert to UTC
                                due_date_utc = due_date_local.astimezone(ZoneInfo('UTC'))
                                # Format with milliseconds as TickTick expects
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
                        # Timed task: preserve the time component
                        due_date_obj = datetime.fromisoformat(due_date_str)

                        # Check if it's midnight (should be all-day)
                        is_all_day = (due_date_obj.hour == 0 and due_date_obj.minute == 0 and due_date_obj.second == 0)

                        if user_timezone:
                            try:
                                tz = ZoneInfo(user_timezone)
                                # Create timezone-aware datetime in user's timezone
                                if due_date_obj.tzinfo is None:
                                    due_date_local = due_date_obj.replace(tzinfo=tz)
                                else:
                                    due_date_local = due_date_obj.astimezone(tz)
                                # Convert to UTC
                                due_date_utc = due_date_local.astimezone(ZoneInfo('UTC'))
                                due_date_formatted = due_date_utc.strftime('%Y-%m-%dT%H:%M:%S.000+0000')
                                updates['timeZone'] = user_timezone
                            except Exception as e:
                                self.logger.warning(f"Error converting timezone: {e}, using as-is")
                                due_date_formatted = due_date_str
                        else:
                            # No timezone, assume UTC
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
                return f"✓ Updated {task_type} task: {task.get('title', task_id)}"

            elif action == "complete":
                task_id = params.get('task_id')
                if not task_id:
                    # Try to find by title
                    title = params.get('title')
                    if not title:
                        return "Error: Task ID or title is required"

                    # Search for task
                    tasks = client.search_tasks(title)
                    if not tasks:
                        return f"Error: Task not found: {title}"
                    if len(tasks) > 1:
                        matches = [client.format_task_summary(t) for t in tasks]
                        return f"Error: Multiple tasks found matching '{title}':\n" + "\n".join(f"• {m}" for m in matches)

                    task_id = tasks[0]['id']

                task = client.complete_task(task_id)
                return f"✓ Completed task: {task.get('title', task_id)}"

            elif action == "search":
                query = params.get('query')
                if not query:
                    return "Error: Query is required for searching"

                tasks = client.search_tasks(query)
                if not tasks:
                    return f"No tasks found matching '{query}'."

                # Include task IDs in search results so they can be used for update/complete
                task_summaries = [client.format_task_summary(t, include_id=True) for t in tasks]
                return f"Tasks matching '{query}' ({len(tasks)}):\n" + "\n".join(f"• {s}" for s in task_summaries)

            else:
                return f"Error: Unknown action: {action}"

        except Exception as e:
            self.logger.error(f"TickTick error: {e}")
            return f"Error: {str(e)}"

