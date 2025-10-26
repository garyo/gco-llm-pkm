#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "bcrypt>=4.1.0",
# ]
# ///
"""Generate authentication credentials for PKM Bridge Server.

This script generates a secure JWT secret and password hash for use in .env file.
"""

import secrets
import bcrypt
import sys


def generate_jwt_secret() -> str:
    """Generate a secure random JWT secret key."""
    return secrets.token_hex(32)


def hash_password(password: str) -> str:
    """Hash a password using bcrypt with 12 rounds."""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode(), salt).decode('utf-8')


def main():
    print("=" * 60)
    print("PKM Bridge Server - Auth Configuration Generator")
    print("=" * 60)
    print()

    # Generate JWT secret
    jwt_secret = generate_jwt_secret()
    print("Generated JWT Secret:")
    print(f"  JWT_SECRET={jwt_secret}")
    print()

    # Get password from user
    print("Enter a password for logging in to the web interface.")
    print("This password will be hashed and stored in your .env file.")
    print()

    while True:
        password = input("Password: ").strip()

        if not password:
            print("Error: Password cannot be empty")
            continue

        if len(password) < 8:
            print("Warning: Password is less than 8 characters")
            confirm = input("Continue anyway? (y/n): ").strip().lower()
            if confirm != 'y':
                continue

        # Confirm password
        password_confirm = input("Confirm password: ").strip()

        if password != password_confirm:
            print("Error: Passwords do not match. Try again.")
            print()
            continue

        break

    # Hash password
    password_hash = hash_password(password)
    print()
    print("Generated Password Hash:")
    print(f"  PASSWORD_HASH={password_hash}")
    print()

    # Show complete config
    print("=" * 60)
    print("Add these lines to your .env file:")
    print("=" * 60)
    print()
    print("AUTH_ENABLED=true")
    print(f"JWT_SECRET={jwt_secret}")
    print(f"PASSWORD_HASH={password_hash}")
    print("TOKEN_EXPIRY_HOURS=168  # 1 week")
    print()
    print("=" * 60)
    print("Setup complete! You can now start the server.")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nCancelled.")
        sys.exit(1)
