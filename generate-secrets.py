#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "bcrypt>=4.0.0",
# ]
# ///
"""
Generate secrets for PKM Bridge deployment.

Usage:
    ./generate-secrets.py

Generates:
- JWT secret (for signing authentication tokens)
- Password hash (for login authentication)
"""

import secrets
import sys
import getpass
import bcrypt


def generate_jwt_secret():
    """Generate a random JWT secret."""
    return secrets.token_urlsafe(64)


def generate_password_hash(password):
    """Generate bcrypt hash for password."""
    salt = bcrypt.gensalt(12)  # 12 rounds
    hash_bytes = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hash_bytes.decode('utf-8')


def main():
    print("=" * 70)
    print("PKM Bridge Server - Secret Generation")
    print("=" * 70)
    print()

    # Generate JWT secret
    print("Generating JWT secret...")
    jwt_secret = generate_jwt_secret()
    print(f"✅ JWT_SECRET: {jwt_secret}")
    print()

    # Get password from user
    print("Enter a password for logging into the web interface.")
    print("This should be strong and unique. Store it in a password manager!")
    print()
    
    while True:
        password = getpass.getpass("Password: ")
        password_confirm = getpass.getpass("Confirm password: ")
        
        if password != password_confirm:
            print("❌ Passwords don't match. Try again.")
            print()
            continue
        
        if len(password) < 8:
            print("❌ Password too short (minimum 8 characters). Try again.")
            print()
            continue
        
        break

    # Generate password hash
    print()
    print("Generating password hash...")
    password_hash = generate_password_hash(password)
    print(f"✅ PASSWORD_HASH: {password_hash}")
    print()

    # Summary
    print("=" * 70)
    print("Add these to your .env file:")
    print("=" * 70)
    print()
    print(f"JWT_SECRET={jwt_secret}")
    print(f"PASSWORD_HASH={password_hash}")
    print()
    print("=" * 70)
    print("⚠️  IMPORTANT:")
    print("  - Store your plaintext password in a password manager")
    print("  - Never commit .env to version control")
    print("  - Set permissions: chmod 600 .env")
    print("=" * 70)


if __name__ == '__main__':
    main()
