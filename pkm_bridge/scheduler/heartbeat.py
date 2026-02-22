"""Heartbeat task — a special scheduled task that fires periodically.

The heartbeat prompt is loaded from .pkm/heartbeat.md so it can be edited
without restarting the server.
"""

import logging
from pathlib import Path
from typing import Optional

from ..database import get_db
from .repository import ScheduledTaskRepository

DEFAULT_HEARTBEAT_INTERVAL = "4h"

DEFAULT_HEARTBEAT_PROMPT = """\
You are running as an autonomous scheduled agent (heartbeat check).
Review any pending items, check today's calendar, and note anything important.
Keep your response concise — just a brief summary of what you found.
"""


def load_heartbeat_prompt(org_dir: str | Path) -> Optional[str]:
    """Load heartbeat prompt from .pkm/heartbeat.md if it exists."""
    heartbeat_file = Path(org_dir).expanduser() / ".pkm" / "heartbeat.md"
    if heartbeat_file.exists():
        content = heartbeat_file.read_text(encoding="utf-8").strip()
        if content:
            return content
    return None


def ensure_heartbeat_task(org_dir: str | Path, logger: logging.Logger) -> None:
    """Create the heartbeat task if it doesn't exist yet.

    Also writes a default .pkm/heartbeat.md if missing.
    """
    db = get_db()
    try:
        existing = ScheduledTaskRepository.get_heartbeat(db)
        if existing:
            logger.debug("Heartbeat task already exists")
            return

        # Create default heartbeat.md if missing
        pkm_dir = Path(org_dir).expanduser() / ".pkm"
        pkm_dir.mkdir(parents=True, exist_ok=True)
        heartbeat_file = pkm_dir / "heartbeat.md"
        if not heartbeat_file.exists():
            heartbeat_file.write_text(DEFAULT_HEARTBEAT_PROMPT, encoding="utf-8")
            logger.info(f"Created default heartbeat prompt: {heartbeat_file}")

        # Create the heartbeat task row
        ScheduledTaskRepository.create(
            db,
            name="heartbeat",
            description="Periodic wake-up check — prompt loaded from .pkm/heartbeat.md",
            prompt=DEFAULT_HEARTBEAT_PROMPT,
            schedule_type="interval",
            schedule_expr=DEFAULT_HEARTBEAT_INTERVAL,
            is_heartbeat=True,
            enabled=True,
            max_turns=5,
            max_input_tokens=100_000,
            max_output_tokens=5_000,
            created_by="system",
        )
        logger.info(f"Created heartbeat scheduled task (every {DEFAULT_HEARTBEAT_INTERVAL})")
    finally:
        db.close()
