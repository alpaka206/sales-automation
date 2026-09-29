"""Background worker that polls for approved messages and sends them with rate limiting."""

from __future__ import annotations

import asyncio
import logging
import os
import random
from collections import deque
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update

from ..common.config import settings
from ..db.conversation_history import add_progress
from .summaries import append_summary_line
from ..db.models import Contact, Conversation, CustomerInteraction, CustomerProfile, Message
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)

# 발송 뒤 단계를 올려도 되는 출발점. `None`·`initial` 은 아직 아무도 안 만진 값입니다.
# 협상·수주 건은 여기 없습니다 — **앞으로만 갑니다**(고객 답장이 협의 중으로 올린 티켓에
# 한 통 더 보냈다고 Contacted 로 되돌아가면 안 됩니다).
_ADVANCES_FROM = {None, "", "initial", "new", "meeting_link_sent"}

POLL_INTERVAL_SECONDS = 60

# Unique per-process token used as the value of Message.status while a worker holds the row.
# Format: "sending:<pid>:<random>". This makes the atomic UPDATE…WHERE status='approved'
# act as a row-level lock — the loser sees zero affected rows and moves on.
_WORKER_ID = f"sending:{os.getpid()}:{random.randint(1_000_000, 9_999_999)}"

_sent_timestamps: deque[float] = deque()
_daily_count: int = 0
_daily_date: str = ""
_shutdown = False


def _reset_daily_if_needed(now: datetime | None = None) -> None:
    """Reset daily counter at midnight UTC."""
    global _daily_count, _daily_date
    today = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    if today != _daily_date:
        _daily_date = today
        _daily_count = 0


def _daily_limit_reached() -> bool:
    """Check if daily send limit has been reached."""
    if settings.DAILY_SEND_LIMIT <= 0:
        return False
    daily, _minute = _database_send_counts()
    return max(_daily_count, daily) >= settings.DAILY_SEND_LIMIT


def _minute_window_full() -> bool:
    """Check if per-minute rate limit is reached."""
    if settings.SEND_RATE_PER_MINUTE <= 0:
        return False
    now = asyncio.get_event_loop().time()
    while _sent_timestamps and now - _sent_timestamps[0] > 60:
        _sent_timestamps.popleft()
    _daily, minute = _database_send_counts()
    return max(len(_sent_timestamps), minute) >= settings.SEND_RATE_PER_MINUTE


def _record_send() -> None:
    """Record a successful send for rate tracking."""
    global _daily_count
    _sent_timestamps.append(asyncio.get_event_loop().time())
    _daily_count += 1


def get_daily_count() -> int:
    """Return current daily send count (for healthcheck)."""
    _reset_daily_if_needed()
    daily, _minute = _database_send_counts()
    return max(_daily_count, daily)


def _database_send_counts(now: datetime | None = None) -> tuple[int, int]:
    """Persist rate accounting across restarts and the current process."""
    current = now or datetime.now(timezone.utc)
    day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    minute_start = current - timedelta(seconds=60)
    try:
        with SessionLocal() as session:
            daily = session.scalar(
                select(func.count(Message.id)).where(Message.sent_at >= day_start)
            ) or 0
            minute = session.scalar(
                select(func.count(Message.id)).where(Message.sent_at >= minute_start)
            ) or 0
        return int(daily), int(minute)
    except Exception:
        logger.warning("Could not read persisted send quota; using process counters.", exc_info=True)
        return 0, 0


SEND_TRANSIENT_MAX_RETRIES = 3
SEND_LEASE_SECONDS = 15 * 60
POST_SEND_SYNC_MAX_RETRIES = 8


# 다시 해도 안 풀리는 실패 — 설정이 없다. 이것만 남았으면 재시도를 멈춘다(복구 화면에는 남는다).
_PERMANENT_SYNC_ERRORS = (":not_configured", ":missing_client_id")


async def _note_mailbox_send(msg, conv, hubspot_contact_id: str) -> bool:
    """개인 사서함으로 나간 회신을 허브스팟 티켓에 노트로. 남겼거나 이미 있으면 True.

    **개인 사서함 발송은 허브스팟 스레드에 없어서 이 노트가 허브스팟의 유일한 기록입니다**
    (「개인 gmail 로 … hubspot 에 기록은 남겨야 해」). 예전에는 발송 안에서 한 번 쓰고 실패를
    삼켰습니다 — 허브스팟이 그 순간 느리면 노트가 없는데 아무도 몰랐습니다. 발송 뒤 처리로
    옮겨 실패가 `hubspot_note` 로 남고 다른 단계처럼 다시 시도됩니다. 다시 시도해도 두 줄이 안
    되는 것은 적기 전에 그 티켓의 노트를 읽기 때문입니다(`mailbox_sync._note_key` — 개인함
    수집의 노트와 같은 열쇠). 읽기가 실패하면 **안 적고** 다음 회차로 미룹니다.
    """
    from .mailbox_sync import _note_on_ticket

    mailbox = (msg.channel_account_id or "")[6:]
    body = f"[개인 메일함 {mailbox} 에서 발송] {msg.subject or ''}\n\n{msg.body or ''}"[:60_000]
    when = msg.sent_at
    if when is not None and when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)  # DB 는 UTC 를 시간대 없이 돌려줍니다
    # 개인함 수집의 노트와 **같은 함수** — 적기 전에 그 티켓의 노트 · 스레드 · CRM 메일 기록을 읽는다
    # (허브스팟의 지메일 연동이 이 발송을 이미 기록했을 수 있다).
    return await _note_on_ticket(hubspot_contact_id, conv.hubspot_ticket_id, body, when, "outgoing")


async def _post_send_bookkeeping(session, msg, conv, message_id: int, *, moved: bool | None = None) -> None:
    """Best-effort HubSpot side effects after a successful send. Never raises —
    the email already went out, so failures here must not reverse it.

    ``moved`` 는 **이번 발송이 단계를 옮겼나**다(`_send_one` 이 안다). 재시도에서는 모르므로
    None 이고, 그때는 지난 회차에 **실패한 단계만** 다시 한다.
    """
    from ..integrations.google_sheets import is_configured as sheets_configured
    from ..integrations.google_sheets import update_inbound_stage
    from ..integrations.hubspot import HubSpotClient, HubSpotNotConfigured
    from ..integrations.hubspot import move_ticket_stage_after_send

    errors: list[str] = []
    # **이번 발송이 실제로 단계를 옮겼을 때만 미러링합니다** (2026-08-31).
    #
    # `_send_one` 은 `conv.stage` 를 `_ADVANCES_FROM` 으로 걸러 올리는데(협상·수주 건은
    # 그대로 둡니다), 아래 셋 — 허브스팟 티켓 단계 · 연락처 상태 · 프로필과 워크북 — 은
    # 그 조건을 안 보고 언제나 `meeting_link_sent` 를 썼습니다. 자동 초안은 New 티켓에만
    # 생겨서 지금까지 드러나지 않았을 뿐입니다. **후속 회신이 생기는 순간 이건 사고입니다**:
    # 협상 중인 티켓에 한 통 보내면 허브스팟 티켓이 Qualified 로 되돌아가고, 다음 스윕이
    # 그 값을 우리 쪽으로 다시 가져옵니다.
    #
    # **「지금 Contacted」는 「이번에 올렸다」가 아닙니다** (2026-09-29). 한동안 그렇게 읽었는데,
    # 이미 Contacted 인 티켓에 나가는 발송 — 후속 리마인더(2026-09-17 에 뺐다)와 「메일 발송」
    # 후속 회신 — 에서는 거짓이라, 보낼 때마다 허브스팟 티켓을 Contacted 로 다시 PUT 했습니다.
    # 그 사이 영업이 허브스팟에서 Negotiating 으로 옮겼고 우리 쪽이 아직 모르면 되돌립니다. 워크북
    # 오류(`missing_client_id` 처럼 다시 해도 안 풀리는 것)가 있으면 몇 시간에 걸쳐 여덟 번까지
    # 그랬습니다. 그래서 첫 회차는 `_send_one` 이 본 사실(`moved`)을, 재시도는 **실패한 단계만** 본다.
    from .followup_sequence import REMINDER_NOTE_PREFIX, REMINDER_VARIANTS, done_label

    reminder = msg.prompt_variant in REMINDER_VARIANTS
    previous_attempts = msg.post_send_sync_attempts
    previous_attempts = previous_attempts if isinstance(previous_attempts, int) else 0
    first = previous_attempts == 0
    previous = msg.post_send_sync_error or ""
    contacted = bool(conv and conv.stage == "meeting_link_sent" and not reminder)

    def due(step: str) -> bool:
        if not contacted:
            return False  # 그사이 다른 단계로 갔다 — 되돌리지 않는다
        if first:
            # 복구 화면 「나간 것 확인」은 발송 워커를 안 지나 `moved` 를 모른다 — 그 라우트가 방금
            # 올린 것이라 예전 규칙(지금 Contacted)으로 본다.
            return True if moved is None else moved
        # 운영자가 「동기화 재시도」를 눌렀다 — 어느 단계가 실패했는지 그 표시가 덮었으므로 다 한다.
        return previous.startswith("operator_") or step in previous

    ticket_id = conv.hubspot_ticket_id if conv else None
    if due("hubspot_ticket_stage") and ticket_id and not await asyncio.to_thread(
        move_ticket_stage_after_send, ticket_id
    ):
        errors.append("hubspot_ticket_stage")

    hubspot_contact_id = None
    contact = None
    if conv and conv.contact_id:
        contact = session.get(Contact, conv.contact_id)
        hubspot_contact_id = contact.hubspot_contact_id if contact else None
    if (
        (msg.channel_account_id or "").startswith("gmail:") and msg.smtp_message_id
        and ticket_id and hubspot_contact_id
        and (first or previous.startswith("operator_") or "hubspot_note" in previous)
        and not await _note_mailbox_send(msg, conv, hubspot_contact_id)
    ):
        errors.append("hubspot_note")
    if due("hubspot_contact_status") and hubspot_contact_id and settings.HUBSPOT_UPDATE_CONTACT_INBOUND_STATUS:
        client = None
        try:
            client = HubSpotClient()
            await client.update_inbound_status(hubspot_contact_id, "meeting_link_sent")
        except HubSpotNotConfigured:
            errors.append("hubspot_contact_status:not_configured")
        except Exception as exc:
            errors.append(f"hubspot_contact_status:{type(exc).__name__}")
            logger.warning(
                "HubSpot contact status update failed (contact=%s, msg=%d). Send succeeded.",
                hubspot_contact_id, message_id,
                exc_info=True,
            )
        finally:
            if client is not None:
                await client.close()

    if conv:
        if due("google_sheets"):
            profile = session.get(CustomerProfile, conv.contact_id)
            if profile:
                profile.pipeline_stage = "meeting_link_sent"
            sheet_client_id = conv.sheet_client_id or (contact.sheet_client_id if contact else None)
            if not sheets_configured():
                errors.append("google_sheets:not_configured")
            elif not isinstance(sheet_client_id, int) or sheet_client_id <= 0:
                errors.append("google_sheets:missing_client_id")
            else:
                sheet_ok = await asyncio.to_thread(
                    update_inbound_stage,
                    sheet_client_id,
                    "meeting_link_sent",
                    # MQL/PQL 은 안 보냅니다 — 시트의 수식이 구독 플랜에서 계산합니다.
                    conv.sheet_inquiry_key,
                )
                if not sheet_ok:
                    errors.append("google_sheets_stage")

        now = datetime.now(timezone.utc)
        msg.post_send_sync_attempted_at = now
        msg.post_send_sync_attempts = previous_attempts + 1
        if errors and all(error.endswith(_PERMANENT_SYNC_ERRORS) for error in errors):
            # 설정이 없어 다시 해도 같다 — 몇 시간에 걸친 여덟 번의 재시도를 안 한다. 오류는 행에
            # 남아 복구 화면에 뜨고, 운영자가 「동기화 재시도」를 누르면 다시 한다.
            msg.post_send_sync_attempts = max(msg.post_send_sync_attempts, POST_SEND_SYNC_MAX_RETRIES)
        msg.post_send_sync_error = ", ".join(errors)[:1000] or None
        msg.post_send_synced_at = None if errors else now
        # **첫 회차의 기록은 이 횟수와 같은 commit 으로 들어갑니다** (2026-09-29). 예전에는 횟수를
        # 먼저 commit 하고 그 뒤에 적었는데, 그 사이에 프로세스가 죽으면(이 서비스는 메모리가 모자라
        # 실제로 죽었다) 다음 회차는 「첫 회차가 아니다」로 읽어 그 줄들이 영영 없었습니다. 같은
        # commit 이면 함께 들어가거나 함께 안 들어가고, 안 들어갔으면 횟수가 0 그대로라 다음 회차가
        # 다시 첫 회차입니다 — 두 번 적히지도 않습니다.
        if first:
            if reminder:
                # 사람이 쓴 답이 아니라 요약에 한 줄을 보태지 않습니다 — 「답을 세 번 했다」로 읽힙니다.
                #
                # **글자의 출처는 `followup_sequence.done_label` 한 곳입니다** (2026-09-21 운영자
                # 지시). 티켓 배너 · 소통 히스토리 · 진행 기록이 같은 문장을 적어야 합니다.
                label = done_label(msg.prompt_variant)
                add_progress(conv.id, "reply", label, session=session)
                # **소통 히스토리에도 한 줄 남깁니다.** 그 표가 티켓 화면과 고객 상세가 그리는
                # 목록이고, 위의 진행 기록 `reply` 는 읽을 때 걸러집니다
                # (`ROUTINE_PROGRESS_KINDS`) — 그것만으로는 운영자 눈에 아무것도 안 남습니다.
                #
                # `customer_ops.interaction_add` 는 안 씁니다: 그 헬퍼는 티켓 요약에 한 줄을
                # 보태고 허브스팟 노트까지 남기는데, 메일 자체는 이미 Conversations 스레드에
                # 있습니다(바로 위 「요약에 안 보탠다」와 같은 이유).
                #
                # **`direction` 은 `outgoing` 이어야 합니다.** `followup_sequence._replies` 가 이
                # 연락처의 `inbound` 줄을 「고객이 답장했다」로 읽어 티켓을 Negotiating 으로 옮기고
                # 시퀀스를 멈춥니다 — 우리 리마인더가 고객 답장으로 보이면 안 됩니다.
                #
                # `external_id` 가 유니크(0106)라 이미 있으면(옛 데이터) 넣지 않습니다 — 부딪히면 위의
                # 횟수까지 같이 롤백됩니다.
                external_id = f"{REMINDER_NOTE_PREFIX}{msg.id}"
                if session.scalar(select(CustomerInteraction.id).where(
                    CustomerInteraction.external_id == external_id
                )) is None:
                    session.add(CustomerInteraction(
                        contact_id=conv.contact_id,
                        conversation_id=conv.id,
                        channel="이메일",
                        direction="outgoing",
                        # 열이 300자입니다. 제목은 「RE: <고객이 쓴 제목>」이라 길이를 우리가 정하지
                        # 않습니다 — 안 자르면 긴 제목 하나가 배달된 메일의 기록을 통째로 날립니다.
                        subject=(msg.subject or "")[:300] or None,
                        summary=label,
                        external_id=external_id,
                        happened_at=msg.sent_at or now,
                    ))
            else:
                add_progress(conv.id, "reply", f"답변 발송 완료: {msg.subject or '(제목 없음)'}"[:200],
                             session=session)
        session.commit()
        if not reminder:
            # 티켓 요약에 우리 답 한 줄을 덧붙입니다. **여기서** 하는 이유: 요약은 예전에
            # 초안이 만들어진 직후에 쓰였고, 그래서 나가지 않은 글이 「이렇게 답했다」로
            # 적혔습니다. 회차마다 부르는 이유: 한 번 적으면 그 행(`summary_line`)이 들고 있어
            # 두 번 안 붙고(`append_summary_line`), 첫 회차 뒤에 죽었어도 다음 회차가 채웁니다.
            await asyncio.to_thread(append_summary_line, msg.id)


# `send()` 가 메시지에 채우는 것 — 나간 그대로의 본문과 발송 id 들.
_DELIVERED_FIELDS = ("body", "hubspot_thread_id", "hubspot_message_id", "smtp_message_id", "in_reply_to")


def _record_delivery(session, message_id: int, carried: dict, delivered: bool):
    """나간 메시지를 적고 commit 합니다. (대화, 이번 발송이 단계를 옮겼나)를 돌려줍니다."""
    msg = session.get(Message, message_id)
    for name, value in carried.items():
        setattr(msg, name, value)
    # "sent" 는 고객에게 실제 발송을 시도한 건뿐입니다. 아예 보내지 않은
    # 건은 test_sent — 화면에서 구분되고 발송률 집계도 흐리지 않습니다.
    msg.status = "sent" if delivered else "test_sent"
    msg.send_claimed_at = None
    msg.sent_at = datetime.now(timezone.utc)
    msg.send_error = None

    moved = False
    conv = session.get(Conversation, msg.conversation_id)
    if conv:
        conv.last_outgoing_at = msg.sent_at
        # **앞으로만 갑니다.** 발송은 「답이 나갔다」는 뜻이지 「협상 전으로
        # 돌아가라」가 아닙니다. 아직 아무도 안 옮긴 건만 올립니다 — 협상·수주·
        # 종료로 이미 가 있는 건을 여기서 되돌리면, 허브스팟이 기준인 값을
        # 우리가 덮어쓰고 다음 스윕이 그걸 또 되돌립니다.
        if conv.stage in _ADVANCES_FROM:
            moved = conv.stage != "meeting_link_sent"
            conv.stage = "meeting_link_sent"
    session.commit()
    return conv, moved


async def _send_one(message_id: int) -> bool:
    """Send a single message that this worker has already claimed (status == _WORKER_ID).

    Caller is responsible for the atomic claim. We only send + update.
    Transient failures (for example an explicit 429) are retried with exponential backoff
    inside this call. Permanent failures (bad recipient, auth) fail immediately to
    send_failed without retry.
    """
    from ..integrations.senders import send
    from ..integrations.delivery import (
        DeliveryPermanentError,
        DeliveryTransientError,
        DeliveryUnknown,
        SendingDisabled,
    )

    session = SessionLocal()
    try:
        msg = session.get(Message, message_id)
        if not msg or msg.status != _WORKER_ID:
            # Lost the row (shouldn't happen — caller already claimed) or another process intervened.
            return False

        last_exc: Exception | None = None
        delivered: bool | None = None  # None = 아직 안 나갔다(실패), True/False = 끝났다
        for attempt in range(SEND_TRANSIENT_MAX_RETRIES):
            # **이 try 는 `send()` 하나만 감쌉니다** (2026-09-29). 예전에는 발송 뒤의 기록(commit ·
            # 단계 이동)까지 같은 try 안이라, 허브스팟이 받은 **직후** DB 연결이 한 번 끊기면 아래
            # `except Exception` 이 그것을 「알 수 없는 발송 실패 = 영구」로 읽어 `send_failed` 를
            # 찍었습니다 — 롤백으로 `hubspot_message_id` 도 사라지고, 승인은 `send_failed` 를
            # 「아무것도 안 갔다」로 보고 재발송을 받아 줍니다. 고객이 같은 메일을 두 통 받습니다.
            try:
                # 메일만 막는 스위치(safe_mode.EMAIL_SENDING_ENABLED)는 **실패가 아닙니다.**
                # "이번 건은 보내지 않는다" 이고, 운영자가 검토 완료·발송을 누른 뒤에 일어나야
                # 하는 나머지 — 단계 이동, HubSpot 티켓, 워크북 — 은 전부 그대로 일어납니다.
                # 여기서 안 잡으면 DeliveryPermanentError 로 떨어져 send_failed 가 되고, 누른
                # 사람 눈에는 아무것도 안 된 것으로 보입니다.
                #
                # HubSpot POST 전에 막히므로 고객 스레드에도 기록이 생기지 않습니다.
                try:
                    await send(msg)
                    delivered = True
                except SendingDisabled:
                    delivered = False
                    logger.info(
                        "메일 발송이 코드에서 꺼져 있어 %d 번은 보내지 않았습니다. "
                        "단계 이동과 HubSpot·워크북 동기화는 그대로 진행합니다.",
                        message_id,
                    )
                break
            except DeliveryUnknown as exc:
                session.rollback()
                msg = session.get(Message, message_id)
                if msg:
                    msg.status = "delivery_unknown"
                    msg.send_claimed_at = None
                    msg.send_error = str(exc)[:2000]
                    session.commit()
                    _read_thread_again(msg.conversation_id)
                logger.error("Delivery outcome unknown for message %d: %s", message_id, exc)
                return False
            except DeliveryPermanentError as exc:
                last_exc = exc
                logger.error("Permanent send failure for message %d: %s", message_id, exc)
                break  # do not retry
            except DeliveryTransientError as exc:
                last_exc = exc
                if attempt < SEND_TRANSIENT_MAX_RETRIES - 1:
                    delay = 2 ** attempt
                    logger.warning(
                        "Transient send failure for message %d (attempt %d/%d): %s — retry in %ds",
                        message_id, attempt + 1, SEND_TRANSIENT_MAX_RETRIES, exc, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error("Transient send failure exhausted retries for message %d: %s", message_id, exc)
                break
            except Exception as exc:
                # Unknown error class — treat as permanent to avoid infinite retries.
                last_exc = exc
                logger.error("Unknown send failure for message %d: %s", message_id, exc)
                break

        if delivered is not None:
            # **여기서부터는 무엇이 실패해도 `send_failed` 가 아닙니다** — 메일은 이미 끝났습니다.
            # 적다가 끊기면 새 세션으로 한 번 더 적습니다. `send()` 가 본문을 다듬고(링크·가격 가드)
            # 발송 id 를 채웠으므로, 롤백이 지운 그 값을 들고 가서 다시 씁니다.
            carried = {name: getattr(msg, name) for name in _DELIVERED_FIELDS}
            try:
                conv, moved = _record_delivery(session, message_id, carried, delivered)
            except Exception:
                logger.exception("발송된 메시지 %d 를 적다가 끊겼습니다 — 새 세션으로 다시 적습니다",
                                 message_id)
                session.rollback()
                session.close()
                session = SessionLocal()
                try:
                    conv, moved = _record_delivery(session, message_id, carried, delivered)
                except Exception:
                    # 두 번 다 못 적었습니다. 행은 우리가 잡은 그대로(`sending:…`)라 15분 뒤
                    # `_reclaim_stuck_sending` 이 `delivery_unknown` 으로 돌리고 사람이 확인합니다 —
                    # 「실패」가 아니라서 다시 보내지는 않습니다.
                    logger.exception("발송된 메시지 %d 를 끝내 못 적었습니다 — 발송 확인 대기로 둡니다",
                                     message_id)
                    return delivered
            msg = session.get(Message, message_id)
            if delivered:
                # 발송 한도는 HubSpot POST가 성공한 것만 셉니다. 안 보낸 건까지 세면 아무도
                # 메일을 못 받는 동안 워커가 스스로 속도를 늦춥니다.
                _record_send()
            # 발송 이후 처리는 test_mode 와 무관하게 돕니다. 각 목적지는 아래에서
            # guard_external_write 가 따로 막습니다 — 여기서 두 번 막을 일이 아닙니다.
            try:
                await _post_send_bookkeeping(session, msg, conv, message_id, moved=moved)
            except Exception:
                # Delivery is already committed. A bookkeeping outage must
                # never turn a delivered email into `send_failed`.
                session.rollback()
                logger.exception(
                    "Post-send bookkeeping failed for message %d; queued for retry.",
                    message_id,
                )
            logger.info("Worker sent message %d.", message_id)
            return True

        # All retries exhausted or hit permanent error.
        session.rollback()
        msg = session.get(Message, message_id)
        if msg:
            msg.status = "send_failed"
            msg.send_claimed_at = None
            # 사유를 행에 남깁니다. 로그에만 있으면 30분 뒤 스크롤 밖이고, 화면은 빨간
            # 「발송 실패」 배지 하나로 끝나 운영자가 왜인지 알 길이 없습니다.
            msg.send_error = str(last_exc)[:2000] if last_exc else None
            session.commit()
        logger.error("Worker failed to send message %d: %s", message_id, last_exc)
        return False
    finally:
        session.close()


def _claim_id(message_id: int) -> bool:
    """Atomically claim one specific approved message."""
    with SessionLocal() as session:
        result = session.execute(
            update(Message)
            .where(
                Message.id == message_id,
                Message.status == "approved",
                (Message.prompt_variant.is_(None)) | (Message.prompt_variant != "auto_ack"),
            )
            .values(
                status=_WORKER_ID,
                send_claimed_at=datetime.now(timezone.utc),
                send_attempts=Message.send_attempts + 1,
                send_error=None,
            )
        )
        session.commit()
        return result.rowcount == 1


def _claim_ready_id() -> int | None:
    """Atomically claim ONE approved message whose scheduled_at has passed.

    Strategy: SELECT a candidate id, then UPDATE…WHERE id=:id AND status='approved'.
    Only one worker's UPDATE will affect a row; losers see rowcount==0 and retry.
    This works on SQLite (with WAL) and Postgres without needing FOR UPDATE.
    """
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        candidate_ids = (
            session.query(Message.id)
            .filter(
                Message.status == "approved",
                (Message.prompt_variant.is_(None)) | (Message.prompt_variant != "auto_ack"),
                (Message.scheduled_at <= now) | (Message.scheduled_at.is_(None)),
            )
            .order_by(Message.scheduled_at.asc().nullsfirst())
            .limit(50)
            .all()
        )

    for (mid,) in candidate_ids:
        if _claim_id(mid):
            return mid
        # Another worker took it; try the next candidate.

    return None


async def send_approved_now(message_id: int) -> bool:
    """Use the same atomic claim and delivery path as the background worker."""
    _reset_daily_if_needed()
    if _daily_limit_reached() or _minute_window_full():
        with SessionLocal() as session:
            session.execute(
                update(Message)
                .where(Message.id == message_id, Message.status == "approved")
                .values(scheduled_at=datetime.now(timezone.utc) + timedelta(seconds=60))
            )
            session.commit()
        logger.warning("Message %d deferred by the shared send quota.", message_id)
        return False
    if not _claim_id(message_id):
        return False
    return await _send_one(message_id)


def request_shutdown() -> None:
    """Signal the send worker to exit at the next checkpoint."""
    global _shutdown
    _shutdown = True


def _reclaim_stuck_sending(now: datetime | None = None) -> int:
    """Quarantine stale sends whose delivery outcome cannot be known safely.

    HubSpot may have accepted the message just before the worker crashed. Automatic
    replay could duplicate a customer email, so an operator must verify it first.
    """
    session = SessionLocal()
    try:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(seconds=SEND_LEASE_SECONDS)
        stale = (
            Message.status.like("sending:%"),
            Message.send_claimed_at.is_not(None),
            Message.send_claimed_at <= cutoff,
        )
        conversations = set(session.scalars(select(Message.conversation_id).where(*stale)))
        result = session.execute(
            update(Message)
            .where(*stale)
            .values(status="delivery_unknown", send_claimed_at=None)
        )
        session.commit()
    finally:
        session.close()
    for conversation_id in conversations:
        _read_thread_again(conversation_id)
    return result.rowcount or 0


def _read_thread_again(conversation_id: int | None) -> None:
    """`delivery_unknown` 이 된 티켓의 스레드를 수집 대기열에 올립니다.

    나갔다면 허브스팟 스레드에 사본이 서고, 수집기가 그것을 보고 그 메시지를 스스로 확인된
    발송으로 닫습니다(`ticket_history._store`). 그 티켓에 웹훅이 올지에 기대지 않습니다 — 안 오면
    사람이 「나간 것 확인」을 누를 때까지 그대로입니다. 못 올려도 발송 결과 표시는 이미 적었으니
    삼킵니다.
    """
    if not conversation_id:
        return
    try:
        from .ticket_history import mark_ticket_history_stale

        mark_ticket_history_stale(conversation_id)
    except Exception:
        logger.warning("문의 %s: 스레드 다시 읽기를 못 올렸습니다", conversation_id, exc_info=True)


def _sync_retry_due(msg: Message, now: datetime) -> bool:
    attempted_at = msg.post_send_sync_attempted_at
    if attempted_at is None:
        return True
    if attempted_at.tzinfo is None:
        attempted_at = attempted_at.replace(tzinfo=timezone.utc)
    delay = min(3600, 60 * (2 ** max((msg.post_send_sync_attempts or 1) - 1, 0)))
    return attempted_at <= now - timedelta(seconds=delay)


async def _retry_post_send_syncs(limit: int = 20) -> int:
    """Retry CRM/Sheets updates without ever resending the delivered email."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        candidates = (
            session.query(Message.id)
            .filter(
                Message.status == "sent",
                (Message.prompt_variant.is_(None)) | (Message.prompt_variant != "auto_ack"),
                Message.post_send_synced_at.is_(None),
                Message.post_send_sync_attempts < POST_SEND_SYNC_MAX_RETRIES,
            )
            .order_by(Message.post_send_sync_attempted_at.asc().nullsfirst())
            .limit(limit)
            .all()
        )

    retried = 0
    for (message_id,) in candidates:
        with SessionLocal() as session:
            msg = session.get(Message, message_id)
            if not msg or not _sync_retry_due(msg, now):
                continue
            conv = session.get(Conversation, msg.conversation_id)
            try:
                await _post_send_bookkeeping(session, msg, conv, message_id)
                retried += 1
            except Exception:
                session.rollback()
                logger.exception("Post-send sync retry failed for message %d.", message_id)
    return retried


async def run_send_worker() -> None:
    """Poll loop with rate limiting, daily cap, jitter, and graceful shutdown.

    Atomic claim makes the loop safe under multiple workers/processes — each row is
    sent by exactly one process.
    """
    logger.info(
        "Send worker started (id=%s, poll %ds, rate %d/min, daily cap %d, jitter %ds).",
        _WORKER_ID,
        POLL_INTERVAL_SECONDS,
        settings.SEND_RATE_PER_MINUTE,
        settings.DAILY_SEND_LIMIT,
        settings.SEND_JITTER_SECONDS,
    )

    while not _shutdown:
        try:
            from .worker_heartbeat import record_worker_heartbeat

            await asyncio.to_thread(record_worker_heartbeat, "send")
            _reset_daily_if_needed()
            quarantined = _reclaim_stuck_sending()
            if quarantined:
                logger.warning(
                    "Quarantined %d stale send(s) as delivery_unknown.", quarantined
                )
            await _retry_post_send_syncs()
            from ._notify import retry_pending_approval_notifications

            await asyncio.to_thread(retry_pending_approval_notifications)

            if _daily_limit_reached():
                logger.info(
                    "Daily send limit reached (%d/%d). Pausing until tomorrow.",
                    _daily_count,
                    settings.DAILY_SEND_LIMIT,
                )
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            sent_this_tick = 0
            while not _shutdown:
                if _daily_limit_reached():
                    break
                if _minute_window_full():
                    logger.info("Minute rate limit hit. Waiting 60s.")
                    await asyncio.sleep(60)

                mid = _claim_ready_id()
                if mid is None:
                    break

                if settings.SEND_JITTER_SECONDS > 0:
                    jitter = random.uniform(0, settings.SEND_JITTER_SECONDS)
                    await asyncio.sleep(jitter)

                await _send_one(mid)
                sent_this_tick += 1

            if sent_this_tick:
                logger.info("Send worker tick: dispatched %d message(s).", sent_this_tick)
                # **나갔다고 화면에 알립니다** (2026-09-08). 초안 워커와 같은 구멍이었습니다 —
                # SSE 를 쏘는 곳이 HTTP 미들웨어뿐이라, 백그라운드가 메일을 보내도 열려 있는
                # 목록은 그 초안을 계속 「발송 대기」로 그렸습니다. 여기는 이벤트 루프라
                # `publish` 를 바로 부를 수 있습니다.
                from ..api.routes.ui_api import publish

                publish("send-worker")

        except Exception:
            logger.exception("Send worker tick error.")

        # Sleep in short slices so shutdown is responsive.
        for _ in range(POLL_INTERVAL_SECONDS):
            if _shutdown:
                break
            await asyncio.sleep(1)

    logger.info("Send worker shutdown complete.")
