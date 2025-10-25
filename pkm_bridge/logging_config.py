"""Logging configuration with emoji indicators."""

import logging


class EmojiFormatter(logging.Formatter):
    """Custom formatter that adds emoji indicators to log levels."""

    EMOJI_MAP = {
        logging.DEBUG: '🔍',
        logging.INFO: 'ℹ️ ',
        logging.WARNING: '⚠️ ',
        logging.ERROR: '❌',
        logging.CRITICAL: '🔥'
    }

    def format(self, record):
        """Format log record with emoji indicator."""
        emoji = self.EMOJI_MAP.get(record.levelno, '')
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
    handler.setFormatter(EmojiFormatter(
        fmt='%(asctime)s %(emoji)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))

    logger = logging.getLogger("pkm_bridge")
    logger.setLevel(getattr(logging, log_level, logging.INFO))
    logger.addHandler(handler)
    logger.propagate = False  # Don't propagate to root logger

    return logger
