"""Google Gmail API client for read-only email access."""

import base64
import logging
import os
import re
from typing import Any, Dict, List, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Get logger
logger = logging.getLogger(__name__)


class GoogleGmailClient:
    """Read-only client for Google Gmail API."""

    def __init__(self, access_token: str, refresh_token: Optional[str] = None):
        """Initialize Google Gmail client.

        Args:
            access_token: OAuth access token
            refresh_token: Optional OAuth refresh token
        """
        self.credentials = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.getenv('GOOGLE_CLIENT_ID'),
            client_secret=os.getenv('GOOGLE_CLIENT_SECRET')
        )

        self.service = build('gmail', 'v1', credentials=self.credentials)

    def list_labels(self) -> List[Dict[str, Any]]:
        """List all Gmail labels.

        Returns:
            List of label dictionaries with id, name, type, etc.
        """
        try:
            results = self.service.users().labels().list(userId='me').execute()
            return results.get('labels', [])
        except HttpError as e:
            raise Exception(f"Failed to list labels: {e}")

    def list_messages(
        self,
        query: Optional[str] = None,
        label_ids: Optional[List[str]] = None,
        max_results: int = 20,
        page_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Search/list messages.

        Args:
            query: Gmail search query (same syntax as Gmail search box)
            label_ids: Filter by label IDs (e.g., ['INBOX', 'UNREAD'])
            max_results: Maximum messages to return (capped at 50)
            page_token: Token for pagination

        Returns:
            Dict with 'messages' list and optional 'nextPageToken'
        """
        try:
            max_results = min(max_results, 50)
            params: Dict[str, Any] = {
                'userId': 'me',
                'maxResults': max_results,
            }
            if query:
                params['q'] = query
            if label_ids:
                params['labelIds'] = label_ids
            if page_token:
                params['pageToken'] = page_token

            results = self.service.users().messages().list(**params).execute()
            return {
                'messages': results.get('messages', []),
                'nextPageToken': results.get('nextPageToken'),
                'resultSizeEstimate': results.get('resultSizeEstimate', 0),
            }
        except HttpError as e:
            raise Exception(f"Failed to list messages: {e}")

    def get_message(self, message_id: str, format: str = 'full') -> Dict[str, Any]:
        """Get a single message.

        Args:
            message_id: Message ID
            format: Response format ('full', 'metadata', 'minimal')

        Returns:
            Message dictionary
        """
        try:
            return self.service.users().messages().get(
                userId='me', id=message_id, format=format
            ).execute()
        except HttpError as e:
            raise Exception(f"Failed to get message {message_id}: {e}")

    def list_threads(
        self,
        query: Optional[str] = None,
        max_results: int = 20,
        page_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List threads.

        Args:
            query: Gmail search query
            max_results: Maximum threads to return (capped at 50)
            page_token: Token for pagination

        Returns:
            Dict with 'threads' list and optional 'nextPageToken'
        """
        try:
            max_results = min(max_results, 50)
            params: Dict[str, Any] = {
                'userId': 'me',
                'maxResults': max_results,
            }
            if query:
                params['q'] = query
            if page_token:
                params['pageToken'] = page_token

            results = self.service.users().threads().list(**params).execute()
            return {
                'threads': results.get('threads', []),
                'nextPageToken': results.get('nextPageToken'),
                'resultSizeEstimate': results.get('resultSizeEstimate', 0),
            }
        except HttpError as e:
            raise Exception(f"Failed to list threads: {e}")

    def get_thread(self, thread_id: str) -> Dict[str, Any]:
        """Get a full thread with all messages.

        Args:
            thread_id: Thread ID

        Returns:
            Thread dictionary with messages
        """
        try:
            return self.service.users().threads().get(
                userId='me', id=thread_id, format='full'
            ).execute()
        except HttpError as e:
            raise Exception(f"Failed to get thread {thread_id}: {e}")

    @staticmethod
    def extract_header(headers: List[Dict[str, str]], name: str) -> str:
        """Extract a header value by name.

        Args:
            headers: List of header dicts with 'name' and 'value'
            name: Header name (case-insensitive)

        Returns:
            Header value or empty string
        """
        name_lower = name.lower()
        for header in headers:
            if header.get('name', '').lower() == name_lower:
                return header.get('value', '')
        return ''

    @staticmethod
    def decode_body(payload: Dict[str, Any]) -> str:
        """Decode message body from MIME payload.

        Handles multipart messages, prefers text/plain, falls back to
        stripping HTML tags from text/html.

        Args:
            payload: Message payload from Gmail API

        Returns:
            Decoded body text
        """
        mime_type = payload.get('mimeType', '')

        # Direct body (non-multipart)
        if 'body' in payload and payload['body'].get('data'):
            decoded = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='replace')
            if 'html' in mime_type:
                return GoogleGmailClient._strip_html(decoded)
            return decoded

        # Multipart: look through parts
        parts = payload.get('parts', [])
        text_plain = ''
        text_html = ''

        for part in parts:
            part_mime = part.get('mimeType', '')

            if part_mime == 'text/plain' and part.get('body', {}).get('data'):
                text_plain = base64.urlsafe_b64decode(
                    part['body']['data']
                ).decode('utf-8', errors='replace')
            elif part_mime == 'text/html' and part.get('body', {}).get('data'):
                text_html = base64.urlsafe_b64decode(
                    part['body']['data']
                ).decode('utf-8', errors='replace')
            elif 'multipart' in part_mime:
                # Recurse into nested multipart
                nested = GoogleGmailClient.decode_body(part)
                if nested:
                    return nested

        if text_plain:
            return text_plain
        if text_html:
            return GoogleGmailClient._strip_html(text_html)

        return ''

    @staticmethod
    def _strip_html(html: str) -> str:
        """Strip HTML tags and decode entities for plain-text fallback."""
        # Remove style/script blocks
        text = re.sub(r'<(style|script)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
        # Replace <br> and </p> with newlines
        text = re.sub(r'<br\s*/?>|</p>', '\n', text, flags=re.IGNORECASE)
        # Strip remaining tags
        text = re.sub(r'<[^>]+>', '', text)
        # Collapse whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def format_message_summary(
        self,
        message: Dict[str, Any],
        include_body: bool = True,
        max_body_chars: int = 2000,
    ) -> str:
        """Format a message into a human-readable summary.

        Args:
            message: Full message dict from Gmail API
            include_body: Whether to include the message body
            max_body_chars: Maximum characters for body text

        Returns:
            Formatted message summary
        """
        payload = message.get('payload', {})
        headers = payload.get('headers', [])

        from_addr = self.extract_header(headers, 'From')
        to_addr = self.extract_header(headers, 'To')
        subject = self.extract_header(headers, 'Subject') or '(no subject)'
        date = self.extract_header(headers, 'Date')
        message_id = message.get('id', '')

        lines = [
            f"Subject: {subject}",
            f"From: {from_addr}",
            f"To: {to_addr}",
            f"Date: {date}",
            f"ID: {message_id}",
        ]

        if include_body:
            body = self.decode_body(payload)
            if body:
                if len(body) > max_body_chars:
                    body = body[:max_body_chars] + '...[truncated]'
                lines.append(f"\n{body}")

        return '\n'.join(lines)
