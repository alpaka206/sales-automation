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

# 처리 경과는 「이 고객에게 무슨 일이 있었나」다. 아래 종류는 그게 아니라 **앱이 스스로에게
# 한 일**이고, 화면이 이미 보여 주는 것을 한 번 더 말한다. 읽을 때만 숨기고 행은 그대로
# 쌓는다 — 문의가 들어왔을 때 설명할 거리가 남아 있어야 한다.
#
# ``reply`` 가 여기 있는 이유: 「답변 발송 완료: <제목>」인데, New 를 지난 티켓의 기록에는
# **그 메일 자체가 줄로 서 있다.** 나갔다는 사실도 제목도 바로 옆에 있고, 제목은 f5a3df1 이
# 「같은 제목이 원문·국문으로 나란히 놓여 다른 두 건처럼 보인다」며 그 목록에서 이미 뺀
# 값이다. 그 카드는 애초에 afterNew 일 때만 그려지므로 — 즉 메일 줄이 반드시 함께 있으므로 —
# 숨겨서 잃는 정보가 없다 (2026-08-26 운영자 지시).
#
# **한 곳에 두는 이유**: 예전에는 이 목록이 messages.py 안에만 있어서 티켓 세부 내역만
# 걸러졌고, 고객 상세는 같은 행을 ``kind`` 문자열까지 그대로 찍고 있었다. 화면마다 목록을
# 따로 들면 다음에 종류가 하나 늘 때 한 화면만 조용히 빠진다.
# ``inbound`` 은 기계 소음이 아니다 — 「고객 문의 접수: <본문 앞부분>」은 진짜 사건이다.
# 그런데 그 문의 메일 자체가 같은 목록에 줄로 서 있고, 그 줄이 본문을 더 온전히 보여 준다.
# 티켓 세부 내역은 이미 화면에서 걸러내고 있었고(``kind !== "inbound"``), 고객 상세만
# 안 걸러서 같은 말이 두 번 있었다 (2026-08-26 운영자 지시). 거르는 곳을 여기 하나로 모은다.
ROUTINE_PROGRESS_KINDS: tuple[str, ...] = (
    "draft",
    "auto_ack",
    "stage",
    "translate",
    "reply",
    "inbound",
)


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
