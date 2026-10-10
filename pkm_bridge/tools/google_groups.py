"""Google Groups membership tool (Admin SDK Directory API).

Works only on groups in the user's Workspace domains; consumer
@googlegroups.com groups have no membership API. Rides on the Gmail OAuth
grant (see GMAIL_SCOPES), so no separate connection is needed.
"""

import logging
from typing import Any, Dict, Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from pkm_bridge.google_oauth import (
    GROUP_MEMBER_SCOPE,
    GoogleOAuth,
    google_credentials,
    missing_scopes,
)
from pkm_bridge.tools.base import BaseTool
from pkm_bridge.tools.google_gmail import RECONNECT_HINT, load_gmail_token

ROLES = ["MEMBER", "MANAGER", "OWNER"]


class GoogleGroupsTool(BaseTool):
    """Add members to Google Workspace groups (e.g. a beta-tester group)."""

    def __init__(self, logger: logging.Logger, oauth_handler: Optional[GoogleOAuth] = None):
        super().__init__(logger)
        self.oauth_handler = oauth_handler

    @property
    def name(self) -> str:
        return "google_groups"

    @property
    def description(self) -> str:
        return """Manage Google Workspace group membership. Actions:
- add_member: add an email address to a group (idempotent; reports if already a member)

Only groups on the user's own Workspace domain work, not @googlegroups.com groups."""

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add_member"]},
                "group": {"type": "string", "description": "Group email address"},
                "email": {"type": "string", "description": "Member email address"},
                "role": {"type": "string", "enum": ROLES, "description": "Default MEMBER"},
            },
            "required": ["action", "group", "email"],
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        action = params.get("action")
        if action != "add_member":
            return f"Error: Unknown action: {action}"
        group = (params.get("group") or "").strip()
        email = (params.get("email") or "").strip()
        role = params.get("role") or "MEMBER"
        if not group or not email:
            return "Error: 'group' and 'email' are required"
        if role not in ROLES:
            return f"Error: role must be one of {', '.join(ROLES)}"

        token = load_gmail_token(self.oauth_handler, self.logger)
        if not token:
            return "Google not connected. Please connect via /auth/google-gmail/authorize"
        if missing_scopes(token.scope, [GROUP_MEMBER_SCOPE]):
            return f"The Google connection lacks group-membership access. {RECONNECT_HINT}"

        service = build(
            "admin",
            "directory_v1",
            credentials=google_credentials(token.access_token, token.refresh_token),
        )
        return self.add_member(service, group, email, role)

    def add_member(self, service: Any, group: str, email: str, role: str) -> str:
        try:
            service.members().insert(groupKey=group, body={"email": email, "role": role}).execute()
        except HttpError as e:
            status = e.resp.status
            if status == 409:
                return f"{email} is already a member of {group}."
            self.logger.error(f"Adding {email} to {group} failed: {e}")
            if status in (403, 404):
                return (
                    f"Could not add {email} to {group} (HTTP {status}). The group must be on "
                    "your Workspace domain and you must be an admin of it; "
                    "@googlegroups.com groups can't be managed through the API."
                )
            return f"Could not add {email} to {group}: HTTP {status}: {e}"
        return f"Added {email} to {group} as {role}."
