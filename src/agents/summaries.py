"""티켓 요약 — 오간 것 하나에 불릿 하나. **덧붙이기만 합니다.**

예전에는 대화 전체를 모델에 다시 넣어 문단 하나로 **다시 썼습니다.** 두 가지가 틀렸습니다.

  * **읽은 것이 나간 답이 아니었습니다.** 그 호출은 초안이 만들어진 **직후**에 돌면서 그
    대화의 모든 메시지를 읽었고, 초안도 메시지 행이라 「이에 Perso AI 는 …라고 안내했다」가
    아무도 안 보낸 글에서 나왔습니다. 운영자가 이 요약을 읽고 다음 답을 씁니다 — 「이 얘기는
    이미 했으니 생략」이 되어 버립니다(2026-08-20 지적).
  * **기록이 하나 늘 때마다 앞의 것이 다시 쓰였습니다.** 지난달에 읽은 문장이 이번 달에
    말이 달라지면 요약이 기록의 구실을 못 합니다.

그래서 이제 **실제로 일어난 일 하나에 줄 하나**를 붙입니다. 고객 문의가 오면 한 줄, 답이
**정말 나가면** 한 줄. 앞의 줄은 건드리지 않습니다.

한 줄로 줄이는 프롬프트는 리드 히스토리와 같은 것(`util/summarize_touchpoint`)입니다 —
같은 성격의 줄을 화면 두 곳에서 다르게 쓸 이유가 없습니다.
"""

from __future__ import annotations

import logging

from ..db.models import Conversation, Message
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)

_MAX_SUMMARY = 8000


def one_line(direction: str, subject: str | None, body: str | None) -> str | None:
    """기록 한 건을 한 줄로. 실패하면 None — 요약 한 줄 때문에 기록을 잃지 않습니다."""
    text = (body or "").strip()
    if not text:
        return None
    if len(text) < 80:
        # 이미 짧은 것은 줄일 것이 없습니다. 모델 왕복만 늘어납니다.
        return " ".join(text.split())[:200]
    try:
        from ..llm.client import LLMClient

        line = LLMClient().complete(
            "util/summarize_touchpoint",
            {
                "direction": "받은 메일" if direction in {"inbound", "incoming"} else "보낸 메일",
                "subject": subject or "(제목 없음)",
                "body": text[:4000],
            },
            tier="flash",
            max_tokens=120,
        )
        line = str(line or "").strip().splitlines()[0].strip().lstrip("-·• ").strip()
        return line[:200] or None
    except Exception:
        logger.warning("기록 한 줄 요약 실패", exc_info=True)
        return None


def append_line(session, conv_id: int | None, line: str | None) -> None:
    """티켓 요약에 불릿 한 줄을 덧붙입니다. **앞 줄은 고치지 않습니다.**

    부르는 곳이 둘입니다: 우리 메시지(`append_summary_line`)와 허브스팟에서 끌어온 그
    티켓의 메일. 둘 다 「이 티켓에 실제로 일어난 일」이라 한 목록에 섞여야 읽힙니다 —
    우리가 만든 메시지만 세면 영업이 허브스팟에서 직접 한 답이 요약에서 빠집니다.
    """
    line = (line or "").strip()
    if not conv_id or not line:
        return
    conv = session.get(Conversation, conv_id)
    if conv is None:
        return
    bullet = f"- {line}"
    current = (conv.summary or "").strip()
    lines = current.splitlines() if current else []
    # 같은 이벤트가 두 번 들어오는 길이 있습니다(웹훅 + 폴러, 재동기화). 이미 있는 줄이면
    # 붙이지 않습니다 — 요약이 같은 말을 두 번 하면 읽는 사람은 두 번 일어난 줄 압니다.
    if bullet in lines:
        return
    lines.append(bullet)
    # 넘치면 **줄 단위로** 앞에서 버립니다. 글자 수로 자르면 가장 오래된 불릿이
    # 반 토막 난 채 남습니다.
    while len(lines) > 1 and sum(len(x) + 1 for x in lines) > _MAX_SUMMARY:
        lines.pop(0)
    conv.summary = "\n".join(lines)


def append_summary_line(message_id: int | None) -> None:
    """이 메시지의 한 줄을 만들어 **행에 저장하고** 티켓 요약에 덧붙입니다.

    앞 줄은 절대 고치지 않습니다. 두 번 불러도 한 줄입니다 — 같은 메시지에 이미 줄이
    있으면 아무것도 하지 않습니다(티켓 하나에 이벤트가 여러 번 옵니다).
    """
    if not message_id:
        return
    try:
        with SessionLocal() as session:
            msg = session.get(Message, message_id)
            if msg is None or msg.summary_line or not (msg.body or "").strip():
                return
            direction, subject, body, conv_id = (
                msg.direction, msg.subject, msg.body, msg.conversation_id,
            )

        line = one_line(direction, subject, body)
        if not line:
            return
        with SessionLocal() as session:
            msg = session.get(Message, message_id)
            if msg is None or msg.summary_line:
                return
            msg.summary_line = line[:300]
            append_line(session, conv_id, line)
            session.commit()
    except Exception:
        logger.warning("티켓 요약 덧붙이기 실패 (message %s)", message_id, exc_info=True)


def rebuild_summary(session, conv_id: int) -> int:
    """한 티켓의 요약을 **실제로 오간 것**으로 다시 만듭니다. 쓴 줄 수를 돌려줍니다.

    두 곳에서 모읍니다 — 우리 DB 의 메시지(문의와 **정말 나간** 답)와 허브스팟에서 끌어온
    **그 티켓의** 메일. 영업이 콘솔 밖에서 직접 한 답이 뒤쪽에 있고, 우리 메시지만 세면
    요약에서 통째로 빠집니다. 둘을 한 시간축에 섞어 시간순으로 붙입니다.

    나가지 않은 초안은 애초에 후보가 아닙니다(`DELIVERED_STATUSES`). 커밋은 부르는
    쪽이 합니다 — 여러 건을 한 번에 돌릴 때 왕복을 건마다 하지 않으려고요.
    """
    from ..db.models import DELIVERED_STATUSES, CustomerInteraction

    events: list[tuple[object, str | tuple, int | None]] = []
    for mid, at, line, direction, subject, body in session.query(
        Message.id, Message.created_at, Message.summary_line,
        Message.direction, Message.subject, Message.body,
    ).filter(
        Message.conversation_id == conv_id,
        (Message.direction == "inbound") | (Message.status.in_(DELIVERED_STATUSES)),
    ).all():
        if (body or "").strip():
            events.append((at, line or (direction, subject, body), mid))
    for at, context, direction, subject, summary in session.query(
        CustomerInteraction.happened_at, CustomerInteraction.context,
        CustomerInteraction.direction, CustomerInteraction.subject,
        CustomerInteraction.summary,
    ).filter(CustomerInteraction.conversation_id == conv_id).all():
        events.append((at, (context or "").strip() or (direction, subject, summary), None))
    if not events:
        return 0

    conv = session.get(Conversation, conv_id)
    if conv is None:
        return 0
    conv.summary = None  # 다시 만드는 것이지 덧붙이는 것이 아닙니다.
    written = 0
    for _at, line, message_id in sorted(events, key=lambda e: (e[0] is None, e[0])):
        if not isinstance(line, str):  # 아직 줄이 없는 것 — 지금 만듭니다.
            line = one_line(*line) or ""
            if line and message_id:
                msg = session.get(Message, message_id)
                if msg is not None:
                    msg.summary_line = line[:300]
        if line:
            append_line(session, conv_id, line)
            written += 1
    return written
