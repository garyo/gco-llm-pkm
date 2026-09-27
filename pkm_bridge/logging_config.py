"""Logging configuration with emoji indicators."""

import logging
import re


class EmojiFormatter(logging.Formatter):
    """Custom formatter that adds emoji indicators to log levels."""

    EMOJI_MAP = {
        logging.DEBUG: "🔍",
        logging.INFO: "ℹ️ ",
        logging.WARNING: "⚠️ ",
        logging.ERROR: "❌",
        logging.CRITICAL: "🔥",
    }

    def format(self, record):
        """Format log record with emoji indicator."""
        emoji = self.EMOJI_MAP.get(record.levelno, "")
        record.emoji = emoji
        return super().format(record)


def setup_logging(log_level: str = "INFO") -> logging.Logger:
    """Configure logging with emoji formatter.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)

    Returns:
        Configured logger instance
    """
    handler = logging.StreamHandler()
    handler.setFormatter(
        EmojiFormatter(
            fmt="%(asctime)s %(emoji)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
    )

    logger = logging.getLogger("pkm_bridge")
    logger.setLevel(getattr(logging, log_level, logging.INFO))
    logger.addHandler(handler)
    logger.propagate = False  # Don't propagate to root logger

    return logger


class HealthCheckFilter(logging.Filter):
    """Drops access-log lines for successful GET /health probes.

    Docker probes both servers every 30 s; left in, those lines crowd real
    events out of the size-capped container logs. Failed probes still log.
    Matches werkzeug's and uvicorn's access-log messages (werkzeug may color
    the status code on a terminal).
    """

    _PROBE = re.compile(r'"GET /health HTTP/[\d.]+" (?:\x1b\[[\d;]*m)?200\b')

    def filter(self, record: logging.LogRecord) -> bool:
        return not self._PROBE.search(record.getMessage())


def quiet_health_checks(access_logger: str) -> None:
    """Filter successful /health probes out of the named access logger."""
    logging.getLogger(access_logger).addFilter(HealthCheckFilter())
