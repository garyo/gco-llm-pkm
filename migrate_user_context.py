#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "python-dotenv>=1.0.0",
#   "sqlalchemy>=2.0.23",
#   "psycopg2-binary>=2.9.9",
# ]
# ///
"""
Migrate user context from file to database.

This script reads config/user_context.txt and stores it in the database.
Run this once to migrate from file-based to database-based user context.
"""

from pathlib import Path
import sys

from pkm_bridge.database import init_db, get_db
from pkm_bridge.db_repository import UserSettingsRepository


def migrate_user_context():
    """Migrate user context from file to database."""
    # Path to the user context file
    user_context_file = Path(__file__).parent / "config" / "user_context.txt"

    if not user_context_file.exists():
        print(f"❌ User context file not found: {user_context_file}")
        print("   Nothing to migrate.")
        return False

    # Read the file
    try:
        user_context = user_context_file.read_text(encoding="utf-8")
        print(f"✓ Read user context from file ({len(user_context)} characters)")
    except Exception as e:
        print(f"❌ Error reading file: {e}")
        return False

    # Initialize database
    try:
        init_db()
        print("✓ Database initialized")
    except Exception as e:
        print(f"❌ Database initialization failed: {e}")
        return False

    # Check if context already exists in database
    db = get_db()
    try:
        existing_context = UserSettingsRepository.get_user_context(db, user_id='default')

        if existing_context:
            print(f"⚠ User context already exists in database ({len(existing_context)} characters)")
            response = input("  Overwrite with file content? [y/N]: ")
            if response.lower() != 'y':
                print("  Migration cancelled.")
                db.close()
                return False

        # Save to database
        UserSettingsRepository.save_user_context(db, user_context, user_id='default')
        print("✓ User context saved to database")

    except Exception as e:
        print(f"❌ Error saving to database: {e}")
        db.close()
        return False
    finally:
        db.close()

    # Optionally rename the file
    response = input("\nRename config/user_context.txt to user_context.txt.bak? [Y/n]: ")
    if response.lower() != 'n':
        backup_file = user_context_file.with_suffix('.txt.bak')
        try:
            user_context_file.rename(backup_file)
            print(f"✓ Renamed {user_context_file.name} to {backup_file.name}")
        except Exception as e:
            print(f"⚠ Could not rename file: {e}")

    print("\n✅ Migration complete!")
    print("   You can now edit your user context through the Settings page in the web UI.")
    return True


if __name__ == '__main__':
    print("=" * 60)
    print("User Context Migration Script")
    print("=" * 60)
    print()

    success = migrate_user_context()
    sys.exit(0 if success else 1)
