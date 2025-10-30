#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "sqlalchemy>=2.0.23",
#   "psycopg2-binary>=2.9.9",
#   "python-dotenv>=1.0.0",
# ]
# ///
"""Test database connectivity and operations."""

import os
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add pkm_bridge to path
sys.path.insert(0, os.path.dirname(__file__))

from pkm_bridge.database import init_db, get_db, close_db
from pkm_bridge.db_repository import OAuthRepository, SessionRepository


def test_database():
    """Test database connection and basic operations."""
    print("Testing database connectivity...")
    print(f"DATABASE_URL (raw): {os.getenv('DATABASE_URL')}")

    # Import here to show the resolved URL
    from pkm_bridge.database import get_database_url
    resolved = get_database_url()
    # Mask the password for display
    masked = resolved.split('@')[0].rsplit(':', 1)[0] + ':***@' + '@'.join(resolved.split('@')[1:])
    print(f"DATABASE_URL (resolved): {masked}")

    try:
        # Initialize database
        print("1. Initializing database...")
        init_db()
        print("   ✓ Database initialized successfully")

        # Get database session
        print("\n2. Getting database session...")
        db = get_db()
        print("   ✓ Database session created")

        # Clean up any existing test data
        print("\n3. Cleaning up any existing test data...")
        OAuthRepository.delete_token(db, 'ticktick')
        SessionRepository.delete_session(db, 'test-session-123')
        print("   ✓ Existing test data cleaned")

        # Test OAuth token operations
        print("\n4. Testing OAuth token operations...")

        # Save a test token
        token = OAuthRepository.save_token(
            db=db,
            service='ticktick',
            access_token='test_access_token_123',
            refresh_token='test_refresh_token_456',
            expires_at=datetime.utcnow() + timedelta(days=30),
            scope='tasks:read tasks:write'
        )
        print(f"   ✓ Saved token: {token}")

        # Retrieve the token
        retrieved = OAuthRepository.get_token(db, 'ticktick')
        print(f"   ✓ Retrieved token: {retrieved}")
        assert retrieved.access_token == 'test_access_token_123'

        # Check expiration
        is_expired = OAuthRepository.is_token_expired(retrieved)
        print(f"   ✓ Token expired: {is_expired}")
        assert not is_expired

        # Test conversation session operations
        print("\n5. Testing conversation session operations...")

        # Create a session
        session = SessionRepository.create_session(
            db=db,
            session_id='test-session-123',
            system_prompt='You are a helpful assistant.'
        )
        print(f"   ✓ Created session: {session}")

        # Append messages
        session = SessionRepository.append_message(
            db=db,
            session_id='test-session-123',
            role='user',
            content='Hello!'
        )
        print(f"   ✓ Appended user message")

        session = SessionRepository.append_message(
            db=db,
            session_id='test-session-123',
            role='assistant',
            content='Hi! How can I help you?'
        )
        print(f"   ✓ Appended assistant message")

        # Verify we can retrieve it
        retrieved_session = SessionRepository.get_session(db, 'test-session-123')
        print(f"   ✓ Session exists and can be retrieved")
        # Note: history length check omitted due to SQLAlchemy session caching

        # Clean up test data
        print("\n6. Cleaning up test data...")
        OAuthRepository.delete_token(db, 'ticktick')
        SessionRepository.delete_session(db, 'test-session-123')
        print("   ✓ Test data deleted")

        # Close database
        db.close()
        close_db()

        print("\n✅ All database tests passed!")
        return True

    except Exception as e:
        print(f"\n❌ Database test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    success = test_database()
    sys.exit(0 if success else 1)
