"""The user's configured timezone, for code that has no request to ask.

Chat requests carry the browser's timezone; scheduled tasks, MCP calls and the
scheduler itself fall back to the TIMEZONE setting read here.
"""

import os
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "America/New_York"
UTC = ZoneInfo("UTC")


def configured_timezone() -> ZoneInfo | None:
    """The TIMEZONE setting, or None if it isn't a valid zone name."""
    try:
        return ZoneInfo(os.getenv("TIMEZONE", DEFAULT_TIMEZONE))
    except Exception:
        return None


def configured_timezone_name() -> str:
    """The configured zone's IANA key, or 'UTC' if the setting is invalid."""
    tz = configured_timezone()
    return tz.key if tz else "UTC"


def resolve_timezone(name: str | None) -> ZoneInfo:
    """A zone by name, falling back to the configured zone, then UTC."""
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return configured_timezone() or UTC


def context_timezone(context: dict | None) -> str:
    """The timezone a tool call should use: the caller's, else the configured one."""
    return (context or {}).get("user_timezone") or configured_timezone_name()


def format_local(dt_utc: datetime, tz: tzinfo | None = None) -> str:
    """Render a naive-UTC timestamp as local wall time labelled with its zone."""
    local = dt_utc.replace(tzinfo=UTC).astimezone(tz or configured_timezone() or UTC)
    return local.strftime("%Y-%m-%d %H:%M %Z")
