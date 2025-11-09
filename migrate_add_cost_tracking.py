#!/usr/bin/env python3
"""Migration script to add cost tracking fields to conversation_sessions table."""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from pkm_bridge.database import get_db, ConversationSession, init_db
from sqlalchemy import text

def migrate():
    """Add cost tracking columns to conversation_sessions table."""
    print("Starting migration: add cost tracking fields...")

    # Initialize database
    init_db()
    db = get_db()

    try:
        # Check if columns already exist
        result = db.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name='conversation_sessions'
            AND column_name IN ('total_input_tokens', 'total_output_tokens', 'total_cost')
        """))
        existing_columns = [row[0] for row in result]

        if len(existing_columns) == 3:
            print("✓ Cost tracking columns already exist, skipping migration")
            return

        print(f"Found {len(existing_columns)} existing columns, adding remaining columns...")

        # Add columns that don't exist
        if 'total_input_tokens' not in existing_columns:
            print("Adding total_input_tokens column...")
            db.execute(text("""
                ALTER TABLE conversation_sessions
                ADD COLUMN total_input_tokens INTEGER NOT NULL DEFAULT 0
            """))

        if 'total_output_tokens' not in existing_columns:
            print("Adding total_output_tokens column...")
            db.execute(text("""
                ALTER TABLE conversation_sessions
                ADD COLUMN total_output_tokens INTEGER NOT NULL DEFAULT 0
            """))

        if 'total_cost' not in existing_columns:
            print("Adding total_cost column...")
            db.execute(text("""
                ALTER TABLE conversation_sessions
                ADD COLUMN total_cost REAL NOT NULL DEFAULT 0.0
            """))

        db.commit()
        print("✓ Migration completed successfully!")

    except Exception as e:
        db.rollback()
        print(f"✗ Migration failed: {e}")
        sys.exit(1)
    finally:
        db.close()

if __name__ == "__main__":
    migrate()
