"""Diagnostic log, written to %APPDATA%\\ClaudeUsageWidget\\widget.log.

Every poll, refresh attempt, and failure is recorded with timestamps so a
single paste of the file reconstructs what the widget actually did — no
guessing from a one-line status message. Tokens never appear in it: values are
reduced to their length and last four characters before logging.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys

from .config import config_dir

LOG_NAME = "widget.log"
_configured = False


def log_path():
    return config_dir() / LOG_NAME


def get_logger() -> logging.Logger:
    """The shared logger, configured on first use."""
    global _configured
    logger = logging.getLogger("claude_usage_widget")
    if _configured:
        return logger

    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    try:
        config_dir().mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_path(), maxBytes=512_000, backupCount=2, encoding="utf-8"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    except OSError:
        # An unwritable profile should not take the widget down; fall through
        # to stderr so `python -m` runs still show something.
        pass

    if not getattr(sys, "frozen", False):
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        stream.setLevel(logging.INFO)
        logger.addHandler(stream)

    _configured = True
    logger.info("=== logger started; log file: %s ===", log_path())
    return logger


def redact(token: str | None) -> str:
    """Describe a token without revealing it."""
    if not token:
        return "<none>"
    return f"<len={len(token)} ...{token[-4:]}>"
