"""Tests for logging setup."""

from __future__ import annotations

import logging
from unittest.mock import patch


def test_setup_logging_configures_handler() -> None:
    from src.common.logging import setup_logging

    root = logging.getLogger()
    original_handlers = root.handlers[:]

    try:
        root.handlers.clear()

        with patch("src.common.logging.settings") as s:
            s.LOG_LEVEL = "DEBUG"
            setup_logging()

        assert len(root.handlers) == 1
        assert root.level == logging.DEBUG
    finally:
        root.handlers = original_handlers


def test_setup_logging_already_configured() -> None:
    from src.common.logging import setup_logging

    root = logging.getLogger()
    original_handlers = root.handlers[:]

    try:
        if not root.handlers:
            root.addHandler(logging.StreamHandler())

        handler_count = len(root.handlers)

        with patch("src.common.logging.settings") as s:
            s.LOG_LEVEL = "WARNING"
            setup_logging()

        assert len(root.handlers) == handler_count
        assert root.level == logging.WARNING
    finally:
        root.handlers = original_handlers
