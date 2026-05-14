"""Logging setup. Call setup_logging() once at app start."""

from __future__ import annotations

import logging
import sys

from .config import settings


def setup_logging() -> None:
    """Configure stdlib logging with a simple readable format."""
    root = logging.getLogger()
    if root.handlers:
        # Already configured (uvicorn may have done it). Adjust level only.
        root.setLevel(settings.LOG_LEVEL)
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
