"""Tests for logging setup."""

from __future__ import annotations

import logging
from unittest.mock import patch


def test_setup_logging_configures_handler() -> None:
    from src.common.log_buffer import BufferLogHandler
    from src.common.logging import setup_logging

    root = logging.getLogger()
    original_handlers = root.handlers[:]

    try:
        root.handlers.clear()

        with patch("src.common.logging.settings") as s:
            s.LOG_LEVEL = "DEBUG"
            setup_logging()

        # A stdout stream handler plus the in-memory buffer handler (/logs viewer).
        assert len(root.handlers) == 2
        assert any(isinstance(h, BufferLogHandler) for h in root.handlers)
        assert any(
            isinstance(h, logging.StreamHandler) and not isinstance(h, BufferLogHandler)
            for h in root.handlers
        )
        assert root.level == logging.DEBUG
    finally:
        root.handlers = original_handlers


def test_setup_logging_already_configured() -> None:
    from src.common.log_buffer import BufferLogHandler
    from src.common.logging import setup_logging

    root = logging.getLogger()
    original_handlers = root.handlers[:]

    try:
        root.handlers = [logging.StreamHandler()]

        with patch("src.common.logging.settings") as s:
            s.LOG_LEVEL = "WARNING"
            setup_logging()

        # Existing stream handler kept; buffer handler attached for the viewer.
        assert root.level == logging.WARNING
        assert any(isinstance(h, BufferLogHandler) for h in root.handlers)

        # Idempotent: a second call doesn't pile on another buffer handler.
        count = len(root.handlers)
        with patch("src.common.logging.settings") as s:
            s.LOG_LEVEL = "WARNING"
            setup_logging()
        assert len(root.handlers) == count
    finally:
        root.handlers = original_handlers
