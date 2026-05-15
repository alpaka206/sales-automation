"""Prospect status enum, transition matrix, and transition helpers."""

from __future__ import annotations

import enum
import logging

from sqlalchemy.orm import Session

from ...db.models import Prospect

logger = logging.getLogger(__name__)


class ProspectStatus(str, enum.Enum):
    """All valid prospect statuses."""

    COLLECTED = "collected"
    ANALYZED = "analyzed"
    SENT = "sent"
    REPLIED = "replied"
    IN_PROGRESS = "in_progress"
    WON = "won"
    LOST = "lost"
    SKIPPED_LOWSCORE = "skipped_lowscore"
    SKIPPED_DUP = "skipped_dup"
    BOUNCED = "bounced"


KR_LABELS: dict[str, str] = {
    "collected": "가져옴",
    "analyzed": "분석완료",
    "sent": "메일발송",
    "replied": "메일응답",
    "in_progress": "진행중",
    "won": "성사",
    "lost": "실패",
    "skipped_lowscore": "점수미달",
    "skipped_dup": "중복제외",
    "bounced": "반송",
}

VALID_TRANSITIONS: dict[ProspectStatus, set[ProspectStatus]] = {
    ProspectStatus.COLLECTED: {
        ProspectStatus.ANALYZED,
        ProspectStatus.SKIPPED_LOWSCORE,
        ProspectStatus.SKIPPED_DUP,
    },
    ProspectStatus.ANALYZED: {ProspectStatus.SENT, ProspectStatus.LOST},
    ProspectStatus.SENT: {
        ProspectStatus.REPLIED,
        ProspectStatus.BOUNCED,
        ProspectStatus.LOST,
    },
    ProspectStatus.REPLIED: {
        ProspectStatus.IN_PROGRESS,
        ProspectStatus.WON,
        ProspectStatus.LOST,
    },
    ProspectStatus.IN_PROGRESS: {ProspectStatus.WON, ProspectStatus.LOST},
    ProspectStatus.WON: set(),
    ProspectStatus.LOST: set(),
    ProspectStatus.SKIPPED_LOWSCORE: set(),
    ProspectStatus.SKIPPED_DUP: set(),
    ProspectStatus.BOUNCED: set(),
}


class InvalidStatusTransition(ValueError):
    """Raised when a prospect status transition is not allowed."""


def transition(
    session: Session,
    prospect_id: int,
    new_status: ProspectStatus | str,
    reason: str | None = None,
) -> Prospect:
    """Transition a prospect to a new status if the transition is valid."""
    if isinstance(new_status, str):
        new_status = ProspectStatus(new_status)

    prospect = session.get(Prospect, prospect_id)
    if prospect is None:
        raise ValueError(f"Prospect {prospect_id} not found.")

    current = ProspectStatus(prospect.status)
    allowed = VALID_TRANSITIONS.get(current, set())

    if new_status not in allowed:
        raise InvalidStatusTransition(
            f"Cannot transition prospect {prospect_id} "
            f"from '{current.value}' to '{new_status.value}'."
        )

    prospect.status = new_status.value
    logger.info(
        "Prospect %d: %s → %s%s",
        prospect_id,
        current.value,
        new_status.value,
        f" ({reason})" if reason else "",
    )
    return prospect
