"""문의 원문 내려받기 — 실제로 어떤 문의가 오는지 읽어보기 위한 텍스트 파일.

용도는 하나입니다: **실제로 어떤 문의가 오는지 통째로 읽어보는 것.** 그래야 "이런 문의가 자주
오니 이런 문서가 필요하겠다"가 보입니다. 콘솔 화면은 "지금 할 일"에 답하도록 만들어져 있어서
이 질문에는 답하지 못합니다.

각 문의의 **첫 인바운드 메일**만 담습니다. 분류기가 실제로 본 것이 그것이고, 회신이나 이후
왕복은 이 질문에 답하지 않으면서 파일만 키웁니다.

시간 역순입니다. 최근에 무엇이 오고 있는지가 먼저 읽혀야 하고, 단계별로 묶으면 같은 시기의
문의가 파일 여기저기로 흩어집니다.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import func, select

from ...db.models import Contact, Conversation, Message
from ...db.session import SessionLocal
from ..auth import admin_required

router = APIRouter(tags=["web"])

_RULE = "─" * 78


def _clean(value: str | None) -> str:
    """읽기 위한 파일이므로 원문의 줄바꿈은 살리고 앞뒤 공백만 정리합니다."""
    return (value or "").strip()


def _inquiry_text() -> str:
    """문의 원문, 최근 것부터. 맨 앞에 한눈에 보이는 분포를 붙입니다."""
    with SessionLocal() as session:
        rows = session.execute(
            select(Conversation, Contact)
            .join(Contact, Conversation.contact_id == Contact.id)
            .order_by(Conversation.created_at.desc())
        ).all()
        # 문의마다 가장 이른 인바운드 = 분류기가 실제로 본 메일. 행마다 조회하지 않고 한 번의
        # 그룹 조회로 id를 모은 뒤 본문을 한 번에 읽습니다.
        first_ids = dict(
            session.execute(
                select(Message.conversation_id, func.min(Message.id))
                .where(Message.direction == "inbound")
                .group_by(Message.conversation_id)
            ).all()
        )
        bodies = {
            message.id: message
            for message in session.scalars(
                select(Message).where(Message.id.in_(list(first_ids.values()) or [0]))
            )
        }

    def tally(values) -> str:
        counts: dict[str, int] = {}
        for value in values:
            counts[value or "미상"] = counts.get(value or "미상", 0) + 1
        ordered = sorted(counts.items(), key=lambda item: -item[1])
        return " · ".join(f"{name} {count}" for name, count in ordered) or "-"

    now = datetime.now(timezone.utc).astimezone()
    out = [
        "PERSO 인바운드 문의 원문",
        f"내려받은 시각  {now.strftime('%Y-%m-%d %H:%M')}",
        f"문의 수        {len(rows)}건 (각 문의의 첫 수신 메일만, 최근 것부터)",
        "",
        f"언어           {tally(c.inquiry_language for c, _ in rows)}",
        f"국가           {tally(ct.country for _, ct in rows)}",
        f"단계           {tally(c.stage for c, _ in rows)}",
        "",
    ]

    for conversation, contact in rows:
        message = bodies.get(first_ids.get(conversation.id, 0))
        received = (
            conversation.created_at.strftime("%Y-%m-%d") if conversation.created_at else "-"
        )
        company = _clean(contact.company) or _clean(contact.full_name) or "(회사 미상)"
        out += [
            "",
            _RULE,
            f"#{conversation.id}  {received}"
            f"  · {company}"
            f"  · {contact.country or '국가 미상'}"
            f"  · {conversation.inquiry_language or '언어 미상'}"
            f"  · {conversation.stage or '-'}",
            f"제목: {_clean(conversation.inquiry_subject) or '(제목 없음)'}",
            _RULE,
            _clean(message.body if message else None) or "(본문 없음)",
        ]
        requests = _clean(conversation.customer_requests)
        if requests:
            out += ["", f"[AI가 뽑은 고객 요청] {requests}"]

    return "\n".join(out) + "\n"


@router.get("/operations/export/inquiries")
async def export_inquiries(request: Request) -> Response:
    if not admin_required(request):
        raise HTTPException(status_code=403, detail="관리자만 접근할 수 있습니다.")

    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M")
    # BOM: 메모장이 UTF-8로 열도록. 없으면 한글이 깨져 보입니다.
    return Response(
        content="﻿" + _inquiry_text(),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="inquiries-{stamp}.txt"'},
    )
