"""Small database-backed worker for reliable HubSpot inbound processing."""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError

from ..db.models import Conversation, InboundJob, Message
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 8
LEASE_SECONDS = 30 * 60
HEARTBEAT_SECONDS = LEASE_SECONDS // 3
IDLE_SECONDS = 2.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def inbound_event_key(ticket_id: str, occurrence_key: str | None = None) -> str:
    """One stable key shared by webhook and polling discovery."""
    ticket_id = str(ticket_id).strip()
    if not ticket_id:
        raise ValueError("ticket_id is required")
    suffix = f"changed:{occurrence_key}" if occurrence_key else "created"
    return f"hubspot:ticket:{ticket_id}:{suffix}"


def enqueue_inbound_ticket(
    ticket_id: str,
    *,
    source: str,
    occurred_at: str | None = None,
    hubspot_event_id: str | None = None,
    event_type: str = "ticket_created",
    occurrence_key: str | None = None,
    draft_message_id: int | None = None,
) -> bool:
    """Persist a ticket for processing. Returns true when newly queued/rearmed.

    ``draft_message_id`` 를 주면 새 초안을 만드는 대신 **그 메시지를 다시 씁니다**. 운영자가
    누르는 「재생성」이 이 길로 옵니다 — 리스가 끊긴 작업이 자기 초안으로 돌아오는 길과 같은
    길입니다(``_draft_message_id``). 새 행을 만들지 않으므로 그 티켓의 회신은 하나로 남고,
    화면·집계·발송이 보던 ``Message.id`` 도 그대로입니다.
    """
    now = _utcnow()
    event_key = inbound_event_key(ticket_id, occurrence_key)
    payload = {
        "ticket_id": str(ticket_id),
        "event_type": event_type,
        "occurred_at": occurred_at,
        "hubspot_event_id": hubspot_event_id,
    }
    if draft_message_id is not None:
        payload["draft_message_id"] = draft_message_id
    with SessionLocal() as session:
        session.add(
            InboundJob(
                event_key=event_key,
                source=source,
                payload=payload,
                status="pending",
                available_at=now,
            )
        )
        try:
            session.commit()
            return True
        except IntegrityError:
            session.rollback()
            existing = session.query(InboundJob).filter_by(event_key=event_key).first()
            if existing and existing.status == "dead":
                existing.payload = payload
                existing.status = "pending"
                existing.attempts = 0
                existing.available_at = now
                existing.locked_at = None
                existing.locked_by = None
                existing.last_error = None
                existing.updated_at = now
                session.commit()
                return True
            return False


class RedraftError(RuntimeError):
    """이 메시지는 다시 쓸 수 없습니다 — 사유는 메시지 본문에 있습니다."""


def request_redraft(message_id: int) -> int:
    """초안을 처음부터 다시 쓰게 큐에 올립니다. 대화 id 를 돌려줍니다.

    **함수인 이유**: 부르는 곳이 둘입니다 — 티켓 세부 내역의 「초안 다시 쓰기」와 복구 화면의
    「재시도」. 한쪽이 다른 쪽의 라우트를 부르게 두면 응답 JSON 을 도로 뜯어 예외로 바꾸는
    코드가 생기고, 두 화면이 서로 다른 세션을 들고 같은 행을 만지게 됩니다.

    **행을 새로 만들지 않습니다.** 새 초안을 따로 만들면 한 티켓에 회신이 둘이 되고, 화면·
    집계·발송 큐가 어느 쪽이 진짜인지 각자 판단하게 됩니다. 대신 이 메시지를 ``drafting`` 으로
    돌리고 ``draft_message_id`` 를 실은 작업을 올려 **그 메시지를 덮어쓰게** 합니다.

    발송은 하지 않습니다. 다시 쓰인 초안은 검토 대기로 서고, 보낼지는 사람이 정합니다.
    """
    from ..db.conversation_history import add_progress

    with SessionLocal() as session:
        msg = session.get(Message, message_id)
        if not msg:
            raise RedraftError("메시지를 찾을 수 없습니다")
        if msg.status not in {"send_failed", "draft_failed"}:
            raise RedraftError(f"발송·작성 실패 상태만 다시 쓸 수 있습니다 (현재: {msg.status})")
        conv = session.get(Conversation, msg.conversation_id)
        ticket_id = (conv.hubspot_ticket_id or "") if conv else ""
        if not ticket_id:
            # 티켓이 없으면 다시 쓸 근거(문의 본문)를 가져올 곳이 없습니다. 워크북에서만 사는
            # 문의가 여기 해당하고, 그 건은 손으로 고치는 편이 맞습니다.
            raise RedraftError("HubSpot 티켓이 없는 문의는 다시 쓸 수 없습니다")
        msg.status = "drafting"
        msg.send_error = None
        msg.send_claimed_at = None
        conv_id = msg.conversation_id
        session.commit()

    # 누를 때마다 새 작업이어야 합니다 — 이벤트 키가 같으면 두 번째 누름이 조용히 버려집니다.
    stamp = _utcnow().strftime("%Y%m%d%H%M%S%f")
    enqueue_inbound_ticket(
        ticket_id,
        source="console_redraft",
        event_type="redraft",
        occurrence_key=f"redraft:{message_id}:{stamp}",
        draft_message_id=message_id,
    )
    add_progress(conv_id, "draft", "회신 초안을 다시 작성합니다.")
    return conv_id


# 연락처 필드 동기화 작업임을 알아보는 표. payload 에 ticket_id 대신 이것이 들어 있다.
CONTACT_SYNC = "contact_field_sync"


def enqueue_contact_field_sync(hubspot_contact_id: str, occurred_at: int | None) -> bool:
    """연락처 하나를 「다시 읽어야 한다」고 큐에 적습니다. **네트워크에 닿지 않습니다.**

    웹훅 요청 안에서 허브스팟을 읽던 코드가 여기로 왔습니다. 이유는 볼륨입니다: 대량 임포트나
    워크플로우 일괄 수정이 한 번에 수백 건을 보내는데, 이벤트마다 읽으면 그 요청 하나가
    수십 초가 되고 허브스팟은 응답을 못 받아 **같은 것을 다시 보냅니다** — 폭주가 스스로를
    키웁니다. 티켓 웹훅이 진작 이 모양인 이유가 그것이고("acknowledge without external
    calls"), 연락처 쪽만 그 규칙을 어기고 있었습니다.

    **같은 연락처의 같은 분(minute)은 한 건으로 접힙니다.** 속성 여덟 개를 감시하므로 한 번의
    저장이 이벤트 여덟 개로 옵니다 — 그 여덟이 한 작업이 되어야 허브스팟을 한 번만 읽습니다.
    분이 바뀌면 새 작업이라, 나중의 변경이 막히지도 않습니다.
    """
    bucket = int(occurred_at or 0) // 60_000
    now = _utcnow()
    with SessionLocal() as session:
        session.add(
            InboundJob(
                event_key=f"{CONTACT_SYNC}:{hubspot_contact_id}:{bucket}",
                source="webhook",
                payload={"kind": CONTACT_SYNC, "hubspot_contact_id": str(hubspot_contact_id)},
                status="pending",
                available_at=now,
            )
        )
        try:
            session.commit()
            return True
        except IntegrityError:
            # 이미 같은 분의 같은 연락처가 큐에 있습니다 — 그게 접히는 자리입니다.
            session.rollback()
            return False


def _claim_next_job() -> tuple[int, dict, int, str] | None:
    now = _utcnow()
    stale_before = now - timedelta(seconds=LEASE_SECONDS)
    ready = or_(
        and_(InboundJob.status == "pending", InboundJob.available_at <= now),
        and_(InboundJob.status == "processing", InboundJob.locked_at <= stale_before),
    )

    with SessionLocal() as session:
        # A worker that died on its final attempt must not leave a permanent lease.
        session.query(InboundJob).filter(
            InboundJob.status == "processing",
            InboundJob.attempts >= MAX_ATTEMPTS,
            InboundJob.locked_at <= stale_before,
        ).update(
            {
                InboundJob.status: "dead",
                InboundJob.locked_at: None,
                InboundJob.locked_by: None,
                InboundJob.updated_at: now,
            },
            synchronize_session=False,
        )
        session.commit()

        candidates = (
            session.query(InboundJob.id)
            .filter(ready, InboundJob.attempts < MAX_ATTEMPTS)
            .order_by(InboundJob.available_at.asc(), InboundJob.id.asc())
            .limit(10)
            .all()
        )
        for (job_id,) in candidates:
            owner = uuid4().hex
            claimed = (
                session.query(InboundJob)
                .filter(InboundJob.id == job_id, ready, InboundJob.attempts < MAX_ATTEMPTS)
                .update(
                    {
                        InboundJob.status: "processing",
                        InboundJob.attempts: InboundJob.attempts + 1,
                        InboundJob.locked_at: now,
                        InboundJob.locked_by: owner,
                        InboundJob.updated_at: now,
                    },
                    synchronize_session=False,
                )
            )
            if not claimed:
                session.rollback()
                continue
            session.commit()
            job = session.get(InboundJob, job_id)
            if job:
                return job.id, dict(job.payload), job.attempts, owner
    return None


def _renew_job_lease(job_id: int, owner: str) -> bool:
    now = _utcnow()
    with SessionLocal() as session:
        updated = (
            session.query(InboundJob)
            .filter_by(id=job_id, status="processing", locked_by=owner)
            .update(
                {InboundJob.locked_at: now, InboundJob.updated_at: now},
                synchronize_session=False,
            )
        )
        session.commit()
        return bool(updated)


def _lease_heartbeat(job_id: int, owner: str, stopped: threading.Event) -> None:
    while not stopped.wait(HEARTBEAT_SECONDS):
        try:
            if not _renew_job_lease(job_id, owner):
                return
        except Exception:
            logger.exception("Inbound job lease heartbeat failed (job=%d)", job_id)


def _finish_job(job_id: int, owner: str) -> None:
    now = _utcnow()
    with SessionLocal() as session:
        session.query(InboundJob).filter_by(
            id=job_id, status="processing", locked_by=owner
        ).update(
            {
                InboundJob.status: "done",
                InboundJob.completed_at: now,
                InboundJob.locked_at: None,
                InboundJob.locked_by: None,
                InboundJob.last_error: None,
                InboundJob.updated_at: now,
            },
            synchronize_session=False,
        )
        session.commit()


def _retry_job(job_id: int, owner: str, attempts: int, exc: Exception) -> None:
    now = _utcnow()
    terminal = attempts >= MAX_ATTEMPTS
    delay = min(30 * (2 ** max(0, attempts - 1)), 30 * 60)
    error = f"{type(exc).__name__}: {str(exc)[:500]}"
    with SessionLocal() as session:
        session.query(InboundJob).filter_by(
            id=job_id, status="processing", locked_by=owner
        ).update(
            {
                InboundJob.status: "dead" if terminal else "pending",
                InboundJob.available_at: now + timedelta(seconds=delay),
                InboundJob.locked_at: None,
                InboundJob.locked_by: None,
                InboundJob.last_error: error,
                InboundJob.updated_at: now,
            },
            synchronize_session=False,
        )
        session.commit()


def process_one_inbound_job() -> bool:
    """Claim and process one job. Returns false when no job is ready."""
    claimed = _claim_next_job()
    if not claimed:
        return False

    job_id, payload, attempts, owner = claimed
    if payload.get("kind") == CONTACT_SYNC:
        # 리스 하트비트를 안 답니다 — 허브스팟 읽기 한 번과 행 하나 쓰기라, 리스가 끊길
        # 만큼 오래 걸리지 않습니다. 실패하면 `_retry_job` 이 뒤로 미룹니다.
        from .contact_sync import sync_contact_from_hubspot

        try:
            sync_contact_from_hubspot(str(payload["hubspot_contact_id"]))
        except Exception as exc:
            _retry_job(job_id, owner, attempts, exc)
            logger.warning("연락처 필드 동기화 실패 (attempt=%d)", attempts, exc_info=True)
        else:
            _finish_job(job_id, owner)
        return True

    ticket_id = str(payload["ticket_id"])
    heartbeat_stop = threading.Event()
    heartbeat = threading.Thread(
        target=_lease_heartbeat,
        args=(job_id, owner, heartbeat_stop),
        name=f"inbound-heartbeat-{job_id}",
        daemon=True,
    )
    heartbeat.start()
    try:
        from ..integrations.hubspot import HubSpotClient
        from .inbound import InboundAgent

        hubspot = HubSpotClient()
        contact_id = hubspot.get_ticket_primary_contact_sync(ticket_id)
        if not contact_id:
            raise RuntimeError("ticket has no associated contact yet")
        result = InboundAgent(hubspot=hubspot).handle(
            {
                "event_type": payload.get("event_type") or "ticket_created",
                "object_id": contact_id,
                "ticket_id": ticket_id,
                "occurred_at": payload.get("occurred_at"),
                # Internal recovery metadata.  InboundAgent stores the placeholder
                # id on this durable job in the same transaction that creates it,
                # so a reclaimed lease resumes that exact draft.
                "_inbound_job_id": job_id,
                "_draft_message_id": payload.get("draft_message_id"),
            }
        )
        if isinstance(result, dict) and result.get("status") == "skipped_no_body":
            raise RuntimeError("ticket body is not available yet")
    except Exception as exc:
        _retry_job(job_id, owner, attempts, exc)
        logger.exception(
            "Inbound job failed; retry scheduled (ticket=%s attempt=%d)",
            ticket_id,
            attempts,
        )
    else:
        _finish_job(job_id, owner)
        logger.info("Inbound job completed (ticket=%s)", ticket_id)
    finally:
        heartbeat_stop.set()
        heartbeat.join(timeout=1)
    return True


async def run_inbound_worker() -> None:
    """Continuously drain the durable inbound queue."""
    logger.info("Durable inbound worker started")
    while True:
        try:
            from .worker_heartbeat import record_worker_heartbeat

            await asyncio.to_thread(record_worker_heartbeat, "inbound")
            handled = await asyncio.to_thread(process_one_inbound_job)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A temporary database/claim failure must not kill the application task.
            logger.exception("Inbound worker iteration failed")
            handled = False
        if not handled:
            await asyncio.sleep(IDLE_SECONDS)
