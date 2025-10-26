"""Authentication module for PKM Bridge Server.

Provides JWT-based token authentication with password verification.
"""

import os
import jwt
import bcrypt
from datetime import datetime, timedelta
from functools import wraps
from typing import Optional, Dict, Any
from flask import request, jsonify


class AuthManager:
    """Manages JWT-based authentication with bcrypt password hashing."""

    def __init__(self, secret_key: str, password_hash: str, token_expiry_hours: int = 168, logger=None):
        """Initialize auth manager.

        Args:
            secret_key: Secret key for JWT signing
            password_hash: bcrypt hash of the password
            token_expiry_hours: Hours until token expires (default: 1 week)
            logger: Optional logger for auth events
        """
        self.secret_key = secret_key
        self.password_hash = password_hash
        self.token_expiry_hours = token_expiry_hours
        self.logger = logger

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password using bcrypt.

        Args:
            password: Plain text password

        Returns:
            bcrypt hash as string
        """
        salt = bcrypt.gensalt(rounds=12)  # 12 rounds is a good balance of security/performance
        return bcrypt.hashpw(password.encode(), salt).decode('utf-8')

    def verify_password(self, password: str) -> bool:
        """Verify a password against the stored bcrypt hash.

        Args:
            password: Plain text password to verify

        Returns:
            True if password matches
        """
        try:
            return bcrypt.checkpw(password.encode(), self.password_hash.encode())
        except (ValueError, AttributeError):
            # Invalid hash format
            if self.logger:
                self.logger.error("Invalid password hash format in configuration")
            return False

    def generate_token(self, username: str = "user") -> str:
        """Generate a JWT token.

        Args:
            username: Username to embed in token

        Returns:
            JWT token string
        """
        payload = {
            "username": username,
            "exp": datetime.utcnow() + timedelta(hours=self.token_expiry_hours),
            "iat": datetime.utcnow()
        }
        token = jwt.encode(payload, self.secret_key, algorithm="HS256")

        if self.logger:
            expiry = datetime.utcnow() + timedelta(hours=self.token_expiry_hours)
            self.logger.info(f"Generated token for '{username}', expires at {expiry.isoformat()}Z")

        return token

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify and decode a JWT token.

        Args:
            token: JWT token string

        Returns:
            Decoded token payload if valid, None otherwise
        """
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=["HS256"])
            if self.logger:
                self.logger.debug(f"Token verified for user '{payload.get('username', 'unknown')}'")
            return payload
        except jwt.ExpiredSignatureError:
            if self.logger:
                self.logger.warning("Token verification failed: expired token")
            return None
        except jwt.InvalidTokenError as e:
            if self.logger:
                self.logger.warning(f"Token verification failed: invalid token ({str(e)})")
            return None

    def require_auth(self, f):
        """Decorator to require authentication on a Flask route.

        Usage:
            @app.route('/protected')
            @auth_manager.require_auth
            def protected_route():
                return "Protected content"
        """
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Get token from Authorization header
            auth_header = request.headers.get('Authorization', '')

            if not auth_header.startswith('Bearer '):
                return jsonify({"error": "Missing or invalid authorization header"}), 401

            token = auth_header[7:]  # Remove "Bearer " prefix
            payload = self.verify_token(token)

            if not payload:
                return jsonify({"error": "Invalid or expired token"}), 401

            # Add user info to request context
            request.user = payload

            return f(*args, **kwargs)

        return decorated_function
