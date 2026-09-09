"""Logging setup. Call setup_logging() once at app start."""

from __future__ import annotations

import logging
import sys

from .config import settings


class RedactionFilter(logging.Filter):
    """Mask common PII and credentials before a record reaches a handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        from .log_buffer import redact_sensitive

        record.msg = redact_sensitive(record.getMessage())
        record.args = ()
        return True


class QuietHealthChecks(logging.Filter):
    """`/healthz` 의 성공 응답은 액세스 로그에 안 적습니다.

    **실측 (2026-09-09, Render 로그 1시간치 1,265줄)**: 그중 **861줄(68%)이 `/healthz`**
    였습니다. 플랫폼의 헬스 체크가 **약 4.2초마다** 한 번씩 두드리고(하루 약 17,000번),
    거기에 외부 업타임 모니터 여덟 곳이 더 붙습니다. 그래서 무슨 일이 났는지 보려면
    로그 세 페이지를 넘겨야 실제 줄 하나가 나옵니다 — 그리고 이 앱의 운영 진단은
    사실상 그 로그뿐입니다.

    **끄는 것은 200 뿐입니다.** 헬스 체크가 실패하는 줄은 정확히 보고 싶은 줄입니다.
    uvicorn 의 액세스 레코드는 인자가 `(client, method, path, http_version, status)`
    다섯 개로 고정이라, 글자를 헤집지 않고 그 자리에서 판단합니다.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) != 5:
            return True
        path, status = args[2], args[4]
        try:
            return not (str(path).split("?")[0] == "/healthz" and int(status) < 400)
        except (TypeError, ValueError):
            return True


def _quiet_health_checks() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, QuietHealthChecks) for item in logger.filters):
        logger.addFilter(QuietHealthChecks())


def _attach_redaction_filter(handler: logging.Handler) -> None:
    if not any(isinstance(item, RedactionFilter) for item in handler.filters):
        handler.addFilter(RedactionFilter())


def _attach_buffer_handler(root: logging.Logger) -> None:
    """Attach the in-memory WARNING+ buffer handler once (for the /logs viewer)."""
    from .log_buffer import BufferLogHandler

    if any(isinstance(h, BufferLogHandler) for h in root.handlers):
        return
    buf = BufferLogHandler(level=logging.WARNING)
    _attach_redaction_filter(buf)
    root.addHandler(buf)


def setup_logging() -> None:
    """Configure stdlib logging with a simple readable format."""
    root = logging.getLogger()
    if root.handlers:
        # Already configured (uvicorn may have done it). Adjust level only, but
        # still ensure our buffer handler is attached so /logs captures events.
        root.setLevel(settings.LOG_LEVEL)
        for existing in root.handlers:
            _attach_redaction_filter(existing)
        _attach_buffer_handler(root)
        _quiet_health_checks()
        return

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    _attach_redaction_filter(handler)
    root.addHandler(handler)
    root.setLevel(settings.LOG_LEVEL)
    _attach_buffer_handler(root)
    _quiet_health_checks()
