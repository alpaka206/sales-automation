"""Logging setup. Call setup_logging() once at app start."""

from __future__ import annotations

import logging
import sys

from .config import settings


def _attach_buffer_handler(root: logging.Logger) -> None:
    """Attach the in-memory WARNING+ buffer handler once (for the /logs viewer)."""
    from .log_buffer import BufferLogHandler

    if any(isinstance(h, BufferLogHandler) for h in root.handlers):
        return
    buf = BufferLogHandler(level=logging.WARNING)
    root.addHandler(buf)


def setup_logging() -> None:
    """Configure stdlib logging with a simple readable format."""
    root = logging.getLogger()
    if root.handlers:
        # Already configured (uvicorn may have done it). Adjust level only, but
        # still ensure our buffer handler is attached so /logs captures events.
        root.setLevel(settings.LOG_LEVEL)
        _attach_buffer_handler(root)
        return

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(handler)
    root.setLevel(settings.LOG_LEVEL)
    _attach_buffer_handler(root)
