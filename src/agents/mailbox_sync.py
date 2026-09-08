"""개인 사서함으로 온 메일을 주워 우리 기록에 잇습니다 (2026-09-08 운영자 지시).

**허브스팟이 못 보는 자리를 메웁니다.** 담당자 개인함으로 직접 온 메일은 Conversations
스레드를 안 지나므로 티켓에도, 리드 히스토리에도 남지 않습니다. 그 사람이 콘솔에서
답을 쓰려고 화면을 열면 그 대화만 통째로 비어 있습니다.

규칙 넷, 전부 운영자가 정한 것입니다:

1. **동의 이후 메일만.** 토큰에는 시점 제한이 없어 몇 년 전까지 읽히므로 우리가 끊습니다
   (`mailbox_accounts.collect_from`).
2. **다 긁어오지 않습니다.** 보낸/받는 주소가 **우리가 아는 연락처**일 때만 남깁니다.
   모르는 주소는 버립니다 — 개인함에는 회사 공지도 광고도 옵니다.
3. **허브스팟과 겹치면 허브스팟 것만.** 같은 메일이 두 줄로 서면 화면이 대화를 두 번
   합니다. 가리는 자를 새로 만들지 않고 `ticket_history._merge_crm_twins` 가 쓰는 규칙을
   그대로 씁니다 — **같은 연락처 · 같은 초 · 같은 방향**.
4. **티켓 연결은 사람이 누릅니다.** 후보가 여럿이면 **가장 최근 티켓**을 제안하고
   (운영자 지시), 연락처는 아는데 티켓이 없으면 리드 히스토리에만 남깁니다.

**메일은 먼저 들어가고, 연결은 나중에 붙습니다.** `customer_interactions` 에
`conversation_id` 없이 넣어 두면 그 자체로 리드 히스토리의 한 줄이고(그게 티켓 없는
연락처의 답입니다), 「연결」은 그 칸을 채우는 일이 됩니다. 확인 대기 표를 따로 만들지
않는 이유이고, 운영자가 거절해도 기록은 남는다는 뜻이기도 합니다.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from email.utils import getaddresses, parsedate_to_datetime

import httpx
from sqlalchemy import select

from ..db.models import Contact, Conversation, CustomerInteraction, MailboxLinkDecision
from ..db.session import SessionLocal
from ..integrations.gmail import (
    MailboxTokenError,
    access_token,
    enabled_accounts,
    mark_polled,
)

logger = logging.getLogger(__name__)

_API = "https://gmail.googleapis.com/gmail/v1/users/me"
# 한 회차에 사서함당 볼 메일 수. 개인함은 하루 수십 통이고 3시간마다 도므로 넉넉합니다.
MESSAGES_PER_SWEEP = 50
_TIMEOUT = 30.0


def _headers(payload: dict) -> dict[str, str]:
    return {
        str(h.get("name", "")).lower(): str(h.get("value", ""))
        for h in (payload.get("headers") or [])
    }


def _plain_text(part: dict) -> str:
    """본문에서 글자만. `text/plain` 이 있으면 그것, 없으면 첫 `text/*`.

    HTML 을 글자로 바꾸는 일은 여기서 안 합니다 — 이 저장소에 이미 그 함수가 있고
    (`integrations.hubspot`), 두 벌이면 같은 메일이 화면마다 다르게 보입니다.
    """
    mime = str(part.get("mimeType") or "")
    body = (part.get("body") or {}).get("data")
    if body and mime.startswith("text/"):
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        text = raw.decode("utf-8", errors="replace")
        if mime == "text/html":
            from ..integrations.hubspot import _html_to_text

            return _html_to_text(text)
        return text
    for child in part.get("parts") or ():
        found = _plain_text(child)
        if found:
            return found
    return ""


def _known_contact(session, addresses: list[str]) -> Contact | None:
    """이 메일에 **우리가 아는 사람**이 있나. 없으면 이 메일은 우리 것이 아닙니다.

    보낸 사람과 받는 사람을 둘 다 봅니다 — 우리 담당자가 고객에게 **보낸** 메일도 그
    고객의 기록이라, 보낸 사람만 보면 절반이 빠집니다.
    """
    for address in addresses:
        row = session.scalar(
            select(Contact).where(Contact.normalized_email == address.strip().lower())
        )
        if row is not None:
            return row
    return None


def _newest_ticket(session, contact_id: int) -> Conversation | None:
    """후보가 여럿이면 **가장 최근 티켓** (2026-09-08 운영자 지시).

    고르게 하지 않는 이유: 고르개를 띄우면 운영자가 매번 판단해야 하고, 그 판단의 답은
    거의 언제나 「지금 진행 중인 그 건」입니다. 틀렸으면 안 누르면 됩니다 — **누르기
    전에는 아무것도 안 붙습니다.**
    """
    return session.scalar(
        select(Conversation)
        .where(
            Conversation.contact_id == contact_id,
            Conversation.hubspot_ticket_id.is_not(None),
        )
        .order_by(Conversation.created_at.desc(), Conversation.id.desc())
        .limit(1)
    )


def _hubspot_already_has_it(session, contact_id: int, when: datetime, direction: str) -> bool:
    """허브스팟이 이미 잡은 메일인가 — **겹치면 허브스팟 것만** (운영자 지시).

    자를 새로 만들지 않고 `ticket_history._merge_crm_twins` 가 같은 메일의 두 사본을
    알아볼 때 쓰는 규칙을 그대로 씁니다: **같은 연락처 · 같은 초 · 같은 방향.** 한
    연락처에서 같은 초에 다른 메일이 둘 오갈 일은 없습니다.

    Message-ID 로 맞추면 더 정확하겠지만, 허브스팟 쪽 줄에 그 값이 **없습니다** — 그걸
    담으려면 이미 들어온 수천 줄을 다시 받아야 합니다.
    """
    from .ticket_history import _same_direction

    stamp = when.replace(microsecond=0, tzinfo=None)
    rows = session.scalars(
        select(CustomerInteraction).where(
            CustomerInteraction.contact_id == contact_id,
            CustomerInteraction.happened_at.is_not(None),
        )
    ).all()
    return any(
        row.happened_at.replace(microsecond=0) == stamp
        and _same_direction(row.direction) == direction
        and not (row.external_id or "").startswith("gmail:")
        for row in rows
    )


def _sync_one(email: str) -> int:
    """사서함 하나. 새로 넣은 줄 수를 돌려줍니다."""
    from .ticket_history import is_our_address

    with SessionLocal() as session:
        from ..db.models import MailboxAccount as _Account

        account = session.get(_Account, email)
        since = account.collect_from if account else None
    if since is None:
        return 0

    token = access_token(email)
    headers = {"Authorization": f"Bearer {token}"}
    # `after:` 는 초 단위 epoch 를 받습니다 — 날짜로만 주면 그날 것이 통째로 들어옵니다.
    query = f"after:{int(since.replace(tzinfo=timezone.utc).timestamp())}"
    with httpx.Client(headers=headers, timeout=_TIMEOUT) as client:
        listing = client.get(
            f"{_API}/messages", params={"q": query, "maxResults": MESSAGES_PER_SWEEP}
        )
        listing.raise_for_status()
        ids = [item["id"] for item in (listing.json().get("messages") or [])]
        if not ids:
            return 0

        with SessionLocal() as session:
            known = set(
                session.scalars(
                    select(CustomerInteraction.external_id).where(
                        CustomerInteraction.external_id.in_([f"gmail:{i}" for i in ids])
                    )
                ).all()
            )

        added = 0
        for message_id in ids:
            external_id = f"gmail:{message_id}"
            if external_id in known:
                continue
            detail = client.get(f"{_API}/messages/{message_id}", params={"format": "full"})
            if detail.is_error:
                logger.warning("메일 %s 를 못 읽었습니다 (%s)", message_id, email)
                continue
            body = detail.json()
            payload = body.get("payload") or {}
            head = _headers(payload)
            people = [
                address
                for _n, address in getaddresses(
                    [head.get("from", ""), head.get("to", ""), head.get("cc", "")]
                )
                if address
            ]
            sender = next(
                (a for _n, a in getaddresses([head.get("from", "")]) if a), ""
            )
            direction = "outgoing" if is_our_address(sender) else "inbound"
            try:
                when = parsedate_to_datetime(head.get("date", ""))
            except Exception:
                when = datetime.fromtimestamp(
                    int(body.get("internalDate", 0)) / 1000, tz=timezone.utc
                )
            when = (when or datetime.now(timezone.utc)).astimezone(timezone.utc)

            with SessionLocal() as session:
                # **우리가 아는 사람이 아니면 버립니다.** 개인함에는 회사 공지도 광고도
                # 옵니다 — 다 긁어오면 리드 히스토리가 그 사람의 받은편지함이 됩니다.
                contact = _known_contact(
                    session, [a for a in people if not is_our_address(a)]
                )
                if contact is None:
                    continue
                if _hubspot_already_has_it(session, contact.id, when, direction):
                    continue
                session.add(CustomerInteraction(
                    contact_id=contact.id,
                    # **티켓은 아직 안 붙입니다** — 사람이 누르면 이 칸이 찹니다.
                    conversation_id=None,
                    channel="이메일",
                    direction=direction,
                    handler=(sender[:120] or None),
                    subject=(head.get("subject", "").strip()[:300] or None),
                    summary=_plain_text(payload).strip() or body.get("snippet") or "(본문 없음)",
                    # 어느 사서함으로 왔는지. 화면이 「untae@… 으로 왔습니다」를 적습니다.
                    context=f"{email} 개인 메일함",
                    external_id=external_id,
                    happened_at=when.replace(tzinfo=None),
                ))
                session.commit()
                added += 1
    return added


def sync_mailboxes_once() -> dict:
    """켜져 있는 사서함을 한 바퀴. 폴러가 부릅니다.

    **한 사서함이 터져도 나머지는 돕니다.** 토큰이 죽는 것은 사람마다 따로 일어나는
    일이라(비밀번호 변경), 하나 때문에 회차가 통째로 죽으면 안 됩니다.
    """
    added = 0
    for email in enabled_accounts():
        try:
            added += _sync_one(email)
            mark_polled(email)
        except MailboxTokenError as exc:
            # 이유는 이미 행에 적혔습니다(`gmail._mark_broken`). 화면이 그것을 그립니다.
            logger.warning("사서함 %s 를 못 열었습니다: %s", email, exc)
        except Exception:
            logger.warning("사서함 %s 수집 실패", email, exc_info=True)
    if added:
        logger.info("개인 메일함: %d줄 들여왔습니다.", added)
    return {"added": added}


def pending_links(limit: int = 50) -> list[dict]:
    """**연결할까요?** — 아직 아무 티켓에도 안 붙었고 아직 안 물어본 개인함 메일.

    후보 티켓은 **가장 최근 것 하나**입니다(운영자 지시). 연락처는 아는데 티켓이 없는
    메일은 여기 안 뜹니다 — 그건 이미 리드 히스토리의 한 줄이고, 그게 답입니다.
    """
    out: list[dict] = []
    with SessionLocal() as session:
        decided = set(session.scalars(select(MailboxLinkDecision.external_id)).all())
        rows = session.scalars(
            select(CustomerInteraction)
            .where(
                CustomerInteraction.conversation_id.is_(None),
                CustomerInteraction.external_id.like("gmail:%"),
            )
            .order_by(CustomerInteraction.happened_at.desc())
            .limit(limit * 4)
        ).all()
        for row in rows:
            if row.external_id in decided:
                continue
            ticket = _newest_ticket(session, row.contact_id)
            if ticket is None:
                continue
            contact = session.get(Contact, row.contact_id)
            out.append({
                "external_id": row.external_id,
                "mailbox": row.context or "",
                "contact": (contact.full_name if contact else "") or "",
                "contact_email": (contact.email if contact else "") or "",
                "subject": row.subject or "",
                "preview": " ".join((row.summary or "").split())[:160],
                "happened_at": row.happened_at,
                "conversation_id": ticket.id,
                "ticket_id": ticket.hubspot_ticket_id,
                "ticket_subject": ticket.inquiry_subject or "",
            })
            if len(out) >= limit:
                break
    return out


async def decide_link(external_id: str, conversation_id: int | None, by: str) -> None:
    """운영자의 한 번. `conversation_id` 가 있으면 붙이고, 없으면 안 붙이기로 적습니다.

    **거절도 적습니다** — 안 적으면 그 메일이 회차마다 다시 물어봅니다.

    붙일 때 하는 일이 둘입니다: 우리 줄에 티켓을 채우고, **허브스팟 티켓에도 노트로
    남깁니다**(운영자 지시: 「개인 gmail 로 온 거여도 hubspot 에 기록은 남겨야 해」).
    노트인 이유는 이 토큰이 메일 기록(engagement)을 **만들 수 없기** 때문입니다 —
    `sales-email-read` 는 읽기 전용입니다.
    """
    with SessionLocal() as session:
        row = session.scalar(
            select(CustomerInteraction).where(
                CustomerInteraction.external_id == external_id
            )
        )
        if row is None:
            return
        ticket_id = None
        hubspot_contact_id = None
        when = row.happened_at
        if conversation_id is not None:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None or conversation.contact_id != row.contact_id:
                raise ValueError("그 티켓의 연락처가 아닙니다.")
            row.conversation_id = conversation_id
            ticket_id = conversation.hubspot_ticket_id
            contact = session.get(Contact, row.contact_id)
            hubspot_contact_id = contact.hubspot_contact_id if contact else None
        session.merge(MailboxLinkDecision(
            external_id=external_id,
            conversation_id=conversation_id,
            decided_by=by or None,
            decided_at=datetime.now(timezone.utc),
        ))
        note = f"[개인 메일함 {row.context or ''}] {row.subject or ''}\n\n{row.summary or ''}"
        session.commit()

    if conversation_id is not None and ticket_id and hubspot_contact_id:
        await _note_on_ticket(hubspot_contact_id, ticket_id, note, when)


async def _note_on_ticket(hubspot_contact_id: str, ticket_id: str, body: str,
                          when: datetime | None) -> None:
    """허브스팟 티켓에 노트 한 줄 — 「개인 gmail 로 온 거여도 hubspot 에 기록은 남겨야 해」.

    **노트인 이유**: 이 토큰은 메일 기록(engagement)을 **만들 수 없습니다** —
    `sales-email-read` 는 읽기 전용입니다(CLAUDE.md). 노트는 이 앱이 이미 쓰는 길이고,
    `create_interaction_note` 가 연락처에 달고 티켓에도 붙입니다.

    **실패해도 우리 연결은 되돌리지 않습니다.** 운영자가 누른 것은 「이 메일은 이 티켓
    것이다」이고 그 판단은 저쪽에 못 써도 유효합니다 — 되돌리면 같은 메일을 다시
    물어보게 됩니다.
    """
    from ..integrations.hubspot import HubSpotClient

    client = HubSpotClient()
    try:
        await client.create_interaction_note(
            hubspot_contact_id, body[:60_000], happened_at=when, ticket_id=ticket_id
        )
    except Exception:
        logger.warning("티켓 %s 에 노트를 못 남겼습니다", ticket_id, exc_info=True)
    finally:
        await client.close()
