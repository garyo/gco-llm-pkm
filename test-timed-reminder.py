#!/usr/bin/env python3
"""Test TickTick timed reminder functionality."""

import os
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()

from pkm_bridge.ticktick_oauth import TickTickOAuth
from pkm_bridge.tools.ticktick import TickTickTool

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def test_timed_reminder():
    """Test creating timed tasks with reminders."""

    # Initialize OAuth
    try:
        oauth_handler = TickTickOAuth()
    except ValueError as e:
        print(f"✗ TickTick not configured: {e}")
        return False

    # Initialize tool
    tool = TickTickTool(logger, oauth_handler)

    print("=" * 60)
    print("TickTick Timed Reminder Test")
    print("=" * 60)

    # Simulate user context with timezone
    context = {
        'user_timezone': 'America/New_York'
    }

    # Test 1: Create an all-day task (backward compatibility)
    print("\n1. Creating all-day task (backward compatibility)...")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

    result = tool.execute({
        "action": "create",
        "title": "TEST: All-Day Task (delete me)",
        "due_date": tomorrow,
        "priority": 0
    }, context=context)
    print(result)

    if "all-day" not in result.lower():
        print("⚠️  Warning: Expected 'all-day' in result")

    # Test 2: Create a timed task with specific time
    print("\n2. Creating timed task at 2:30 PM...")
    tomorrow_2pm = (datetime.now() + timedelta(days=1)).replace(hour=14, minute=30, second=0, microsecond=0)
    due_date_str = tomorrow_2pm.strftime('%Y-%m-%dT%H:%M:%S')

    result = tool.execute({
        "action": "create",
        "title": "TEST: Timed Task at 2:30 PM (delete me)",
        "content": "This should show as a timed task, not all-day",
        "due_date": due_date_str,
        "priority": 3
    }, context=context)
    print(result)

    if "timed" not in result.lower():
        print("⚠️  Warning: Expected 'timed' in result")

    # Test 3: Create a timed task with reminders
    print("\n3. Creating timed task with reminders...")
    day_after_tomorrow = (datetime.now() + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
    due_date_str = day_after_tomorrow.strftime('%Y-%m-%dT%H:%M:%S')

    result = tool.execute({
        "action": "create",
        "title": "TEST: Task with Reminders (delete me)",
        "content": "This task should have reminders set",
        "due_date": due_date_str,
        "priority": 5,
        "reminders": [
            "TRIGGER:-PT30M",  # 30 minutes before
            "TRIGGER:-PT1H"    # 1 hour before
        ]
    }, context=context)
    print(result)

    if "2 reminder" not in result.lower():
        print("⚠️  Warning: Expected '2 reminder' in result")

    print("\n" + "=" * 60)
    print("✓ Test completed!")
    print("=" * 60)
    print("\nPlease check these tasks in TickTick UI and verify:")
    print("1. First task is all-day (no specific time)")
    print("2. Second task shows time of 2:30 PM in your timezone")
    print("3. Third task shows time of 10:00 AM with 2 reminders set")
    print("\nYou can delete these test tasks after verification.")

    return True


if __name__ == '__main__':
    import sys
    success = test_timed_reminder()
    sys.exit(0 if success else 1)
