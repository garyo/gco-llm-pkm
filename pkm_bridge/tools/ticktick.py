"""TickTick integration tool for Claude."""

from typing import Dict, Any, Optional
from datetime import datetime
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
- Create new todos
- Mark tasks as complete
- Search for specific tasks

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
                    "enum": ["list_today", "list_all", "create", "complete", "search"],
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
                    "description": "Due date in ISO format YYYY-MM-DD (optional, for create)"
                },
                "priority": {
                    "type": "integer",
                    "description": "Priority: 0=None, 1=Low, 3=Medium, 5=High (optional, for create)"
                },
                "task_id": {
                    "type": "string",
                    "description": "Task ID (for complete, or use title to search)"
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
            if action == "list_today":
                tasks = client.get_today_tasks()
                if not tasks:
                    return "No tasks due today or overdue."

                task_summaries = [client.format_task_summary(t) for t in tasks]
                return f"Tasks due today or overdue ({len(tasks)}):\n" + "\n".join(f"• {s}" for s in task_summaries)

            elif action == "list_all":
                tasks = client.list_tasks()
                if not tasks:
                    return "No tasks found."

                task_summaries = [client.format_task_summary(t) for t in tasks]
                return f"All tasks ({len(tasks)}):\n" + "\n".join(f"• {s}" for s in task_summaries)

            elif action == "create":
                title = params.get('title')
                if not title:
                    return "Error: Title is required for creating a task"

                content = params.get('content')
                due_date = params.get('due_date')  # ISO format string
                priority = params.get('priority', 0)

                # Parse due date if provided
                due_dt = None
                if due_date:
                    try:
                        due_dt = datetime.fromisoformat(due_date)
                    except (ValueError, TypeError):
                        return f"Error: Invalid due_date format: {due_date}"

                task = client.create_task(
                    title=title,
                    content=content,
                    due_date=due_dt,
                    priority=priority
                )

                return f"✓ Created task: {title}"

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

                task_summaries = [client.format_task_summary(t) for t in tasks]
                return f"Tasks matching '{query}' ({len(tasks)}):\n" + "\n".join(f"• {s}" for s in task_summaries)

            else:
                return f"Error: Unknown action: {action}"

        except Exception as e:
            self.logger.error(f"TickTick error: {e}")
            return f"Error: {str(e)}"

