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
4. **티켓 연결은 자동입니다** (2026-09-09 지시: 「확인 안 누르고 최신 티켓 혹은 최신
   수주로 들어가게」). 넣으면서 그 고객의 **가장 최근 기록**에 붙입니다 — 단계도 허브스팟
   번호도 안 봅니다. 붙을 자리가 아예 없는 고객만 「티켓 외」로 남습니다.
5. **우리가 보낸 사본은 안 가져옵니다.** `perso.ai@estsoft.com` 은 허브스팟 이메일 채널
   계정이라 콘솔에서 나간 회신이 그 사서함에 그대로 남습니다 — 안 거르면 우리 답장이
   티켓 기록에 두 번 섭니다(`_we_already_sent_it`).

**잘못 들어온 줄은 티켓 화면에서 지웁니다**(`customer_ops.interaction_delete`). 지울 때
`mailbox_link_decisions` 에 묘비를 남기므로 다음 회차에 되살아나지 않습니다 — 그 표는
원래 「물어본 적 있다」였고, 확인 단계가 없어지면서 뜻만 바뀌었습니다.
"""

from __future__ import annotations

import asyncio

import base64
import logging
from datetime import datetime, timedelta, timezone
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
# 창을 조금 겹칩니다 — 경계에 걸친 메일을 놓치지 않게. 다시 읽는 것은 무해합니다.
_SWEEP_OVERLAP = timedelta(minutes=5)
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


def _newest_conversation(session, contact_id: int) -> Conversation | None:
    """이 고객의 **가장 최근 기록** — 그 메일이 붙을 자리 (2026-09-09 운영자 지시).

    「티켓, 수주 등 아무거나 최신으로 기록을 추가하도록」이 그 지시입니다. 그래서 단계도
    안 보고 **허브스팟 티켓 번호가 있는지도 안 봅니다.**

    **번호를 요구하던 것이 실제 사고였습니다** (2026-09-09). 예전에는
    `hubspot_ticket_id IS NOT NULL` 이 붙어 있어서, 워크북에서만 사는 문의나 번호가 아직
    안 달린 대화밖에 없는 고객은 후보가 **0개**가 됐습니다. 그러면 개인함으로 온 메일이
    티켓에 안 붙고 「티켓 외」로 떨어졌고, 화면에서는 그것이 「무관한 연락」처럼 보였습니다.
    붙을 자리가 있는데 못 붙은 것이라 그건 정보가 아니라 손실입니다.

    후보가 여럿이면 가장 최근 것입니다. 고르개를 안 띄우는 이유: 답이 거의 언제나
    「지금 진행 중인 그 건」이고, 매번 묻는 것은 그 판단을 사람에게 떠넘기는 것입니다.
    """
    return session.scalar(
        select(Conversation)
        .where(Conversation.contact_id == contact_id)
        .order_by(Conversation.created_at.desc(), Conversation.id.desc())
        .limit(1)
    )


def _we_already_sent_it(session, contact_id: int, when: datetime) -> bool:
    """콘솔에서 **우리가 보낸 회신**이 개인함에 사본으로 돌아온 것인가 (2026-09-09).

    운영자가 잡은 실제 사고: 사이트에서 써서 허브스팟으로 나간 답장(`RE: Test Custom
    Quote`)이 `perso.ai@estsoft.com` 개인함에도 남아, 같은 글이 티켓 기록에 **두 번**
    섰습니다. 그 주소가 곧 허브스팟 이메일 채널 계정이라 나가는 메일이 전부 그 사서함을
    지납니다 — 개인함 수집을 켜는 순간 우리 회신이 통째로 되돌아옵니다.

    **`_hubspot_already_has_it` 로는 못 잡습니다.** 그쪽은 `customer_interactions` 만
    보고 **같은 초**를 요구하는데, 우리 회신은 `messages` 에 있고 지메일이 찍는 시각은
    허브스팟이 보낸 시각과 몇 초 어긋납니다.

    그래서 우리 발송 기록을 봅니다. 창을 넉넉히(±10분) 잡는 이유: 맞히려는 것이 「이
    고객에게 그 무렵 우리가 보낸 답장」이고, 한 고객에게 10분 안에 답장을 두 번 보내는
    일은 없습니다. 좁게 잡아 놓치면 대가가 **중복 표시**이고, 그건 나중에 읽는 사람이
    「답을 두 번 보냈다」로 셉니다.

    **`hubspot_message_id` 가 있는 것만 셉니다** — 실제로 나간 것이라는 뜻입니다. 초안과
    거절한 글은 고객에게 안 갔으므로 개인함에 사본이 있을 수 없습니다.
    """
    from ..db.models import Message

    window = timedelta(minutes=10)
    stamp = when.replace(tzinfo=None)
    return session.scalar(
        select(Message.id)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Conversation.contact_id == contact_id,
            Message.direction == "outgoing",
            Message.hubspot_message_id.is_not(None),
            Message.sent_at.is_not(None),
            Message.sent_at >= stamp - window,
            Message.sent_at <= stamp + window,
        )
        .limit(1)
    ) is not None


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


def _sync_one(email: str) -> tuple[int, list[tuple[str, str, str, datetime]]]:
    """사서함 하나. (새로 넣은 줄 수, 허브스팟에 남길 노트들)."""
    from .ticket_history import is_our_address

    notes: list[tuple[str, str, str, datetime]] = []
    with SessionLocal() as session:
        from ..db.models import MailboxAccount as _Account

        account = session.get(_Account, email)
        if account is None or account.collect_from is None:
            return 0, notes
        # **묻는 창은 「마지막으로 본 이후」입니다** (2026-09-08).
        #
        # 「동의 이후」로 물으면 창이 날마다 넓어지는데 한 회차에 받는 것은
        # `MESSAGES_PER_SWEEP` 통뿐이라, 한 창에 그보다 많이 오면 **넘친 것이 영영 안
        # 들어옵니다** — 다음 회차도 같은 조건으로 물어 같은 쪽만 돌려받기 때문입니다.
        # 그리고 빠졌다는 표시가 아무 데도 안 남습니다. 개인함에 광고가 한꺼번에 쏟아지면
        # 실제로 나는 일입니다.
        #
        # 도장은 **수집이 성공했을 때만** 찍히므로(`mark_polled`) 실패한 회차의 메일도
        # 안 놓칩니다. 연락처 스윕이 쓰는 것과 같은 방식이고, 겹침을 두는 이유도 같습니다 —
        # 경계에 걸친 메일을 놓치지 않게. 다시 읽는 것은 무해합니다(`external_id` 가
        # 유니크라 같은 메일이 두 줄이 안 됩니다).
        since = account.collect_from
        if account.last_polled_at is not None:
            since = max(since, account.last_polled_at - _SWEEP_OVERLAP)

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
            return 0, notes

        candidates = [f"gmail:{i}" for i in ids]
        with SessionLocal() as session:
            known = set(
                session.scalars(
                    select(CustomerInteraction.external_id).where(
                        CustomerInteraction.external_id.in_(candidates)
                    )
                ).all()
            )
            # **운영자가 지운 메일은 다시 안 가져옵니다** (2026-09-09). 행을 지우면 그
            # `external_id` 가 위 목록에서 사라지므로, 묘비가 없으면 **다음 회차에 그대로
            # 되살아납니다** — 지우기가 10분짜리가 되는 셈입니다.
            known |= set(
                session.scalars(
                    select(MailboxLinkDecision.external_id).where(
                        MailboxLinkDecision.external_id.in_(candidates)
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
            # **참조로만 온 메일은 안 가져옵니다** (2026-09-09 운영자 지시).
            #
            # 참조는 「알아 두라」는 뜻이지 이 사람이 그 대화의 당사자라는 뜻이 아닙니다.
            # 사내 공유·전체 회신에 담당자가 얹혀 있는 일이 흔한데, 그것까지 담으면 그
            # 고객의 티켓 기록에 우리끼리 주고받은 줄이 섞입니다.
            #
            # 기준은 **이 사서함 주소가 보낸사람이나 받는사람에 있나**입니다 — 참조 칸에만
            # 있으면 건너뜁니다. 상대가 누구인지(`_known_contact`)는 참조까지 봐서 찾습니다:
            # 그건 「이 메일이 누구 이야기인가」이고, 여기서 묻는 것은 「우리가 당사자인가」라
            # 서로 다른 물음입니다.
            direct = {
                address.strip().lower()
                for _n, address in getaddresses([head.get("from", ""), head.get("to", "")])
                if address
            }
            if email.strip().lower() not in direct:
                continue
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
                # 사이트에서 써서 나간 우리 답장이 이 사서함에 사본으로 남습니다 —
                # 그 주소가 곧 허브스팟 이메일 채널 계정이기 때문입니다.
                if direction == "outgoing" and _we_already_sent_it(session, contact.id, when):
                    continue
                # **붙일 자리가 있으면 그 자리에 넣습니다** (2026-09-09 운영자 지시:
                # 「최신 티켓이 있으면 무조건 거기다가 넣도록」). 예전에는 언제나 비워
                # 두고 화면에서 사람이 누르기를 기다렸는데, 그 사이 그 메일은 「티켓 외」에
                # 서서 무관한 연락처럼 보였습니다 — 붙을 자리를 아는데도 그랬습니다.
                #
                # **아직 아무 기록도 없는 고객은 그대로 비어 있습니다** — 붙일 자리가
                # 없다는 뜻이고, 그때는 고객 상세의 「티켓 외」에 남습니다.
                conversation = _newest_conversation(session, contact.id)
                session.add(CustomerInteraction(
                    contact_id=contact.id,
                    conversation_id=conversation.id if conversation else None,
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
                # **허브스팟에도 남깁니다** — 「개인 gmail 로 온 거여도 hubspot 에 기록은
                # 남겨야 해」(운영자). 예전에는 운영자가 「연결할까요?」를 누를 때 했는데,
                # 그 확인이 없어졌으니(2026-09-09) 붙이는 이 자리로 왔습니다. 커밋 뒤에
                # 모아 두고 세션 밖에서 보냅니다 — 저쪽 왕복을 세션이 붙들고 기다리면 안
                # 됩니다.
                if conversation is not None and conversation.hubspot_ticket_id:
                    notes.append((
                        contact.hubspot_contact_id or "",
                        conversation.hubspot_ticket_id,
                        f"[개인 메일함 {email}] {head.get('subject', '')}\n\n"
                        f"{_plain_text(payload).strip() or body.get('snippet') or ''}",
                        when,
                    ))
    return added, notes


def sync_mailboxes_once() -> dict:
    """켜져 있는 사서함을 한 바퀴. 폴러가 부릅니다.

    **한 사서함이 터져도 나머지는 돕니다.** 토큰이 죽는 것은 사람마다 따로 일어나는
    일이라(비밀번호 변경), 하나 때문에 회차가 통째로 죽으면 안 됩니다.
    """
    added = 0
    notes: list[tuple[str, str, str, datetime]] = []
    for email in enabled_accounts():
        try:
            gained, mine = _sync_one(email)
            added += gained
            notes.extend(mine)
            mark_polled(email)
        except MailboxTokenError as exc:
            # 이유는 이미 행에 적혔습니다(`gmail._mark_broken`). 화면이 그것을 그립니다.
            logger.warning("사서함 %s 를 못 열었습니다: %s", email, exc)
        except Exception:
            logger.warning("사서함 %s 수집 실패", email, exc_info=True)
    if added:
        logger.info("개인 메일함: %d줄 들여왔습니다.", added)
    # **노트는 마지막에, 한 번에.** 수집 중에 보내면 저쪽이 느린 날 사서함 한 바퀴가
    # 그만큼 길어지고, 그 사이 세션이 열려 있습니다. 실패해도 우리 줄은 그대로입니다 —
    # 「이 메일은 이 티켓 것이다」는 저쪽에 못 써도 유효한 판단입니다.
    for hubspot_contact_id, ticket_id, body, when in notes:
        if not hubspot_contact_id:
            continue
        try:
            asyncio.run(_note_on_ticket(hubspot_contact_id, ticket_id, body[:60_000], when))
        except Exception:
            logger.warning("티켓 %s 에 노트를 못 남겼습니다", ticket_id, exc_info=True)
    return {"added": added}


# **「연결할까요?」는 없앴습니다** (2026-09-09 운영자 지시: 「티켓으로 바로 연결하게 해줘
# 확인 안 누르고 최신 티켓 혹은 최신 수주로 들어가게」).
#
# `pending_links` 가 후보를 모으고 `decide_link` 가 운영자의 한 번을 받았는데, 운영자가
# 그 화면을 보고 「좀 애매한 느낌」이라고 했습니다 — 물음에 답하려면 그 티켓이 맞는지
# 판단해야 하는데, 답은 거의 언제나 「가장 최근 그 건」이라 묻는 것 자체가 일이었습니다.
# 이제 `_sync_one` 이 넣으면서 바로 붙입니다.
#
# **`mailbox_link_decisions` 표는 남았고 뜻이 바뀌었습니다** — 「물어본 적 있다」에서
# **「운영자가 지웠다」**로. 지우기가 되살아나지 않게 하는 묘비입니다
# (`customer_ops.interaction_delete` 가 적고 `_sync_one` 이 읽습니다). 표를 새로 만들지
# 않은 이유는 모양이 똑같기 때문입니다: `external_id` 하나가 기본키.


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
