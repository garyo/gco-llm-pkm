"""Google Gmail integration tool for Claude — read-only access."""

import logging
from typing import Any, Dict, Optional

from pkm_bridge.database import get_db
from pkm_bridge.db_repository import OAuthRepository
from pkm_bridge.google_gmail_client import GoogleGmailClient
from pkm_bridge.google_oauth import GoogleOAuth
from pkm_bridge.tools.base import BaseTool


class GoogleGmailTool(BaseTool):
    """Read-only tool for querying Gmail messages and threads."""

    def __init__(self, logger: logging.Logger, oauth_handler: Optional[GoogleOAuth] = None):
        super().__init__(logger)
        self.oauth_handler = oauth_handler

    @property
    def name(self) -> str:
        return "google_gmail"

    @property
    def description(self) -> str:
        return """Search and read Gmail messages (read-only). Use this to:
- Search emails by keyword, sender, date, label, etc. (uses Gmail search syntax)
- Read a specific email by its message ID
- List email threads and read full thread conversations
- List Gmail labels

Gmail search syntax examples:
  from:alice@example.com
  subject:meeting after:2025/01/01
  is:unread label:important
  has:attachment filename:pdf

Connection status: Check /auth/google-gmail/status. If not connected, user needs to visit /auth/google-gmail/authorize."""

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "search",
                        "get_message",
                        "list_threads",
                        "get_thread",
                        "list_labels",
                    ],
                    "description": "Action to perform",
                },
                "query": {
                    "type": "string",
                    "description": "Gmail search query (for search, list_threads)",
                },
                "message_id": {
                    "type": "string",
                    "description": "Message ID (for get_message)",
                },
                "thread_id": {
                    "type": "string",
                    "description": "Thread ID (for get_thread)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum results to return (default 20, max 50)",
                },
                "label_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by label IDs, e.g. ['INBOX', 'UNREAD'] (for search)",
                },
            },
            "required": ["action"],
        }

    def get_client(self) -> Optional[GoogleGmailClient]:
        """Get authenticated Gmail client, refreshing token if needed."""
        if not self.oauth_handler:
            return None

        try:
            db = get_db()
            token = OAuthRepository.get_token(db, 'google_gmail')

            if not token:
                return None

            if OAuthRepository.is_token_expired(token):
                self.logger.info("Gmail token expired, refreshing...")
                try:
                    new_token_data = self.oauth_handler.refresh_token(token.refresh_token)
                    OAuthRepository.save_token(
                        db=db,
                        service='google_gmail',
                        access_token=new_token_data['access_token'],
                        refresh_token=new_token_data.get('refresh_token'),
                        expires_at=new_token_data['expires_at'],
                        scope=new_token_data.get('scope'),
                    )
                    token = OAuthRepository.get_token(db, 'google_gmail')
                    self.logger.info("Gmail token refreshed successfully")
                except Exception as e:
                    self.logger.error(f"Failed to refresh Gmail token: {e}")
                    return None
                finally:
                    db.close()
            else:
                db.close()

            return GoogleGmailClient(token.access_token, token.refresh_token)

        except Exception as e:
            self.logger.error(f"Error getting Gmail client: {e}")
            return None

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        action = params.get('action')
        if not action:
            return "Error: 'action' parameter is required"

        client = self.get_client()
        if not client:
            return "Gmail not connected. Please connect via /auth/google-gmail/authorize"

        try:
            max_results = min(params.get('max_results', 20), 50)

            if action == "search":
                query = params.get('query', '')
                label_ids = params.get('label_ids')
                results = client.list_messages(
                    query=query, label_ids=label_ids, max_results=max_results
                )
                messages = results.get('messages', [])

                if not messages:
                    return f"No messages found{f' for query: {query}' if query else ''}."

                # Fetch full details for each message
                summaries = []
                for msg_stub in messages:
                    msg = client.get_message(msg_stub['id'])
                    summaries.append(client.format_message_summary(msg, include_body=False))

                header = f"Messages ({len(summaries)})"
                if query:
                    header += f" matching '{query}'"
                if results.get('nextPageToken'):
                    header += " (more available)"

                return header + ":\n\n" + "\n---\n".join(summaries)

            elif action == "get_message":
                message_id = params.get('message_id')
                if not message_id:
                    return "Error: message_id is required for get_message"

                msg = client.get_message(message_id)
                return client.format_message_summary(msg, include_body=True)

            elif action == "list_threads":
                query = params.get('query', '')
                results = client.list_threads(query=query, max_results=max_results)
                threads = results.get('threads', [])

                if not threads:
                    return f"No threads found{f' for query: {query}' if query else ''}."

                # Fetch snippet for each thread
                lines = []
                for t in threads:
                    thread = client.get_thread(t['id'])
                    msgs = thread.get('messages', [])
                    if msgs:
                        first = msgs[0]
                        headers = first.get('payload', {}).get('headers', [])
                        subject = client.extract_header(headers, 'Subject') or '(no subject)'
                        from_addr = client.extract_header(headers, 'From')
                        lines.append(
                            f"Thread ID: {t['id']} | {len(msgs)} message(s)\n"
                            f"  Subject: {subject}\n"
                            f"  From: {from_addr}"
                        )

                header = f"Threads ({len(threads)})"
                if query:
                    header += f" matching '{query}'"
                if results.get('nextPageToken'):
                    header += " (more available)"

                return header + ":\n\n" + "\n\n".join(lines)

            elif action == "get_thread":
                thread_id = params.get('thread_id')
                if not thread_id:
                    return "Error: thread_id is required for get_thread"

                thread = client.get_thread(thread_id)
                msgs = thread.get('messages', [])

                if not msgs:
                    return f"Thread {thread_id} has no messages."

                summaries = [
                    client.format_message_summary(m, include_body=True) for m in msgs
                ]
                return f"Thread ({len(msgs)} messages):\n\n" + "\n---\n".join(summaries)

            elif action == "list_labels":
                labels = client.list_labels()
                if not labels:
                    return "No labels found."

                lines = [f"Gmail labels ({len(labels)}):"]
                for label in labels:
                    name = label.get('name', 'Unknown')
                    label_id = label.get('id', '')
                    label_type = label.get('type', '')
                    lines.append(f"  {name} (ID: {label_id}, type: {label_type})")

                return '\n'.join(lines)

            else:
                return f"Error: Unknown action: {action}"

        except Exception as e:
            self.logger.error(f"Gmail error: {e}")
            return f"Error: {str(e)}"
