"""Push notification tool (ntfy)."""

from typing import Any, Dict

from pkm_bridge import ntfy

from .base import BaseTool


class NotifyTool(BaseTool):
    """Send a push notification to the user's phone via ntfy."""

    @property
    def name(self) -> str:
        return "notify"

    @property
    def description(self) -> str:
        return """Send a push notification to the user's phone.

Use this to surface something the user asked to be alerted about — a scheduled
task finding new items, a long job finishing, an approaching deadline. Do not
use it for ordinary conversational replies: the user is already reading those.
Send ONE notification per event, with all items in the body, rather than one
per item.

Credentials and the default topic come from the server environment, so no
secret is needed here."""

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Notification body. Multiline is fine — one line per item.",
                },
                "title": {
                    "type": "string",
                    "description": "Short title shown on the lock screen",
                },
                "priority": {
                    "type": "string",
                    "enum": list(ntfy.PRIORITIES),
                    "description": "Notification priority (default: 'default')",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Emoji shortcodes shown as icons, e.g. ['bell'] or ['warning']",
                },
                "click_url": {
                    "type": "string",
                    "description": "URL opened when the notification is tapped",
                },
                "topic": {
                    "type": "string",
                    "description": (
                        "ntfy topic to publish to. Defaults to the server's configured "
                        "topic; set it explicitly to route to a different one."
                    ),
                },
                "markdown": {
                    "type": "boolean",
                    "description": "Render the body as Markdown in supporting clients",
                },
            },
            "required": ["message"],
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        """Send the notification.

        Args:
            params: Dict with 'message' and optional title/priority/tags/click_url/topic/markdown
            context: Unused

        Returns:
            Success message or error
        """
        message = params.get("message", "")

        try:
            result = ntfy.send(
                message,
                title=params.get("title"),
                topic=params.get("topic"),
                priority=params.get("priority") or "default",
                tags=params.get("tags"),
                click_url=params.get("click_url"),
                markdown=bool(params.get("markdown")),
            )
        except ntfy.NtfyError as e:
            self.logger.warning(f"Notification not sent: {e}")
            return f"❌ Notification NOT sent: {e}"

        summary = params.get("title") or message
        self.logger.info(f"Sent notification: {summary[:80]}")
        msg_id = result.get("id", "")
        return f"✅ Notification sent{f' (id {msg_id})' if msg_id else ''}: {summary[:80]}"
