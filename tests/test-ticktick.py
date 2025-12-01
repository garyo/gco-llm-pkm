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
  ./test-ticktick.py create "Task title"    # Create a test task (all-day)
  ./test-ticktick.py create-timed "Task title" 14:30  # Create timed task at 2:30 PM
  ./test-ticktick.py create-with-reminders "Task title" 10:00  # Create timed task with reminders
  ./test-ticktick.py completed 2025-10-01 2025-10-31  # Get completed tasks
"""

import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

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
    """Create an all-day test task."""
    print(f"\n=== Creating All-Day Test Task ===\n")

    # Create all-day task - set time to midnight
    tomorrow = datetime.now() + timedelta(days=1)
    tomorrow = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)

    # Get timezone from env or default
    timezone = os.getenv('USER_TIMEZONE', 'America/New_York')

    try:
        result = client.create_task(
            title=title,
            content="Test task created by test-ticktick.py",
            due_date=tomorrow,
            priority=3,  # Medium
            user_timezone=timezone,
            is_all_day=True  # Explicitly set as all-day
        )
        print("\n✓ All-day task created successfully!")
        print(f"  ID: {result['id']}")
        print(f"  Title: {result['title']}")
        print(f"  Due: {result.get('dueDate', 'No due date')}")
        print(f"  IsAllDay: {result.get('isAllDay', 'Unknown')}")
        print(f"  Priority: {result.get('priority', 0)}")
        print(f"\nCheck TickTick UI - task should show as all-day with no specific time")
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


def cmd_create_timed(client: TickTickClient, title: str, time_str: str):
    """Create a timed task (not all-day) at specific time tomorrow.

    Args:
        title: Task title
        time_str: Time in HH:MM format (e.g., "14:30")
    """
    print(f"\n=== Creating Timed Task ===\n")

    # Parse time
    try:
        hour, minute = map(int, time_str.split(':'))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError("Invalid time range")
    except (ValueError, AttributeError):
        print(f"Invalid time format: {time_str}. Use HH:MM (e.g., 14:30)")
        return

    # Create datetime for tomorrow at specified time
    tomorrow = datetime.now() + timedelta(days=1)
    due_date = tomorrow.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # Get timezone from env or default
    timezone = os.getenv('USER_TIMEZONE', 'America/New_York')

    print(f"Creating timed task for: {due_date.strftime('%Y-%m-%d %H:%M')}")
    print(f"Timezone: {timezone}")

    try:
        result = client.create_task(
            title=title,
            content="Timed test task created by test-ticktick.py",
            due_date=due_date,
            priority=3,  # Medium
            user_timezone=timezone,
            is_all_day=False  # Explicitly set as timed task
        )
        print("\n✓ Timed task created successfully!")
        print(f"  ID: {result['id']}")
        print(f"  Title: {result['title']}")
        print(f"  Due: {result.get('dueDate', 'No due date')}")
        print(f"  IsAllDay: {result.get('isAllDay', 'Unknown')}")
        print(f"  Priority: {result.get('priority', 0)}")
        print(f"\nCheck TickTick UI - task should show at {time_str} (not as all-day)")
    except Exception as e:
        print(f"\n✗ Creation failed: {e}")
        import traceback
        traceback.print_exc()


def cmd_create_with_reminders(client: TickTickClient, title: str, time_str: str):
    """Create a timed task with reminders.

    Args:
        title: Task title
        time_str: Time in HH:MM format (e.g., "10:00")
    """
    print(f"\n=== Creating Timed Task with Reminders ===\n")

    # Parse time
    try:
        hour, minute = map(int, time_str.split(':'))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError("Invalid time range")
    except (ValueError, AttributeError):
        print(f"Invalid time format: {time_str}. Use HH:MM (e.g., 10:00)")
        return

    # Create datetime for day after tomorrow at specified time
    day_after = datetime.now() + timedelta(days=2)
    due_date = day_after.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # Get timezone from env or default
    timezone = os.getenv('USER_TIMEZONE', 'America/New_York')

    # Set up reminders
    reminders = [
        "TRIGGER:-PT30M",  # 30 minutes before
        "TRIGGER:-PT1H",   # 1 hour before
    ]

    print(f"Creating timed task for: {due_date.strftime('%Y-%m-%d %H:%M')}")
    print(f"Timezone: {timezone}")
    print(f"Reminders: 30 minutes before, 1 hour before")

    try:
        result = client.create_task(
            title=title,
            content="Timed test task with reminders, created by test-ticktick.py",
            due_date=due_date,
            priority=5,  # High
            user_timezone=timezone,
            reminders=reminders,
            is_all_day=False  # Explicitly set as timed task
        )
        print("\n✓ Timed task with reminders created successfully!")
        print(f"  ID: {result['id']}")
        print(f"  Title: {result['title']}")
        print(f"  Due: {result.get('dueDate', 'No due date')}")
        print(f"  IsAllDay: {result.get('isAllDay', 'Unknown')}")
        print(f"  Reminders: {result.get('reminders', [])}")
        print(f"  Priority: {result.get('priority', 0)}")
        print(f"\nCheck TickTick UI - task should show:")
        print(f"  - Time: {time_str} in your timezone")
        print(f"  - 2 reminders set (30 min and 1 hour before)")
    except Exception as e:
        print(f"\n✗ Creation failed: {e}")
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

    elif command == "create-timed":
        if len(sys.argv) < 4:
            print("Usage: ./test-ticktick.py create-timed \"Task title\" HH:MM")
            print("  Example: ./test-ticktick.py create-timed \"Meeting prep\" 14:30")
            sys.exit(1)
        cmd_create_timed(client, sys.argv[2], sys.argv[3])

    elif command == "create-with-reminders":
        if len(sys.argv) < 4:
            print("Usage: ./test-ticktick.py create-with-reminders \"Task title\" HH:MM")
            print("  Example: ./test-ticktick.py create-with-reminders \"Doctor appointment\" 10:00")
            sys.exit(1)
        cmd_create_with_reminders(client, sys.argv[2], sys.argv[3])

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

