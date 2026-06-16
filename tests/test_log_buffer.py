"""Tests for the in-memory problem-log buffer."""

from __future__ import annotations

import logging

from src.common import log_buffer
from src.common.log_buffer import BufferLogHandler


def setup_function():
    log_buffer.clear()


def test_records_warning_and_error_not_info():
    handler = BufferLogHandler(level=logging.WARNING)
    logger = logging.getLogger("test.buffer")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info("quiet")
        logger.warning("heads up")
        logger.error("broken")
    finally:
        logger.removeHandler(handler)

    msgs = [(e.level, e.message) for e in log_buffer.recent()]
    assert ("WARNING", "heads up") in msgs
    assert ("ERROR", "broken") in msgs
    assert all(m != "quiet" for _, m in msgs)


def test_note_http_levels_and_filters():
    log_buffer.note_http("GET", "/x", 404)
    log_buffer.note_http("POST", "/y", 500)

    assert [e.level for e in log_buffer.recent(kind="http")] == ["ERROR", "WARNING"]  # newest first
    assert log_buffer.recent(level="ERROR")[0].source == "HTTP 500"
    c = log_buffer.counts()
    assert c["http"] == 2 and c["ERROR"] == 1 and c["WARNING"] == 1


def test_recent_is_newest_first_and_clear():
    log_buffer.record("WARNING", "a", "first")
    log_buffer.record("WARNING", "b", "second")
    assert [e.message for e in log_buffer.recent()] == ["second", "first"]
    log_buffer.clear()
    assert log_buffer.recent() == []
