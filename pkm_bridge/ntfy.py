"""ntfy push-notification client.

Thin wrapper over the ntfy publish API. Credentials and server come from the
environment (NTFY_SERVER / NTFY_TOPIC / NTFY_USER+NTFY_PASS or NTFY_TOKEN), so
callers never handle secrets and no credential ever appears in a prompt or an
LLM tool argument.

Publishing uses ntfy's JSON format (POST to the server root) rather than the
header-based format, so titles, bodies, and tags carry UTF-8 unmangled.
"""

import base64
import json
import logging
import os
import re

import requests

logger = logging.getLogger("pkm_bridge.ntfy")

DEFAULT_SERVER = "https://ntfy.sh"
DEFAULT_TIMEOUT = 10

# ntfy priorities are 1-5 on the wire; callers use the names.
PRIORITIES = {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5}

MAX_TITLE_LEN = 250

# ntfy topics are a single URL path segment: letters, digits, - and _
TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class NtfyError(Exception):
    """Raised when a notification could not be published."""


def _auth_header() -> str | None:
    """Build the Authorization header value from env, or None if unauthenticated."""
    user = os.getenv("NTFY_USER", "").strip()
    password = os.getenv("NTFY_PASS", "")
    if user:
        encoded = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
        return f"Basic {encoded}"

    token = os.getenv("NTFY_TOKEN", "").strip()
    if token:
        return f"Bearer {token}"

    return None


def send(
    message: str,
    title: str | None = None,
    topic: str | None = None,
    priority: str = "default",
    tags: list[str] | None = None,
    click_url: str | None = None,
    markdown: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """Publish a notification to ntfy.

    Args:
        message: Notification body. May be multiline.
        title: Notification title. Collapsed to a single line.
        topic: Topic to publish to. Defaults to $NTFY_TOPIC.
        priority: One of min, low, default, high, urgent.
        tags: Emoji shortcodes or labels (e.g. ["bell"]).
        click_url: URL opened when the notification is tapped.
        markdown: Render the body as Markdown in supporting clients.
        timeout: HTTP timeout in seconds.

    Returns:
        The parsed JSON response from ntfy (includes the message id).

    Raises:
        NtfyError: on missing topic, network failure, or non-2xx response.
    """
    topic = (topic or os.getenv("NTFY_TOPIC", "")).strip()
    if not topic:
        raise NtfyError("No ntfy topic configured. Set NTFY_TOPIC or pass topic=.")
    if not TOPIC_RE.match(topic):
        raise NtfyError(
            f"Invalid topic {topic!r}. Topics are a single path segment of "
            "letters, digits, hyphens, or underscores."
        )

    if priority not in PRIORITIES:
        raise NtfyError(f"Invalid priority {priority!r}. Expected one of {tuple(PRIORITIES)}.")

    if not message.strip():
        raise NtfyError("Refusing to send an empty notification.")

    server = os.getenv("NTFY_SERVER", "").strip() or DEFAULT_SERVER

    payload: dict = {
        "topic": topic,
        "message": message,
        "priority": PRIORITIES[priority],
    }
    if title:
        payload["title"] = " ".join(title.split())[:MAX_TITLE_LEN]
    if tags:
        payload["tags"] = tags
    if click_url:
        payload["click"] = click_url
    if markdown:
        payload["markdown"] = True

    headers = {"Content-Type": "application/json; charset=utf-8"}
    auth = _auth_header()
    if auth:
        headers["Authorization"] = auth

    try:
        response = requests.post(
            server.rstrip("/") + "/",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            timeout=timeout,
        )
    except requests.RequestException as e:
        raise NtfyError(f"Could not reach ntfy at {server}: {type(e).__name__}: {e}") from e

    if not response.ok:
        # Never echo headers back — they carry the Authorization value.
        raise NtfyError(f"ntfy returned {response.status_code}: {response.text[:200]}")

    logger.info(f"Sent ntfy notification to {topic} ({len(message)} chars)")

    try:
        return response.json()
    except ValueError:
        return {"status": response.status_code}
