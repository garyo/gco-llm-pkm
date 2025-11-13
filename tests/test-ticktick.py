#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "python-dotenv>=1.0.0",
#   "requests>=2.31.0",
#   "sqlalchemy>=2.0.23",
#   "psycopg2-binary>=2.9.9",
# ]
# ///
"""
Test script for TickTick API operations.

Usage:
  ./test-ticktick.py list                    # List all tasks
  ./test-ticktick.py today                   # Show today's tasks
  ./test-ticktick.py get <task_id>          # Get specific task details
  ./test-ticktick.py update <task_id> priority=5  # Update task priority
  ./test-ticktick.py update <task_id> dueDate=2025-11-01  # Update due date
  ./test-ticktick.py update <task_id> title="New title"   # Update title
  ./test-ticktick.py create "Task title"    # Create a test task
  ./test-ticktick.py completed 2025-10-01 2025-10-31  # Get completed tasks
"""

import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
from pkm_bridge.database import init_db, get_db
from pkm_bridge.db_repository import OAuthRepository
from pkm_bridge.ticktick_client import TickTickClient

# Load environment
load_dotenv()
load_dotenv('.env.local', override=True)


def get_client() -> TickTickClient:
    """Get authenticated TickTick client."""
    # Initialize database
    init_db()

    # Get access token from database
    db = get_db()
    try:
        token_obj = OAuthRepository.get_token(db, service='ticktick', user_id='default')
        if not token_obj:
            print("Error: No TickTick access token found. Please authenticate via the web UI first.")
            sys.exit(1)
        return TickTickClient(token_obj.access_token)
    finally:
        db.close()


def cmd_list(client: TickTickClient):
    """List all tasks."""
    print("\n=== All Tasks ===\n")
    tasks = client.list_tasks()

    if not tasks:
        print("No tasks found.")
        return

    for task in tasks:
        print(f"ID: {task['id']}")
        print(f"  Title: {task.get('title', 'Untitled')}")
        print(f"  Status: {task.get('status', 0)} (0=active, 2=completed)")
        print(f"  Priority: {task.get('priority', 0)} (0=None, 1=Low, 3=Medium, 5=High)")
        print(f"  Due: {task.get('dueDate', 'No due date')}")
        print(f"  Project: {task.get('projectId', 'None')}")
        if task.get('content'):
            print(f"  Content: {task['content'][:100]}")
        print()


def cmd_today(client: TickTickClient):
    """Show today's tasks."""
    print("\n=== Today's Tasks ===\n")
    tasks = client.get_today_tasks()

    if not tasks:
        print("No tasks due today or overdue.")
        return

    for task in tasks:
        print(client.format_task_summary(task))
        print(f"  ID: {task['id']}")
        print()


def cmd_get(client: TickTickClient, task_id: str):
    """Get specific task details."""
    # Find the task
    all_tasks = client.list_tasks()
    task = next((t for t in all_tasks if t['id'] == task_id), None)

    if not task:
        print(f"Task {task_id} not found")
        return

    print(f"\n=== Task Details: {task_id} ===\n")

    # Pretty print all fields
    import json
    print(json.dumps(task, indent=2))


def cmd_update(client: TickTickClient, task_id: str, updates: dict):
    """Update a task with new field values."""
    print(f"\n=== Updating Task: {task_id} ===\n")
    print(f"Updates: {updates}")

    # First, get the current task to have all fields
    all_tasks = client.list_tasks()
    task = next((t for t in all_tasks if t['id'] == task_id), None)

    if not task:
        print(f"Task {task_id} not found")
        return

    # Start with minimal update - only changed fields
    transformed = {}

    # Apply updates
    for key, value in updates.items():
        if key == 'dueDate':
            # Convert date string to TickTick format
            # Input: YYYY-MM-DD, Output: YYYY-MM-DDTHH:MM:SS+0000
            try:
                dt = datetime.strptime(value, '%Y-%m-%d')
                if task.get('isAllDay'):
                    # For all-day tasks, use 04:00:00 (UTC) like the original
                    transformed['dueDate'] = dt.strftime('%Y-%m-%dT04:00:00.000+0000')
                    # Also update startDate to match
                    transformed['startDate'] = dt.strftime('%Y-%m-%dT04:00:00.000+0000')
                else:
                    transformed['dueDate'] = dt.strftime('%Y-%m-%dT00:00:00.000+0000')
            except ValueError:
                print(f"Invalid date format: {value}. Use YYYY-MM-DD")
                return
        elif key == 'priority':
            # Convert to int
            transformed['priority'] = int(value)
        else:
            transformed[key] = value

    print(f"\nSending to API:")
    import json
    print(json.dumps(transformed, indent=2))

    try:
        result = client.update_task(task_id, **transformed)
        print("\n✓ Task updated successfully!")
        print(f"\nUpdated task:")
        print(f"  Title: {result.get('title', 'Untitled')}")
        print(f"  Priority: {result.get('priority', 0)}")
        print(f"  Due: {result.get('dueDate', 'No due date')}")
        if result.get('startDate'):
            print(f"  Start: {result.get('startDate')}")
    except Exception as e:
        print(f"\n✗ Update failed: {e}")
        # Try to get response body if available
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response status: {e.response.status_code}")
            print(f"Response body: {e.response.text}")
        import traceback
        traceback.print_exc()


def cmd_create(client: TickTickClient, title: str):
    """Create a test task."""
    print(f"\n=== Creating Test Task ===\n")

    # Create with various fields
    tomorrow = datetime.now() + timedelta(days=1)

    try:
        result = client.create_task(
            title=title,
            content="Test task created by test-ticktick.py",
            due_date=tomorrow,
            priority=3  # Medium
        )
        print("\n✓ Task created successfully!")
        print(f"  ID: {result['id']}")
        print(f"  Title: {result['title']}")
        print(f"  Due: {result.get('dueDate', 'No due date')}")
        print(f"  Priority: {result.get('priority', 0)}")
    except Exception as e:
        print(f"\n✗ Creation failed: {e}")
        import traceback
        traceback.print_exc()


def cmd_completed(client: TickTickClient, start: str, end: str):
    """Get completed tasks in date range."""
    print(f"\n=== Completed Tasks: {start} to {end} ===\n")

    try:
        tasks = client.get_completed_tasks(start, end)

        if not tasks:
            print("No completed tasks in this date range.")
            return

        for task in tasks:
            print(f"✓ {task.get('title', 'Untitled')}")
            print(f"  Completed: {task.get('completedTime', 'Unknown')}")
            if task.get('dueDate'):
                print(f"  Due: {task.get('dueDate')}")
            print()
    except Exception as e:
        print(f"\n✗ Failed: {e}")
        import traceback
        traceback.print_exc()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    # Get client
    client = get_client()

    # Route commands
    if command == "list":
        cmd_list(client)

    elif command == "today":
        cmd_today(client)

    elif command == "get":
        if len(sys.argv) < 3:
            print("Usage: ./test-ticktick.py get <task_id>")
            sys.exit(1)
        cmd_get(client, sys.argv[2])

    elif command == "update":
        if len(sys.argv) < 4:
            print("Usage: ./test-ticktick.py update <task_id> field=value [field2=value2 ...]")
            sys.exit(1)

        task_id = sys.argv[2]

        # Parse field=value pairs
        updates = {}
        for arg in sys.argv[3:]:
            if '=' not in arg:
                print(f"Invalid update format: {arg}. Use field=value")
                sys.exit(1)
            key, value = arg.split('=', 1)
            # Remove quotes if present
            value = value.strip('"').strip("'")
            updates[key] = value

        cmd_update(client, task_id, updates)

    elif command == "create":
        if len(sys.argv) < 3:
            print("Usage: ./test-ticktick.py create \"Task title\"")
            sys.exit(1)
        cmd_create(client, sys.argv[2])

    elif command == "completed":
        if len(sys.argv) < 4:
            print("Usage: ./test-ticktick.py completed <start_date> <end_date>")
            print("  Example: ./test-ticktick.py completed 2025-10-01 2025-10-31")
            sys.exit(1)
        cmd_completed(client, sys.argv[2], sys.argv[3])

    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == '__main__':
    main()

