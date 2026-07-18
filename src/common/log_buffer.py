"""In-memory ring buffer of noteworthy log events for the web log viewer.

Collects WARNING+ log records (via :class:`BufferLogHandler`) and HTTP 4xx/5xx
responses (via :func:`note_http`, called from a FastAPI middleware) so an
operator can see "things a developer should look at" at ``/logs`` without
SSHing into the box. In-memory only — bounded and reset on process restart.
"""

from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

_MAX = 500
_EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d ().-]{7,}\d)(?!\d)")
_SECRET_RE = re.compile(
    r"(?i)\b(authorization|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|"
    r"password|client[_ -]?secret)\b(\s*[:=]\s*|\s+)([^\s,;]+)"
)


def redact_sensitive(value: object) -> str:
    """Best-effort PII/secret masking for operator-visible and stdout logs."""
    text = str(value or "")
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
    return _SECRET_RE.sub(r"\1\2[REDACTED]", text)


@dataclass(frozen=True)
class LogEvent:
    ts: datetime  # UTC
    level: str  # "ERROR" | "WARNING" | "INFO" ...
    source: str  # logger name or "HTTP 404"
    message: str
    kind: str  # "log" | "http"


# deque.append/clear are atomic in CPython, so no explicit lock is needed for the
# append-from-many-threads / read-from-request pattern here.
_BUFFER: deque[LogEvent] = deque(maxlen=_MAX)


def record(level: str, source: str, message: str, kind: str = "log") -> None:
    """Append one event to the buffer (never raises)."""
    try:
        _BUFFER.append(
            LogEvent(
                ts=datetime.now(timezone.utc),
                level=(level or "INFO").upper(),
                source=redact_sensitive(source),
                message=redact_sensitive(message),
                kind=kind,
            )
        )
    except Exception:  # logging must never break the caller
        pass


def note_http(method: str, path: str, status: int) -> None:
    """Record an HTTP error response (4xx → WARNING, 5xx → ERROR)."""
    level = "ERROR" if status >= 500 else "WARNING"
    record(level, f"HTTP {status}", f"{method} {path}", kind="http")


def recent(level: str | None = None, kind: str | None = None, limit: int = 200) -> list[LogEvent]:
    """Return buffered events newest-first, optionally filtered by level/kind."""
    events = list(_BUFFER)
    events.reverse()
    if level:
        lv = level.upper()
        events = [e for e in events if e.level == lv]
    if kind:
        events = [e for e in events if e.kind == kind]
    return events[:limit]


def counts() -> dict[str, int]:
    """Totals by severity for the viewer's filter chips."""
    out = {"all": 0, "ERROR": 0, "WARNING": 0, "http": 0}
    for e in _BUFFER:
        out["all"] += 1
        if e.level in ("ERROR", "WARNING"):
            out[e.level] += 1
        if e.kind == "http":
            out["http"] += 1
    return out


def clear() -> None:
    """Empty the buffer."""
    _BUFFER.clear()


class BufferLogHandler(logging.Handler):
    """Logging handler that mirrors WARNING+ records into the ring buffer."""

    def emit(self, record_: logging.LogRecord) -> None:
        try:
            self.format(record_)  # populate record_.message
            record(record_.levelname, record_.name, record_.getMessage(), kind="log")
        except Exception:
            pass
