"""티켓 하나의 **모든** 대화를 HubSpot Conversations 에서 가져옵니다 (2026-09-02 운영자 지시).

## 왜 필요했나 — 「계속 빼먹는다」의 정체

빼먹은 것이 아니라 **가져오는 코드가 없었습니다.** `messages` 행을 만드는 곳은 셋뿐이고
(첫 문의 하나 · 워크북 임포트 · 운영자가 쓴 초안), 그중 어느 것도 스레드를 읽지 않았습니다.
스레드 메시지를 읽는 유일한 자리는 발신 경로를 고르는 `find_conversation_reply_context`
인데 **읽고 버렸습니다.** 그 위에 관문이 셋 더 있었습니다:

- `inbound._persist_placeholder` 는 티켓당 고객 메시지를 **첫 통만** 저장합니다.
- 이미 보낸 회신이 있으면 그 이벤트를 통째로 버립니다(행도 기록도 없이).
- New 단계가 아니면 건너뜁니다 — 운영 327건이 전부 New 를 지났으므로 사실상 전면 차단.
- 웹훅에 conversation 구독이 없어, 고객이 답장해도 이벤트 자체가 안 옵니다.

실측(2026-09-02): 티켓 **327건 중 133건**에서 고객 메시지 **627건**이 없었고, 채팅 채널
메시지 **896건**은 기존 경로(연락처별 CRM 이메일)로는 아예 안 보였습니다.

## 어디에 넣나 — `customer_interactions`

`messages` 가 아닙니다. 그 표는 초안·승인·발송 기계가 상태로 쓰는 자리라, 지난 대화를
행으로 부어 넣으면 「보낸 적 있는 회신」 판정과 발송 큐가 같이 흔들립니다. 접점 기록은
**읽기 전용 타임라인**이고 `conversation_id` 가 있어서, 넣는 즉시 티켓 상세의 그 티켓
칸에 뜹니다.

`external_id` 가 `hubspot:conv:<메시지 id>` 라 **몇 번을 다시 돌려도 중복이 안 생깁니다** —
지우고 새로 받을 필요가 없습니다(이관 0106 이 그 칸에 유니크를 겁니다).

## 실측이 가르쳐 준 것 넷 — 이 규칙들은 추측이 아닙니다

1. **페이징을 「페이지가 안 찼으면 끝」으로 판단하면 안 됩니다.** `limit=2` 요청이 결과
   1건과 `paging.next.after` 를 **같이** 돌려줍니다. 커서가 없을 때만 끝입니다.
2. **티켓 하나에 스레드가 여럿입니다** — 60건 중 41건이 2개 이상, 최대 7개. 첫 스레드만
   읽으면 나머지가 통째로 빠집니다.
3. **본문이 잘려 옵니다** — 265건 중 46건이 `TRUNCATED_TO_MOST_RECENT_REPLY` 이고 목록이
   준 것은 앞부분뿐입니다(실측 174자 대 원본 1,670자). 그때만 `original-content` 를 부릅니다.
4. **방향은 보낸 주소가 정합니다** (운영자 규칙). HubSpot 의 `direction` 을 믿으면 우리
   영업이 자기 메일함에서 답한 **35건**이 「고객 메시지」가 됩니다. 다만 규칙을
   `@estsoft.com` 하나로 두면 `support@perso.ai` 발신 **60건**이 고객으로 뒤집히고, 발신
   주소가 아예 없는 채팅·봇 메시지 **896건**은 분류가 안 됩니다 — 그래서 도메인 목록을
   넓히고 actorId 접두사를 보조로 씁니다(`V-` 방문자 = 고객, `A-`·`B-` 상담원·봇 = 우리).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select

from ..db.models import Conversation, CustomerInteraction
from ..integrations.hubspot import _BULK_PACE_SECONDS
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)

# 우리 쪽 주소. **`perso.ai` 가 없으면 `support@perso.ai` 발신 60건이 고객으로 뒤집힙니다**
# (실측). `*.hs-inbox.com` · `*.hubspot-inbox.com` 은 허브스팟이 인박스마다 발급하는 우리
# 전달 주소입니다 — 도메인만 보면 남의 것처럼 생겼지만 우리가 보낸 것입니다.
OUR_DOMAINS = ("estsoft.com", "perso.ai", "perso.co.kr")
OUR_DOMAIN_SUFFIXES = ("hs-inbox.com", "hubspot-inbox.com")

# 한 회차에 처리할 티켓 수. 티켓 하나가 스레드 1~7개 × 메시지 목록이라 호출이 여러 번이고,
# 10분마다 도는 자리라 서두를 이유가 없습니다 — 운영자 지시도 「천천히 해도 되니 완벽하게」.
TICKETS_PER_SWEEP = 8


def _is_ours(address: str) -> bool:
    domain = address.rsplit("@", 1)[-1].strip().lower()
    if not domain:
        return False
    if any(domain == d or domain.endswith("." + d) for d in OUR_DOMAINS):
        return True
    return domain.endswith(OUR_DOMAIN_SUFFIXES)


def _addresses(parties) -> list[str]:
    """그 쪽(senders/recipients)의 이메일 주소들. 응답 모양 둘을 다 받습니다."""
    out: list[str] = []
    for party in parties or ():
        if not isinstance(party, dict):
            continue
        identifiers = party.get("deliveryIdentifiers") or []
        if party.get("deliveryIdentifier"):
            identifiers = [party["deliveryIdentifier"]]
        for identifier in identifiers:
            if not isinstance(identifier, dict):
                continue
            if identifier.get("type") != "HS_EMAIL_ADDRESS":
                continue
            value = str(identifier.get("value") or "").strip().lower()
            if value:
                out.append(value)
    return out


def classify_direction(message: dict) -> str:
    """이 메시지가 **우리가 보낸 것**인지 **고객이 보낸 것**인지.

    운영자 규칙: 보낸 주소가 우리 도메인이면 우리, 그 외는 고객. HubSpot 의 `direction`
    필드보다 이쪽이 정확합니다 — 우리 영업이 자기 메일함에서 답한 것을 HubSpot 은
    INCOMING 으로 적습니다(실측 35건).

    주소가 없는 채팅·봇 메시지(실측 896건)는 주소로 못 가리므로 actorId 접두사를 봅니다:
    `V-` 는 방문자라 고객, `A-`(상담원)·`B-`(봇)는 우리입니다. 그것도 없으면 그때서야
    HubSpot 이 적어 준 방향으로 떨어집니다.
    """
    # **폼 제출은 언제나 받은 것입니다.** 채널이 곧 방향입니다 — 폼은 고객이 우리에게
    # 내는 것이지 우리가 보내는 통로가 아닙니다. 이 줄이 없으면 우리 직원 주소로 제출된
    # 폼이 「우리가 보낸 것」이 됩니다(실측: 티켓 330705398519 의 `mina14@estsoft.com`).
    if str(message.get("channelId") or "") == "1003":
        return "inbound"
    senders = message.get("senders") or []
    for address in _addresses(senders):
        return "outgoing" if _is_ours(address) else "inbound"
    for party in senders:
        actor = str((party or {}).get("actorId") or "")
        if actor.startswith("V-"):
            return "inbound"
        if actor.startswith(("A-", "B-")):
            return "outgoing"
    return "inbound" if str(message.get("direction") or "") == "INCOMING" else "outgoing"


def _happened_at(message: dict) -> datetime:
    raw = str(message.get("createdAt") or "")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def _channel_label(message: dict) -> str:
    """워크북·화면이 쓰는 채널 말. 숫자를 그대로 두면 화면에 `1000` 이 뜹니다."""
    return {"1000": "채팅", "1002": "이메일", "1003": "폼"}.get(
        str(message.get("channelId") or ""), "기타"
    )


async def _thread_messages(client, thread_id: str) -> list[dict]:
    """그 스레드의 **모든** 메시지. 커서가 없을 때까지 넘깁니다.

    「받아온 수가 limit 보다 적으면 끝」으로 판단하면 안 됩니다 — 실측으로 `limit=2` 요청이
    결과 1건과 `paging.next.after` 를 같이 돌려줍니다.
    """
    out: list[dict] = []
    after: str | None = None
    while True:
        params: dict[str, object] = {"limit": 100}
        if after:
            params["after"] = after
        page = await client._get_conversation_json(
            f"/conversations/v3/conversations/threads/{thread_id}/messages",
            params=params,
            action=f"thread {thread_id} history",
        )
        out.extend(page.get("results") or [])
        after = ((page.get("paging") or {}).get("next") or {}).get("after")
        if not after:
            return out


async def _full_text(client, thread_id: str, message: dict) -> str:
    """본문. 잘렸다고 적혀 있을 때만 원본을 따로 받아옵니다.

    목록이 주는 `text` 는 스레드의 마지막 답장만 남긴 조각일 수 있습니다(실측 265건 중
    46건). 화면에는 잘렸다는 표시가 없어서, 그대로 두면 반쪽짜리 기록이 남습니다.
    """
    text = str(message.get("text") or "").strip()
    if str(message.get("truncationStatus") or "") != "TRUNCATED_TO_MOST_RECENT_REPLY":
        return text
    message_id = str(message.get("id") or "")
    if not message_id:
        return text
    try:
        full = await client._get_conversation_json(
            f"/conversations/v3/conversations/threads/{thread_id}/messages/{message_id}"
            "/original-content",
            action=f"message {message_id} original content",
        )
    except Exception:
        # 원본을 못 받아도 조각이라도 남깁니다 — 없는 것보다 낫고, 다음 회차에 다시 시도합니다.
        logger.warning("원본 본문을 못 받았습니다 (thread=%s msg=%s)", thread_id, message_id)
        return text
    return str(full.get("text") or full.get("richText") or text).strip() or text


async def collect_ticket_history(client, ticket_id: str) -> list[dict]:
    """그 티켓의 모든 스레드 × 모든 메시지를, 접점 기록 한 줄씩으로.

    읽기만 합니다 — 이 함수로는 아무것도 나가지 않습니다.
    """
    threads: list[dict] = []
    after: str | None = None
    while True:
        params: dict[str, object] = {
            "associatedTicketId": ticket_id,
            "association": "TICKET",
            "limit": 100,
        }
        if after:
            params["after"] = after
        page = await client._get_conversation_json(
            "/conversations/v3/conversations/threads",
            params=params,
            action=f"ticket {ticket_id} threads",
        )
        threads.extend(page.get("results") or [])
        after = ((page.get("paging") or {}).get("next") or {}).get("after")
        if not after:
            break

    rows: list[dict] = []
    for thread in threads:
        # 보관·스팸 스레드는 건너뜁니다 — 화면에서 치운 대화를 되살릴 이유가 없습니다.
        if thread.get("archived") or thread.get("spam"):
            continue
        thread_id = str(thread.get("id") or "")
        if not thread_id:
            continue
        for message in await _thread_messages(client, thread_id):
            if message.get("type") != "MESSAGE":
                continue
            message_id = str(message.get("id") or "")
            if not message_id:
                continue
            text = await _full_text(client, thread_id, message)
            senders = _addresses(message.get("senders"))
            rows.append({
                "external_id": f"hubspot:conv:{message_id}",
                "channel": _channel_label(message),
                "direction": classify_direction(message),
                # `customer_interactions.subject` 는 300자입니다. 안 자르면 긴 제목 하나가
                # DataError 로 그 회차를 통째로 죽입니다 — 그리고 그 티켓은 영영 안 들어옵니다.
                "subject": (str(message.get("subject") or "").strip()[:300] or None),
                "summary": text or "(본문 없음)",
                "handler": (senders[0][:120] if senders else None),
                "happened_at": _happened_at(message),
            })
    rows.sort(key=lambda row: row["happened_at"])
    return rows


def _store(conversation_id: int, contact_id: int, rows: list[dict]) -> int:
    """새 기록만 넣습니다. 이미 있는 것은 건드리지 않습니다 — 몇 번을 돌려도 같은 결과."""
    added = 0
    with SessionLocal() as session:
        # **티켓으로 좁히면 안 됩니다.** 유니크 인덱스(0106)는 `external_id` 하나에만
        # 걸려 있어서, 같은 메시지가 **다른 티켓 밑에 또는 티켓 없이** 이미 들어 있으면
        # 여기서는 안 보이고 commit 에서 터집니다. 그리고 한 번에 commit 하므로 **그 티켓의
        # 멀쩡한 행까지 같이 날아가고, 도장도 안 찍혀 다음 회차에 또 터집니다.**
        # (재현됨. 도달 경로: `hubspot_reconcile` 이 티켓이 파이프라인 밖으로 나갈 때
        # `conversation_id` 를 NULL 로 만들고 대화를 지웁니다 — 그 행들이 고아로 남고,
        # 티켓이 되돌아오면 새 대화 id 로 다시 넣다가 부딪힙니다.)
        wanted = [row["external_id"] for row in rows]
        known = set(
            session.scalars(
                select(CustomerInteraction.external_id).where(
                    CustomerInteraction.external_id.in_(wanted)
                )
            ).all()
        ) if wanted else set()
        for row in rows:
            if row["external_id"] in known:
                continue
            session.add(CustomerInteraction(
                contact_id=contact_id, conversation_id=conversation_id, context=None, **row
            ))
            known.add(row["external_id"])
            added += 1
        conversation = session.get(Conversation, conversation_id)
        if conversation is not None:
            conversation.history_synced_at = datetime.now(timezone.utc)
        session.commit()

    # ------------------------------------------------------------------ #
    # **`last_incoming_at` 은 건드리지 않습니다.** 한 번 채웠다가 지웠습니다.
    #
    # 그 칸은 「마지막 고객 연락 시각」처럼 보이지만 실제로는 **워크북 append 대기열의
    # 방아쇠**입니다: `sheet_sync.sync_pending_inbound_rows` 가 매 폴러 회차마다
    # `sheet_inbound_row IS NULL AND last_incoming_at IS NOT NULL` 을 고릅니다. 백필이
    # 만든 300건 넘는 티켓은 그 칸이 일부러 NULL 인데(`hubspot_backfill` 모듈 docstring 이
    # 그 이유를 적어 두었습니다), 이 수집기가 채우면 **그 전부가 영업팀 공용 워크북에
    # 한꺼번에 실려 나갑니다.** 운영은 `LIVE_SHEETS_WRITES=true` 입니다.
    #
    # 히스토리를 보이게 하는 데 그 칸은 필요 없습니다 — 화면은 접점 기록을 읽습니다.
    # ------------------------------------------------------------------ #
    return added


def _stamp(conversation_id: int) -> None:
    """「이번 회차에 봤다」는 도장. 성공·실패 둘 다 찍습니다 — 굶기지 않기 위해서."""
    with SessionLocal() as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is not None:
            conversation.history_synced_at = datetime.now(timezone.utc)
            session.commit()


async def sync_one_ticket(conversation_id: int) -> int:
    """티켓 하나의 히스토리를 맞춥니다. 넣은 기록 수를 돌려줍니다."""
    from ..integrations.hubspot import HubSpotClient

    with SessionLocal() as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            return 0
        ticket_id = (conversation.hubspot_ticket_id or "").strip()
        contact_id = conversation.contact_id
    if not ticket_id or not contact_id:
        # 티켓이나 연락처가 없으면 가져올 자리가 없습니다. 다시 고르지 않게 도장은 찍습니다.
        _stamp(conversation_id)
        return 0

    client = HubSpotClient()
    try:
        rows = await collect_ticket_history(client, ticket_id)
    finally:
        await client.close()
    return _store(conversation_id, contact_id, rows)


async def sync_pending_ticket_history(limit: int = TICKETS_PER_SWEEP) -> dict:
    """아직 안 받았거나 가장 오래 전에 받은 티켓부터 조금씩. 10분 폴러가 부릅니다.

    **이어하기가 곧 순서입니다**: `history_synced_at` 이 NULL 인 것이 먼저(아직 한 번도 안
    받은 티켓), 그 다음은 오래된 순. 도중에 배포가 나가도 다음 회차가 그 자리에서
    계속합니다 — 어디까지 했는지 따로 적어 둘 곳이 필요 없습니다.

    한 바퀴를 다 돌면 가장 오래된 것부터 다시 도므로, **새로 쌓인 대화도 저절로
    들어옵니다.** 급한 티켓은 `mark_ticket_history_stale` 이 맨 앞으로 올립니다.
    """
    from ..integrations.hubspot import HubSpotNotConfigured

    with SessionLocal() as session:
        pending = session.scalars(
            select(Conversation.id)
            .where(Conversation.hubspot_ticket_id.isnot(None))
            .order_by(
                Conversation.history_synced_at.is_(None).desc(),
                Conversation.history_synced_at.asc(),
                Conversation.id.asc(),
            )
            .limit(max(1, limit))
        ).all()
    done = added = failed = 0
    for conversation_id in pending:
        try:
            added += await sync_one_ticket(conversation_id)
            done += 1
        except HubSpotNotConfigured:
            break
        except Exception:
            # **실패해도 도장은 찍습니다.** 안 찍으면 그 티켓이 다음 회차에도 맨 앞에 오고,
            # 계속 실패하는 티켓이 8건이면 **나머지는 영영 순서가 안 옵니다**(재현됨:
            # 12건 중 8건이 실패하니 나머지 4건은 한 번도 안 불렸습니다). 도장을 찍으면
            # 큐의 맨 뒤로 가고, 한 바퀴 뒤에 다시 시도합니다 — 재시도는 유지하면서
            # 굶기지는 않습니다.
            failed += 1
            logger.warning("티켓 히스토리 동기화 실패 (conversation=%s)", conversation_id,
                           exc_info=True)
            _stamp(conversation_id)
    if done or added or failed:
        logger.info("티켓 히스토리: %d건 처리, 기록 %d개 추가, 실패 %d건", done, added, failed)
    return {"tickets": done, "added": added, "failed": failed}


def run_pending_ticket_history(limit: int = TICKETS_PER_SWEEP) -> dict:
    """10분 폴러가 부르는 **동기** 진입점.

    **이 함수가 없으면 아무것도 안 돕니다.** 폴러는 단계를 `asyncio.to_thread(run)` 로
    돌립니다 — 그 자리에 `async def` 를 두면 코루틴 객체만 만들어지고 **실행되지 않은 채**
    버려집니다(경고 한 줄도 로그에 안 남을 수 있습니다). 배포하고 나서 「왜 아무 일도 안
    일어나지」가 되는 종류의 실수라, 진입점을 따로 둡니다.

    `asyncio.run` 이 안전한 이유: 폴러가 이미 별도 스레드로 넘겨 주므로 그 스레드에는
    도는 이벤트 루프가 없습니다.
    """
    import asyncio

    return asyncio.run(sync_pending_ticket_history(limit))


def mark_ticket_history_stale(conversation_id: int) -> None:
    """이 티켓을 다음 회차의 맨 앞으로. 방금 무언가 오간 티켓에 씁니다.

    한 바퀴가 도는 데 걸리는 시간을 기다리지 않게 하는 장치입니다 — 단계가 움직였다는 것은
    대개 대화가 오갔다는 뜻입니다.
    """
    with SessionLocal() as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is not None and conversation.history_synced_at is not None:
            conversation.history_synced_at = None
            session.commit()


__all__ = [
    "attach_personal_emails",
    "classify_direction",
    "run_pending_ticket_history",
    "collect_ticket_history",
    "mark_ticket_history_stale",
    "sync_one_ticket",
    "sync_pending_ticket_history",
]


# --------------------------------------------------------------------------- #
# 개인 사서함으로 오간 메일을 티켓에 붙이기 (2026-09-02 운영자 요청)
# --------------------------------------------------------------------------- #
# **실측이 이 기능의 근거입니다.** 담당자가 자기 메일(`untae@estsoft.com` 등)로 고객에게
# 답하면 허브스팟이 그 메일을 **연락처에는 기록하지만 티켓에는 안 붙입니다** — 운영 표본
# 5건 전부가 그랬습니다(연락처 연결 5/5, 티켓 연결 0/5). 그래서 그 대화가 티켓 화면에서
# 사라집니다.
#
# 여기서 하는 일은 **기록을 만드는 것이 아니라 이어 붙이는 것**입니다. 허브스팟이 이미
# 만들어 둔 이메일 기록을 그 연락처의 티켓에 연결합니다.
#
# **티켓이 하나일 때만 붙입니다.** 그 연락처에 열린 티켓이 여럿이면 어느 대화인지 우리가
# 알 수 없고, 잘못 붙이면 남의 티켓에 남의 메일이 섭니다 — 붙이는 것은 되돌릴 수 있지만
# 아무도 그것이 잘못 붙었다는 것을 모릅니다. 담당자 가이드가 적어 둔 규칙과 같습니다.


async def attach_personal_emails(conversation_id: int) -> dict:
    """그 티켓의 연락처가 개인 사서함으로 주고받은 메일을 티켓에 붙입니다.

    돌려주는 값: 살펴본 수 · 붙인 수 · 이미 붙어 있던 수 · 안 붙인 이유.
    """
    from ..integrations.hubspot import HubSpotClient
    from ..db.models import Conversation as _Conversation

    with SessionLocal() as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            return {"looked": 0, "attached": 0, "already": 0, "skipped": "대화 없음"}
        ticket_id = (conversation.hubspot_ticket_id or "").strip()
        contact = conversation.contact
        contact_id = conversation.contact_id
        hubspot_contact_id = (contact.hubspot_contact_id or "").strip() if contact else ""
        # **그 연락처에 티켓이 하나일 때만 붙입니다.** 여럿이면 그 메일이 어느 대화의
        # 것인지 우리가 알 수 없고, 잘못 붙으면 남의 티켓에 남의 메일이 서는데 **아무도
        # 그것이 잘못됐다는 것을 모릅니다.** 담당자 가이드가 적어 둔 규칙과 같습니다.
        ticket_count = session.scalar(
            select(func.count())
            .select_from(_Conversation)
            .where(
                _Conversation.contact_id == contact_id,
                _Conversation.hubspot_ticket_id.isnot(None),
            )
        ) or 0
    if not ticket_id or not hubspot_contact_id:
        return {"looked": 0, "attached": 0, "already": 0, "skipped": "티켓·연락처 정보 없음"}
    if ticket_count != 1:
        return {
            "looked": 0, "attached": 0, "already": 0,
            "skipped": f"이 연락처에 티켓이 {ticket_count}건 — 어느 대화인지 정할 수 없습니다",
        }

    client = HubSpotClient()
    looked = attached = already = 0
    try:
        for email_id in await client.emails_for_contact(hubspot_contact_id):
            looked += 1
            tickets = await client.email_ticket_ids(email_id)
            if ticket_id in tickets:
                already += 1
                continue
            if tickets:
                # 이미 **다른** 티켓의 것입니다. 건드리지 않습니다.
                continue
            await client.attach_email_to_ticket(email_id, ticket_id)
            attached += 1
            # 호출을 서로 붙여 쏘면 사설 앱 한도(약 100회/10초)에 걸립니다. 이 저장소가
            # 대량 조회에서 쓰는 것과 같은 간격입니다.
            await asyncio.sleep(_BULK_PACE_SECONDS)
    finally:
        await client.close()
    if attached:
        logger.info(
            "개인 사서함 메일 %d건을 티켓 %s 에 붙였습니다 (살펴본 %d건, 이미 붙어 있던 %d건).",
            attached, ticket_id, looked, already,
        )
    return {"looked": looked, "attached": attached, "already": already, "skipped": ""}
