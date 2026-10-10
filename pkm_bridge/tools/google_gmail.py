"""Google Gmail integration tool for Claude: read mail and create drafts."""

import logging
from typing import Any, Dict, Optional

from pkm_bridge.database import OAuthToken, get_db
from pkm_bridge.db_repository import OAuthRepository
from pkm_bridge.google_gmail_client import GoogleGmailClient
from pkm_bridge.google_oauth import GMAIL_SCOPE_COMPOSE, GoogleOAuth, missing_scopes
from pkm_bridge.tools.base import BaseTool

GMAIL_SERVICE = "google_gmail"
RECONNECT_HINT = "Reconnect via /auth/google-gmail/authorize to grant it."


def load_gmail_token(
    oauth_handler: Optional[GoogleOAuth], logger: logging.Logger
) -> Optional[OAuthToken]:
    """The Google token granted through the Gmail consent, refreshed if expired."""
    if not oauth_handler:
        return None
    try:
        db = get_db()
        try:
            return OAuthRepository.refresh_if_expired(db, GMAIL_SERVICE, oauth_handler, logger)
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Error loading Google token: {e}")
        return None


def as_address_list(value: Any) -> list[str]:
    """Recipients given as a list or a comma-separated string."""
    if not value:
        return []
    items = value if isinstance(value, list) else str(value).split(",")
    return [a.strip() for a in items if a and a.strip()]


class GoogleGmailTool(BaseTool):
    """Tool for querying Gmail messages and threads, and creating drafts."""

    def __init__(self, logger: logging.Logger, oauth_handler: Optional[GoogleOAuth] = None):
        super().__init__(logger)
        self.oauth_handler = oauth_handler

    @property
    def name(self) -> str:
        return "google_gmail"

    @property
    def description(self) -> str:
        return """Search and read Gmail messages, and create drafts. Use this to:
- Search emails by keyword, sender, date, label, etc. (uses Gmail search syntax)
- Read a specific email by its message ID
- List email threads and read full thread conversations
- List Gmail labels
- Create a draft (never sends; the user reviews and sends it). Links in the body are
  stored exactly as given. Prefer html_body; a plain-text part is derived from it.

Gmail search syntax examples:
  from:alice@example.com
  subject:meeting after:2025/01/01
  is:unread label:important
  has:attachment filename:pdf

Connection status: Check /auth/google-gmail/status. If not connected, user needs to visit
/auth/google-gmail/authorize."""

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
                        "create_draft",
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
                "to": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Recipient addresses (for create_draft)",
                },
                "cc": {"type": "array", "items": {"type": "string"}},
                "bcc": {"type": "array", "items": {"type": "string"}},
                "subject": {"type": "string", "description": "Subject (for create_draft)"},
                "html_body": {"type": "string", "description": "HTML body (for create_draft)"},
                "text_body": {
                    "type": "string",
                    "description": "Plain-text body; derived from html_body when omitted",
                },
            },
            "required": ["action"],
        }

    def get_client(self, required_scopes: list[str] | None = None) -> GoogleGmailClient | str:
        """An authenticated Gmail client, or a message saying why there isn't one."""
        token = load_gmail_token(self.oauth_handler, self.logger)
        if not token:
            return "Gmail not connected. Please connect via /auth/google-gmail/authorize"
        missing = missing_scopes(token.scope, required_scopes or [])
        if missing:
            return f"The Gmail connection lacks scope {', '.join(missing)}. {RECONNECT_HINT}"
        try:
            return GoogleGmailClient(token.access_token, token.refresh_token)
        except Exception as e:
            self.logger.error(f"Error building Gmail client: {e}")
            return f"Gmail client error: {e}"

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        action = params.get("action")
        if not action:
            return "Error: 'action' parameter is required"

        client = self.get_client([GMAIL_SCOPE_COMPOSE] if action == "create_draft" else None)
        if isinstance(client, str):
            return client

        try:
            max_results = min(params.get("max_results", 20), 50)

            if action == "search":
                query = params.get("query", "")
                label_ids = params.get("label_ids")
                results = client.list_messages(
                    query=query, label_ids=label_ids, max_results=max_results
                )
                messages = results.get("messages", [])

                if not messages:
                    return f"No messages found{f' for query: {query}' if query else ''}."

                # Fetch full details for each message
                summaries = []
                for msg_stub in messages:
                    msg = client.get_message(msg_stub["id"])
                    summaries.append(client.format_message_summary(msg, include_body=False))

                header = f"Messages ({len(summaries)})"
                if query:
                    header += f" matching '{query}'"
                if results.get("nextPageToken"):
                    header += " (more available)"

                return header + ":\n\n" + "\n---\n".join(summaries)

            elif action == "get_message":
                message_id = params.get("message_id")
                if not message_id:
                    return "Error: message_id is required for get_message"

                msg = client.get_message(message_id)
                return client.format_message_summary(msg, include_body=True)

            elif action == "list_threads":
                query = params.get("query", "")
                results = client.list_threads(query=query, max_results=max_results)
                threads = results.get("threads", [])

                if not threads:
                    return f"No threads found{f' for query: {query}' if query else ''}."

                # Fetch snippet for each thread
                lines = []
                for t in threads:
                    thread = client.get_thread(t["id"])
                    msgs = thread.get("messages", [])
                    if msgs:
                        first = msgs[0]
                        headers = first.get("payload", {}).get("headers", [])
                        subject = client.extract_header(headers, "Subject") or "(no subject)"
                        from_addr = client.extract_header(headers, "From")
                        lines.append(
                            f"Thread ID: {t['id']} | {len(msgs)} message(s)\n"
                            f"  Subject: {subject}\n"
                            f"  From: {from_addr}"
                        )

                header = f"Threads ({len(threads)})"
                if query:
                    header += f" matching '{query}'"
                if results.get("nextPageToken"):
                    header += " (more available)"

                return header + ":\n\n" + "\n\n".join(lines)

            elif action == "get_thread":
                thread_id = params.get("thread_id")
                if not thread_id:
                    return "Error: thread_id is required for get_thread"

                thread = client.get_thread(thread_id)
                msgs = thread.get("messages", [])

                if not msgs:
                    return f"Thread {thread_id} has no messages."

                summaries = [client.format_message_summary(m, include_body=True) for m in msgs]
                return f"Thread ({len(msgs)} messages):\n\n" + "\n---\n".join(summaries)

            elif action == "list_labels":
                labels = client.list_labels()
                if not labels:
                    return "No labels found."

                lines = [f"Gmail labels ({len(labels)}):"]
                for label in labels:
                    name = label.get("name", "Unknown")
                    label_id = label.get("id", "")
                    label_type = label.get("type", "")
                    lines.append(f"  {name} (ID: {label_id}, type: {label_type})")

                return "\n".join(lines)

            elif action == "create_draft":
                return self.create_draft(client, params)

            else:
                return f"Error: Unknown action: {action}"

        except Exception as e:
            self.logger.error(f"Gmail error ({action}): {e}", exc_info=True)
            return (
                f"Gmail '{action}' failed: {type(e).__name__}: {e}. "
                "Check that Gmail is connected via OAuth."
            )

    @staticmethod
    def create_draft(client: GoogleGmailClient, params: Dict[str, Any]) -> str:
        to = as_address_list(params.get("to"))
        if not to:
            return "Error: 'to' is required for create_draft"
        if not params.get("html_body") and not params.get("text_body"):
            return "Error: html_body or text_body is required for create_draft"
        draft = client.create_draft(
            to=to,
            subject=params.get("subject", ""),
            html_body=params.get("html_body"),
            text_body=params.get("text_body"),
            cc=as_address_list(params.get("cc")),
            bcc=as_address_list(params.get("bcc")),
        )
        message_id = draft.get("message", {}).get("id", "")
        return (
            f"Draft created for {', '.join(to)} (draft ID: {draft.get('id')}). Not sent.\n"
            f"Open: https://mail.google.com/mail/#drafts?compose={message_id}"
        )
