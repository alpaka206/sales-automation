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


def one_line(
    direction: str, subject: str | None, body: str | None, *, always: bool = False
) -> str | None:
    """기록 한 건을 한 줄로. 실패하면 None — 요약 한 줄 때문에 기록을 잃지 않습니다.

    ``always`` 는 **짧은 본문도 모델에 맡깁니다** (2026-09-03 운영자 지시: 「다 쓰길 원해」).
    기본값은 예전 그대로 — 티켓 요약(`append_summary_line`)은 오간 것마다 그때그때 도는
    자리라, 짧은 줄까지 모델을 부르면 문의 한 건에 왕복이 하나씩 더 붙습니다. 뒤늦게 메우는
    백필은 기다리는 사람이 없으므로 켭니다.
    """
    text = (body or "").strip()
    if not text:
        return None
    if len(text) < 80 and not always:
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


def backfill_interaction_digests(limit: int = 120) -> int:
    """한 줄 요약이 비어 있는 기록을 조금씩 채웁니다. 채운 건수를 돌려줍니다.

    **왜 필요한가.** 기록이 들어오는 길이 여럿인데 요약을 만드는 길은 일부뿐입니다 —
    허브스팟 메일 임포트(`customer_ops`)와 지난 티켓 이관(`hubspot_reconcile`)은 채우지만,
    티켓 대화를 통째로 받아오는 `ticket_history` 는 ``context=None`` 으로 넣습니다(그
    경로는 티켓 수백 건을 도는 자리라 줄마다 모델을 부를 수 없습니다). 그래서 화면이
    요약 대신 **본문 앞머리**를 보여 주는 줄이 쌓였습니다. 여기서 뒤늦게 메웁니다.

    **티켓이 없는 기록도 같이 돕니다** (2026-09-03 운영자 지시) — 조건은 ``conversation_id``
    가 아니라 「요약이 비었나」 하나입니다.

    **이어하기는 `context IS NULL` 그 자체입니다.** 따로 진행 위치를 적지 않습니다 — 비어
    있다는 것이 곧 「아직 안 했다」이고, 배포가 끼어들어도 다음 순회가 이어서 합니다.

    **이미 값이 있으면 안 건드립니다.** 그 칸은 사람이 적을 수도 있는 자리라, 덮어쓰면
    운영자가 쓴 메모가 모델 한 줄로 바뀝니다.

    **티켓 하나씩, 티켓 고르기는 무작위로.** 두 가지를 동시에 만족해야 합니다.

    *티켓 단위인 이유*: 티켓 요약은 그 티켓의 줄이 **전부** 채워졌을 때만 다시 만듭니다.
    표 전체에서 무작위로 집던 때에는 한 티켓의 열네 줄이 다 뽑힐 때까지 몇 시간이 걸려
    **요약이 하나도 안 만들어졌습니다**(운영 로그: 「20건 채움, 티켓 요약 0건 재생성, 남은
    1864건」). 티켓 단위면 첫 회차부터 하나씩 완성됩니다.

    *무작위인 이유*: 이 저장소가 이미 당한 자리입니다. NULL 을 「아직 안 했다」로 쓰면서
    순서를 고정하면 앞머리의 **계속 실패하는** 것이 뒤엣것을 영영 굶깁니다(`ticket_history`
    에서 12건 중 8건이 실패해 나머지 4건이 한 번도 안 불렸던 그 일). 이제 그 무작위가
    행이 아니라 **티켓**에 걸립니다.

    **길이와 무관하게 전부 모델이 씁니다** (2026-09-03 운영자 지시). 짧은 본문을 그대로
    눌러 쓰던 지름길은 이 경로에서만 끕니다(`one_line(always=True)`) — 티켓 요약은 오간
    것마다 그때그때 도는 자리라 그대로 둡니다.

    그래서 **실패한 행은 값이 없는 채로 남고 다음 회차에 다시 집힙니다.** 본문을 그대로
    적어 두고 끝내지 않는 이유: 그러면 모델이 잠깐 죽어 있던 동안의 행들만 영영 요약 없이
    굳는데, 화면에서는 그 차이가 안 보입니다. 남은 건수가 0 으로 안 내려가면 그건 모델이
    아프다는 뜻이고, 그 편이 조용히 굳는 것보다 낫습니다.
    """
    from sqlalchemy import func as sa_func, select

    from ..db.models import CustomerInteraction

    # **공백만 있는 요약은 빼야 합니다.** `!= ""` 만으로는 " " 가 통과하는데, `one_line` 은
    # 그것을 strip 해서 빈 문자열로 보고 None 을 돌려줍니다 — 그러면 그 행은 영원히 다시
    # 집히고 「남은 N건」이 0 으로 안 내려갑니다.
    pending = (
        CustomerInteraction.context.is_(None),
        sa_func.trim(CustomerInteraction.summary) != "",
    )
    budget = max(1, min(limit, 400))
    with SessionLocal() as session:
        # **티켓 하나씩 통째로 채웁니다** (2026-09-04).
        #
        # 처음에는 표 전체에서 무작위로 집었습니다. 굶주림은 안 생겼지만 **요약이 하나도 안
        # 만들어졌습니다** — 티켓 요약은 그 티켓의 줄이 **전부** 채워졌을 때만 다시 만드는데,
        # 무작위로 집으면 한 티켓의 열네 줄이 다 뽑힐 때까지 몇 시간이 걸립니다. 운영 로그가
        # 그대로 말해 줬습니다: 「20건 채움, 티켓 요약 **0건** 재생성, 남은 1864건」. 그동안
        # 화면에서는 이전 티켓들이 제목·날짜만 남아 「히스토리가 사라진」 것처럼 보입니다.
        #
        # 티켓 단위로 집으면 **첫 회차부터 요약이 하나씩 완성됩니다.** 티켓 고르기는 여전히
        # 무작위라, 계속 실패하는 티켓이 뒤엣것을 굶기지 않습니다.
        conv_ids = [
            row
            for row in session.scalars(
                select(CustomerInteraction.conversation_id)
                .where(*pending, CustomerInteraction.conversation_id.is_not(None))
                .group_by(CustomerInteraction.conversation_id)
                .order_by(sa_func.random())
                .limit(40)
            )
        ]
        rows: list = []
        for conv_id in conv_ids:
            if len(rows) >= budget:
                break
            rows.extend(
                session.scalars(
                    select(CustomerInteraction)
                    .where(*pending, CustomerInteraction.conversation_id == conv_id)
                ).all()
            )
        # 티켓에 안 달린 줄은 다시 만들 요약이 없으므로 남는 자리만 씁니다. 그래도
        # 채웁니다 — 고객 상세의 미리보기가 그 값을 읽습니다.
        if len(rows) < budget:
            rows.extend(
                session.scalars(
                    select(CustomerInteraction)
                    .where(*pending, CustomerInteraction.conversation_id.is_(None))
                    .order_by(sa_func.random())
                    .limit(budget - len(rows))
                ).all()
            )
        # 세션을 붙들고 모델을 기다리지 않습니다 — 값만 들고 나옵니다.
        targets = [(r.id, r.direction or "", r.subject, r.summary) for r in rows]
        remaining = session.scalar(
            select(sa_func.count(CustomerInteraction.id)).where(*pending)
        )

    digests: dict[int, str] = {}
    for row_id, direction, subject, body in targets:
        # `always=True` — **짧은 기록도 모델이 씁니다** (2026-09-03 운영자 지시).
        # 그래서 실패하면 그 행은 `context IS NULL` 로 남아 다음 회차에 다시 집힙니다.
        # 본문을 그대로 적어 두고 끝내지 않는 이유: 그러면 모델이 잠깐 죽어 있던 동안의
        # 행들만 영영 요약 없이 굳고, 화면에서는 그 차이가 안 보입니다.
        line = one_line(direction, subject, body, always=True)
        if line:
            digests[row_id] = line

    filled = 0
    rebuilt = 0
    if digests:
        with SessionLocal() as session:
            touched: set[int] = set()
            for row_id, line in digests.items():
                row = session.get(CustomerInteraction, row_id)
                # 그 사이에 사람이 적었을 수 있습니다. 그러면 그쪽이 이깁니다.
                if row is not None and row.context is None:
                    row.context = line
                    filled += 1
                    if row.conversation_id is not None:
                        touched.add(row.conversation_id)
            session.flush()
            # **줄이 다 채워진 티켓의 요약을 다시 만듭니다** (2026-09-04 운영자 지시:
            # 「요약을 만들어」).
            #
            # 왜 필요한가: 티켓 화면의 「리드 히스토리」가 이제 `conversations.summary`
            # 하나를 그립니다. 그런데 그 칸을 채우는 자동 경로는 셋뿐이고(실시간 문의 ·
            # 실제로 나간 회신 · CRM 메일 임포트), **대화를 통째로 받아오는 수집기
            # (`ticket_history`)는 한 줄도 안 붙입니다.** 그래서 기록이 가장 많은 티켓의
            # 요약이 가장 비어 있었습니다(실측 321건 중 106건이 빈 칸).
            #
            # `rebuild_summary` 는 접점 기록의 `context` 를 **먼저** 씁니다. 그래서 이
            # 자리에서 부르면 방금 채운 줄들이 그대로 쓰이고 **모델을 다시 안 부릅니다.**
            #
            # **그 대화에 빈 줄이 하나도 안 남았을 때만** 부릅니다. 남아 있으면
            # `rebuild_summary` 가 그 줄을 모델로 만들었다가 행에는 안 남기고 버려서
            # (`Message` 에만 적습니다) 회차마다 같은 값을 다시 삽니다.
            for conv_id in touched:
                unfinished = session.scalar(
                    select(sa_func.count(CustomerInteraction.id)).where(
                        CustomerInteraction.conversation_id == conv_id,
                        CustomerInteraction.context.is_(None),
                        sa_func.trim(CustomerInteraction.summary) != "",
                    )
                )
                if unfinished:
                    continue
                try:
                    rebuild_summary(session, conv_id)
                    rebuilt += 1
                except Exception:
                    # 요약 하나 때문에 방금 채운 줄들을 잃지 않습니다.
                    logger.warning("티켓 %s 요약 재생성 실패", conv_id, exc_info=True)
            session.commit()
    if targets:
        logger.info(
            "기록 요약 백필: %d건 시도, %d건 채움, 티켓 요약 %d건 재생성, 남은 %d건",
            len(targets), filled, rebuilt, max(0, (remaining or 0) - filled),
        )
    return filled


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
