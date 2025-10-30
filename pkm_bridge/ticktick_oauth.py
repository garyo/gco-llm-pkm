"""TickTick OAuth2 authentication handler."""

import os
import secrets
from datetime import datetime, timedelta
from typing import Dict, Optional
from urllib.parse import urlencode
import requests


class TickTickOAuth:
    """Handle TickTick OAuth2 authentication flow."""

    # TickTick OAuth endpoints
    AUTHORIZE_URL = "https://ticktick.com/oauth/authorize"
    TOKEN_URL = "https://ticktick.com/oauth/token"

    def __init__(self):
        """Initialize OAuth handler with credentials from environment."""
        self.client_id = os.getenv('TICKTICK_CLIENT_ID')
        self.client_secret = os.getenv('TICKTICK_CLIENT_SECRET')
        self.redirect_uri = os.getenv('TICKTICK_REDIRECT_URI')

        if not all([self.client_id, self.client_secret, self.redirect_uri]):
            raise ValueError(
                "TickTick OAuth credentials not configured. "
                "Set TICKTICK_CLIENT_ID, TICKTICK_CLIENT_SECRET, and TICKTICK_REDIRECT_URI"
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
            'scope': 'tasks:read tasks:write',
            'response_type': 'code',
            'redirect_uri': self.redirect_uri,
            'state': state
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
            - expires_in: Seconds until expiration
            - token_type: Token type (usually "Bearer")

        Raises:
            requests.HTTPError: If token exchange fails
        """
        data = {
            'code': code,
            'grant_type': 'authorization_code',
            'redirect_uri': self.redirect_uri
        }

        # Send credentials via HTTP Basic Auth
        response = requests.post(
            self.TOKEN_URL,
            data=data,
            auth=(self.client_id, self.client_secret)
        )
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
            'scope': token_data.get('scope', 'tasks:read tasks:write')
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
            'grant_type': 'refresh_token'
        }

        # Send credentials via HTTP Basic Auth
        response = requests.post(
            self.TOKEN_URL,
            data=data,
            auth=(self.client_id, self.client_secret)
        )
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
            'scope': token_data.get('scope', 'tasks:read tasks:write')
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
