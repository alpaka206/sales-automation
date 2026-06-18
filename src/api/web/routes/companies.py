"""Company page — all contacts and conversations sharing one email domain.

Surfaces the cross-ticket / cross-person history for a company: "same person,
different ticket" and "different people, same domain", each with its rolling
summary and append-only processing log. Linked from the message detail sidebar.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from ....db.models import Contact, Conversation, ConversationProgress, Message
from ....db.session import SessionLocal
from ._shared import templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["web"])


@router.get("/companies/{domain}")
async def company_detail(request: Request, domain: str):
    """Render every contact + conversation for an email domain."""
    from ....common.domains import is_personal_domain

    domain_l = (domain or "").lower()
    # Never aggregate personal/free-email domains (gmail, naver, …) as one company —
    # that would expose unrelated customers' conversations to each other.
    if not domain_l or is_personal_domain(domain_l):
        return templates.TemplateResponse(
            request,
            "company_detail.html",
            {
                "domain": domain,
                "company_name": None,
                "people": [],
                "conversations": [],
                "total_conversations": 0,
                "personal_domain": True,
            },
        )
    with SessionLocal() as session:
        contacts = (
            session.execute(
                select(Contact)
                .where(func.lower(Contact.domain) == domain_l)
                .order_by(Contact.created_at.asc())
            )
            .scalars()
            .all()
        )
        contact_ids = [c.id for c in contacts]

        conversations: list[dict] = []
        if contact_ids:
            rows = session.execute(
                select(Conversation, Contact)
                .join(Contact, Conversation.contact_id == Contact.id)
                .where(Conversation.contact_id.in_(contact_ids))
                .order_by(Conversation.created_at.desc())
            ).all()
            conv_ids = [cv.id for cv, _ in rows]
            latest = (
                dict(
                    session.execute(
                        select(Message.conversation_id, func.max(Message.id))
                        .where(Message.conversation_id.in_(conv_ids))
                        .group_by(Message.conversation_id)
                    ).all()
                )
                if conv_ids
                else {}
            )
            counts = (
                dict(
                    session.execute(
                        select(Message.conversation_id, func.count(Message.id))
                        .where(Message.conversation_id.in_(conv_ids))
                        .group_by(Message.conversation_id)
                    ).all()
                )
                if conv_ids
                else {}
            )
            for cv, ct in rows:
                prog = (
                    session.execute(
                        select(ConversationProgress)
                        .where(ConversationProgress.conversation_id == cv.id)
                        .order_by(
                            ConversationProgress.created_at.asc(),
                            ConversationProgress.id.asc(),
                        )
                    )
                    .scalars()
                    .all()
                )
                conversations.append(
                    {
                        "conversation_id": cv.id,
                        "contact_name": ct.full_name,
                        "contact_email": ct.email,
                        "ticket_id": cv.hubspot_ticket_id,
                        "topic": cv.topic,
                        "summary": cv.summary,
                        "customer_requests": cv.customer_requests,
                        "message_count": counts.get(cv.id, 0),
                        "last_activity": cv.last_incoming_at
                        or cv.last_outgoing_at
                        or cv.created_at,
                        "link_message_id": latest.get(cv.id),
                        "progress": [
                            {"kind": p.kind, "detail": p.detail, "created_at": p.created_at}
                            for p in prog
                        ],
                    }
                )

        people = [
            {
                "id": c.id,
                "name": c.full_name,
                "email": c.email,
                "company": c.company,
                "role_description": c.role_description,
            }
            for c in contacts
        ]
        company_name = next((c.company for c in contacts if c.company), None)

    ctx = {
        "domain": domain,
        "company_name": company_name,
        "people": people,
        "conversations": conversations,
        "total_conversations": len(conversations),
    }
    return templates.TemplateResponse(request, "company_detail.html", ctx)
