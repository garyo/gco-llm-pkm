"""Google OAuth2 authentication handler for Google APIs (Calendar, Gmail, etc.)."""

import os
import secrets
from datetime import datetime, timedelta
from typing import Dict, Optional
from urllib.parse import urlencode
import requests


class GoogleOAuth:
    """Handle Google OAuth2 authentication flow for Google APIs.

    Supports multiple Google APIs (Calendar, Gmail, etc.) by parameterizing
    scopes and redirect URI. Defaults to Calendar scopes for backward compatibility.
    """

    # Google OAuth endpoints
    AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"

    # Default scopes (Google Calendar - backward compatible)
    DEFAULT_SCOPES = [
        "https://www.googleapis.com/auth/calendar",  # Full calendar access
        "https://www.googleapis.com/auth/calendar.events"  # Events access
    ]

    def __init__(
        self,
        scopes: list[str] | None = None,
        redirect_uri_env: str = 'GOOGLE_REDIRECT_URI'
    ):
        """Initialize OAuth handler with credentials from environment.

        Args:
            scopes: OAuth scopes to request. Defaults to Calendar scopes.
            redirect_uri_env: Environment variable name for redirect URI.
        """
        self.client_id = os.getenv('GOOGLE_CLIENT_ID')
        self.client_secret = os.getenv('GOOGLE_CLIENT_SECRET')
        self.redirect_uri = os.getenv(redirect_uri_env)
        self.scopes = scopes or self.DEFAULT_SCOPES

        if not all([self.client_id, self.client_secret, self.redirect_uri]):
            raise ValueError(
                "Google OAuth credentials not configured. "
                "Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REDIRECT_URI"
            )

    def get_authorization_url(self, state: Optional[str] = None) -> Dict[str, str]:
        """Generate OAuth authorization URL for user to visit.

        Args:
            state: Optional state parameter for CSRF protection.
                   If not provided, a random one will be generated.

        Returns:
            Dict with 'url' and 'state' keys
        """
        if state is None:
            state = secrets.token_urlsafe(32)

        params = {
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'scope': ' '.join(self.scopes),  # Space-delimited scopes
            'access_type': 'offline',  # Request refresh token
            'state': state,
            'prompt': 'consent'  # Force consent to ensure refresh token
        }

        url = f"{self.AUTHORIZE_URL}?{urlencode(params)}"

        return {
            'url': url,
            'state': state
        }

    def exchange_code(self, code: str) -> Dict[str, any]:
        """Exchange authorization code for access token.

        Args:
            code: Authorization code from callback

        Returns:
            Dict with token information:
            - access_token: Access token
            - refresh_token: Refresh token
            - expires_at: Expiration datetime
            - token_type: Token type (usually "Bearer")
            - scope: Granted scopes

        Raises:
            requests.HTTPError: If token exchange fails
        """
        data = {
            'code': code,
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'redirect_uri': self.redirect_uri,
            'grant_type': 'authorization_code'
        }

        response = requests.post(self.TOKEN_URL, data=data)
        response.raise_for_status()

        token_data = response.json()

        # Calculate expiration time
        expires_in = token_data.get('expires_in', 3600)
        expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

        return {
            'access_token': token_data['access_token'],
            'refresh_token': token_data.get('refresh_token'),
            'expires_at': expires_at,
            'token_type': token_data.get('token_type', 'Bearer'),
            'scope': token_data.get('scope', ' '.join(self.scopes))
        }

    def refresh_token(self, refresh_token: str) -> Dict[str, any]:
        """Refresh an expired access token.

        Args:
            refresh_token: Refresh token from previous authorization

        Returns:
            Dict with new token information (same format as exchange_code)

        Raises:
            requests.HTTPError: If token refresh fails
        """
        data = {
            'refresh_token': refresh_token,
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'grant_type': 'refresh_token'
        }

        response = requests.post(self.TOKEN_URL, data=data)
        response.raise_for_status()

        token_data = response.json()

        # Calculate expiration time
        expires_in = token_data.get('expires_in', 3600)
        expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

        return {
            'access_token': token_data['access_token'],
            'refresh_token': token_data.get('refresh_token', refresh_token),  # Use old if not provided
            'expires_at': expires_at,
            'token_type': token_data.get('token_type', 'Bearer'),
            'scope': token_data.get('scope', ' '.join(self.scopes))
        }

    def is_token_expired(self, expires_at: datetime, buffer_seconds: int = 300) -> bool:
        """Check if a token is expired or about to expire.

        Args:
            expires_at: Token expiration datetime
            buffer_seconds: Seconds before expiration to consider token expired
                           (default 5 minutes)

        Returns:
            True if token is expired or will expire soon
        """
        if not expires_at:
            return False

        buffer = timedelta(seconds=buffer_seconds)
        return datetime.utcnow() >= (expires_at - buffer)
