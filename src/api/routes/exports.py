"""문의 원문 내려받기 — 실제로 어떤 문의가 오는지 읽어보기 위한 텍스트 파일.

콘솔 화면으로는 답할 수 없는 질문들이 있습니다. 실제로 어떤 유형의 문의가 오는지, 한 문의가
여러 유형에 동시에 걸치는 일이 얼마나 잦은지, 성사된 문의의 첫 메일이 실패한 문의의 첫 메일과
어떻게 다른지. 그건 원문을 쭉 읽어야 보이고, 읽으려면 파일이 편합니다.

각 문의의 **첫 인바운드 메일**만 담습니다. 분류기가 실제로 본 것이 그것이고, 회신이나 이후
왕복은 이 질문들에 답하지 않으면서 파일만 키웁니다.
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
    """단계별로 묶은 문의 원문. 성사·실패가 한눈에 대비되도록 순서를 정합니다."""
    stage_order = [
        ("won", "계약 성사"),
        ("negotiation", "협의 중"),
        ("reminder_sent", "리마인더 발송"),
        ("meeting_link_sent", "답변 발송"),
        ("new", "새 문의"),
        ("closed_lost", "실패"),
        ("closed", "협상 전 종료"),
    ]

    with SessionLocal() as session:
        rows = session.execute(
            select(Conversation, Contact)
            .join(Contact, Conversation.contact_id == Contact.id)
            .order_by(Conversation.created_at.desc())
        ).all()
        # 문의마다 가장 이른 인바운드 = 분류기가 실제로 본 메일. 행마다 조회하지 않고
        # 한 번의 그룹 조회로 id를 모은 뒤 본문을 한 번에 읽습니다.
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

    by_stage: dict[str, list] = {}
    for conversation, contact in rows:
        by_stage.setdefault(conversation.stage or "new", []).append((conversation, contact))

    now = datetime.now(timezone.utc).astimezone()
    out = [
        "PERSO 인바운드 문의 원문",
        f"내려받은 시각: {now.strftime('%Y-%m-%d %H:%M')}",
        f"문의 수: {len(rows)}건 (각 문의의 첫 수신 메일만)",
        "",
        "단계별 건수: "
        + " · ".join(
            f"{label} {len(by_stage.get(key, []))}" for key, label in stage_order
            if by_stage.get(key)
        ),
        "",
    ]

    for key, label in stage_order:
        group = by_stage.get(key)
        if not group:
            continue
        out += ["", "=" * 78, f"■ {label}  ({len(group)}건)", "=" * 78]
        for conversation, contact in group:
            message = bodies.get(first_ids.get(conversation.id, 0))
            received = conversation.created_at.strftime("%Y-%m-%d") if conversation.created_at else "-"
            company = _clean(contact.company) or _clean(contact.full_name) or "(회사 미상)"
            out += [
                "",
                _RULE,
                f"#{conversation.id}  {company}"
                f"  · {contact.country or '국가 미상'}"
                f"  · {conversation.inquiry_language or '언어 미상'}"
                f"  · {received}",
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
