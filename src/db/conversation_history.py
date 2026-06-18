"""Helpers for conversation history: append-only progress log + summary access.

The processing log ("처리경과") is APPEND-ONLY by the operator's rule. This module
is the only writer, and it only ever INSERTs — there is intentionally no update or
delete path, so existing entries can never be altered.
"""

from __future__ import annotations

import logging

from .models import ConversationProgress
from .session import SessionLocal

logger = logging.getLogger(__name__)


def add_progress(
    conversation_id: int,
    kind: str,
    detail: str,
    *,
    actor: str | None = None,
    session=None,
) -> None:
    """Append one dated progress entry. Never updates/deletes existing rows.

    If ``session`` is given, the row is added to it and the caller commits;
    otherwise a short-lived session is opened and committed here. Best-effort:
    a logging failure never propagates into the calling pipeline.
    """
    detail = (detail or "").strip()
    if not detail:
        return
    row = ConversationProgress(
        conversation_id=conversation_id, kind=kind, detail=detail, actor=actor
    )
    if session is not None:
        session.add(row)
        return
    try:
        with SessionLocal() as own:
            own.add(row)
            own.commit()
    except Exception:
        logger.warning(
            "Failed to append progress (conv=%s kind=%s)", conversation_id, kind, exc_info=True
        )
