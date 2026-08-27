"""SQLAlchemy ORM models for the sales automation system."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hubspot_contact_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    normalized_email: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    company: Mapped[str | None] = mapped_column(String, nullable=True)
    domain: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    country: Mapped[str | None] = mapped_column(String, nullable=True)
    # 허브스팟이 접속 IP 로 뽑은 국가 (0094). 위 `country` 와 다른 값이다 — 저쪽은 사람이
    # 폼에 적은 값이라 대개 비어 있고, 워크북의 IP Country 열이 뜻하는 것은 이쪽이다.
    ip_country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lifecycle_stage: Mapped[str | None] = mapped_column(String, nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    # Operator-editable free-text note on what this person/company does. Filled in
    # over the course of a conversation even for gmail/unverified senders.
    role_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Stable ID allocated in the existing Inbound DB (1000-series). Reused by
    # the order sheet so both tabs keep the sales team's original join key.
    sheet_client_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    conversations: Mapped[list[Conversation]] = relationship(back_populates="contact")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    # The customer's own inquiry subject — what they wrote in the subject line, or the
    # HubSpot ticket subject for rows created by the backfill. Renamed from ``topic`` in
    # migration 0041: that column held two unrelated things, an AI "문의 유형" category on
    # the inbound path and the ticket subject on the backfill path, and only the second
    # was ever worth showing.
    inquiry_subject: Mapped[str | None] = mapped_column(String, nullable=True)
    # 문의 유형 (src/common/inquiry.py). 0041 이후 한동안 저장하지 않았는데, 목록에서
    # 채널("email" — 전부 같은 값) 대신 보여줄 것이 이것이고, 어떤 문의가 실제로 오는지도
    # 이 열이 없으면 알 수 없습니다.
    inquiry_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stage: Mapped[str] = mapped_column(String, nullable=False, default="initial")
    # Won Type(PoC/Contract/Renewal) 또는 Lost Reason 여섯 가지. **한 열입니다** — 한
    # 문의가 동시에 이기고 지지는 않으므로, 어느 목록의 값인지는 그때의 ``stage`` 가
    # 정합니다(`customer_ops.DEAL_DETAILS`). 열을 둘로 나누면 Won 이었다가 Lost 가 된
    # 티켓에 두 값이 남고, 어느 쪽이 지금 값인지 행만 봐서는 알 수 없습니다.
    deal_detail: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_outgoing_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_incoming_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    hubspot_ticket_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    # ISO 639-1 language the customer wrote in — the language every reply in this
    # thread must go out in (enforced in code at send time).
    inquiry_language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Rolling LLM-maintained summary + the customer's standing requests. These are
    # regenerated as the thread evolves (unlike the append-only progress log).
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_requests: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Exact row written for this inquiry. Pipeline moves update only that row's
    # stage cells and never rewrite the operator-owned sheet layout.
    sheet_inbound_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Customer/account ID shared by every inquiry from the same company. It is a
    # join key, not an inquiry-row key; ``sheet_inquiry_key`` identifies the row.
    sheet_client_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # Stable per-inquiry key written into Inbound DB. Unlike the physical row number
    # it survives sorting, and unlike Client ID it stays unique when one company asks
    # more than once. Migration 0085 backfills and uniquely indexes this column.
    sheet_inquiry_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    contact: Mapped[Contact] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(back_populates="conversation")
    progress: Mapped[list[ConversationProgress]] = relationship(
        back_populates="conversation", order_by="ConversationProgress.created_at"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    direction: Mapped[str] = mapped_column(String, nullable=False)
    channel: Mapped[str] = mapped_column(String, nullable=False, default="email")
    from_address: Mapped[str | None] = mapped_column(String, nullable=True)
    to_address: Mapped[str | None] = mapped_column(String, nullable=True)
    subject: Mapped[str | None] = mapped_column(String, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # 이 메일의 한국어 판본. **양쪽 다 씁니다**: 외국어로 온 고객 문의(0045)와, 외국어로
    # 쓰인 우리 회신 초안. 저장하는 이유는 같습니다 — 본문이 바뀌지 않는 한 번역도 바뀌지
    # 않는데, 이것 말고는 캐시가 프로세스 메모리뿐이었고 Render 는 서비스가 잠들 때마다
    # 그것을 비웁니다. 문의 쪽에서 None 은 "아직 안 옮겼다"(폴러가 다시 집습니다), 초안
    # 쪽에서 None 은 "본문이 이미 한국어다" 입니다.
    body_ko: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject_ko: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ``language`` = language the body is CURRENTLY in (a draft is "ko" until the
    # operator translates it). ``target_language`` = language it must be SENT in
    # (the inquiry's language); the send guard enforces body matches it.
    language: Mapped[str] = mapped_column(String, nullable=False, default="ko")
    target_language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending_approval")
    # Operator-selected outgoing signature. NULL/'' = default text signature (LLM
    # writes it into the body). 'none' = no signature. Otherwise an email_templates
    # key (e.g. 'signature_html_ko') whose branded HTML card is attached at send
    # time, replacing the text signature. See integrations/email_html.py.
    signature_key: Mapped[str | None] = mapped_column(String, nullable=True)
    score_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_variant: Mapped[str | None] = mapped_column(String, nullable=True)
    draft_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Lease timestamp for the worker that changed status to ``sending:*``.
    # A different worker may reclaim the row only after this lease is stale.
    send_claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    send_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Why the last send attempt failed. Separate from post_send_sync_error below: that
    # one means "the mail went out and only the bookkeeping failed", which the recovery
    # screen lists apart and acts on differently. Cleared when a retry is claimed.
    send_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Delivery and CRM/Sheets synchronization are separate commits: HubSpot may
    # succeed while a downstream system is temporarily unavailable.
    post_send_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    post_send_sync_attempted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    post_send_sync_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    post_send_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    replied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    smtp_message_id: Mapped[str | None] = mapped_column(String, nullable=True)
    in_reply_to: Mapped[str | None] = mapped_column(String, nullable=True)
    hubspot_engagement_id: Mapped[str | None] = mapped_column(String, nullable=True)
    hubspot_thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hubspot_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    # 이 메일 한 통을 줄인 한 줄. 티켓 요약이 이 줄들을 이어 붙인 것이고, New 를 지난
    # 화면은 본문 대신 이것을 보여 준 뒤 「전체보기」로 본문을 엽니다.
    summary_line: Mapped[str | None] = mapped_column(String(300), nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    slack_notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    slack_notification_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    slack_notification_attempts: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_messages_status_scheduled", "status", "scheduled_at"),
        Index("ix_messages_status_claimed", "status", "send_claimed_at"),
        Index(
            "ix_messages_post_send_sync",
            "status",
            "post_send_synced_at",
            "post_send_sync_attempted_at",
        ),
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    approvals: Mapped[list[Approval]] = relationship(back_populates="message")


# 고객에게 **실제로 나간** 답변의 status. 히스토리는 이 집합만 셉니다 — 검토 대기로 남은
# 초안, 종료된 초안, 발송 실패는 우리 안에서만 있던 문서라, 그걸 히스토리에 넣으면 나중에
# 읽는 사람이 보낸 적 없는 답변을 보낸 것으로 셉니다(2026-08-19 운영자 지시).
#
# `test_sent` 가 여기 있는 이유: 안전 모드에서 테스트 주소로 돌린 것이지만 **우리가 답을
# 썼고 그 내용이 이것이었다**는 사실은 실재합니다. 고객에게 정말 간 것만 세는 자리는
# `sent` 하나이고, 그 구분은 send_worker 가 지킵니다.
DELIVERED_STATUSES = frozenset({"sent", "test_sent", "delivery_unknown"})


class ConversationProgress(Base):
    """Append-only, dated processing log for a conversation ("처리경과").

    The operator's rule: existing entries are NEVER edited — only new ones are
    appended. So there is INSERT-only code against this table (no UPDATE/DELETE),
    and each row stamps its own ``created_at``. ``kind`` is a short machine tag
    (inbound / draft / reply / note; legacy rows may use auto_ack), ``detail`` the human line shown.
    """

    __tablename__ = "conversation_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="note")
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    conversation: Mapped[Conversation] = relationship(back_populates="progress")


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    approver: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    diff: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    message: Mapped[Message] = relationship(back_populates="approvals")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (Index("ix_events_kind", "kind"),)


class InboundJob(Base):
    """Durable, idempotent work item for one HubSpot inbound ticket."""

    __tablename__ = "inbound_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    __table_args__ = (Index("ix_inbound_jobs_ready", "status", "available_at"),)


class IntegrationCredential(Base):
    """Encrypted delegated credentials for operator-owned integrations."""

    __tablename__ = "integration_credentials"

    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class PolicySource(Base):
    """정책·지식 문서 한 편. **원본이 여기 있습니다.**

    한동안 이 행은 노션 페이지를 가리키는 등록부였습니다. 노션에서 자동으로 받아 오는
    경로가 전부 막혀(설계 문서 참고) 사람이 본문을 붙여넣게 되었으므로, 이제 이 행이
    원본이고 위쪽에 아무것도 없습니다 — ``notion_url`` / ``last_synced_at`` /
    ``last_error`` 는 0050 에서 지웠습니다.

    ``mode`` decides how the copy is used:
      ``rules``     — always applied, concatenated into the LLM system instruction
                      (this is what ``company_rules/*.md`` used to be).
      ``knowledge`` — offered to the per-inquiry document router. 라우터가 이 행을
                      **직접** 읽습니다 — 한동안 ``knowledge_documents`` 라는 사본 표가
                      있었는데, 칸이 하나도 자기 것이 아니어서 2026-08-27 에 지웠습니다.
    """

    __tablename__ = "policy_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String, nullable=False)
    # 이 문서의 신원. 제목에서 만든 32-hex 해시(같은 제목을 두 번 만들면 새 행이 아니라
    # 충돌이 되도록)이거나, 예전 파일 시드가 남긴 ``file:01_tone.md`` 입니다. 제목을 바꿔도
    # 지식 문서 사본이 따라오도록 슬러그는 이 값에서 나옵니다.
    doc_key: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String, nullable=False, default="knowledge")
    # Ordering for mode='rules': the system instruction is read top to bottom.
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    # 이 문서를 근거로 회신할 때의 메일 제목. 비면 RE: 고객이 쓴 제목입니다. 본문 안에
    # "Subject: ..." 로 적으면 모델이 그 줄을 본문에 옮겨 적는 일이 생겨서 열로 뺐습니다.
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    # **언제 이 문서를 쓰는가.** 문서를 고르는 것은 모델이고, 모델이 볼 수 있는 것은 본문이
    # 아니라 인덱스 한 줄(slug·title·categories·tags·summary)입니다. 이 칸을 채우면 그것이
    # 그 요약이 되고, 비우면 예전처럼 본문 앞부분이 잘려 들어갑니다. 본문 맨 위에 용도를 적어
    # 두던 방식은 노션에서 다시 붙여넣을 때마다 그 줄이 날아갔습니다.
    usage_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 이 문서가 언제 기준인가. 적으면 그 값, 안 적으면 edited_at 이 대신합니다 — 오늘
    # 붙여넣은 넉 달 된 정책이 "최신"으로 보이지 않게 하는 것이 요점입니다. "YYYY-MM-DD".
    effective_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # 콘솔에서 본문을 고친 시각. 다음 업로드가 파일 내용으로 되돌리므로, 화면이 그렇게
    # 말해 줄 수 있도록 남깁니다 (조용히 사라지는 것이 문제이지 덮어쓰는 것 자체가 아님).
    edited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 몇 번째 판인가. 저장할 때마다 1씩. 화면이 「v3」로 그리고, 판본 목록이 그 번호로
    # 정렬됩니다 — ``email_templates.version`` 과 같은 뜻입니다.
    #
    # ``server_default`` 가 붙어 있는 이유: 씨앗 마이그레이션(0043 등)이 이 표에 **raw SQL**
    # 로 넣는데, 그 INSERT 는 이 열을 모릅니다. 파이썬 쪽 기본값만 두면 새 DB 의 seed 가
    # `NOT NULL constraint failed` 로 죽습니다.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    # The synced copy. NULL until the first successful sync.
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 지운 시각. 지우면 행이 사라지는 대신 ``status='deleted'`` 가 되고 여기 시각이 박히며,
    # 목록에 일주일 남았다가 청소됩니다 — 읽는 쪽은 전부 이미 ``status='active'`` 만 보므로
    # 이 한 칸으로 "언제까지 되돌릴 수 있나" 가 정해집니다. src/db/soft_delete.py
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class EmailTemplate(Base):
    """Editable email building-block templates (signature, greeting, footer, ...).

    Stores reusable snippets keyed by ``key``, optionally scoped per ``language``
    ("ko" | "en" | "all"). Only ``active`` rows are surfaced to the send path via
    the lookup helper. Every edit snapshots the prior state into
    ``document_revisions`` — the same table policy documents use.
    """

    __tablename__ = "email_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    # 메일 제목. 제목이 있는 것은 접수확인뿐입니다 — 서명·링크·담당자 이름에는 제목이라는
    # 것이 없고, 답변 메일 형식은 뼈대일 뿐 메일이 아닙니다. 비면 RE: 고객 제목입니다.
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    language: Mapped[str] = mapped_column(String, nullable=False, default="all")
    channel: Mapped[str] = mapped_column(String, nullable=False, default="email")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    # ``is_default`` 는 0060 에서 지웠습니다. 어느 서명을 쓸지는 초안마다 검토 화면에서
    # 고르고, 아무것도 안 고르면 회사 규칙의 텍스트 서명이 붙습니다.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    author: Mapped[str | None] = mapped_column(String, nullable=True)
    # 지운 시각 — PolicySource 와 같은 뜻입니다. src/db/soft_delete.py
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class DocumentRevision(Base):
    """이전 판본 한 벌. **템플릿과 정책 문서가 같은 표를 씁니다.**

    새 행은 고치기 **전**에 쓰입니다. 그래서 이 표의 맨 위 행은 「지금 본문」이 아니라
    「직전 본문」이고, 되돌릴 때 꺼내는 것이 그것입니다.

    표가 하나인 이유: 운영자가 콘솔에서 고치는 글은 두 종류(이메일 템플릿 · 정책 문서)인데
    보고 싶은 것은 같습니다 — 언제, 누가, 무엇을, 그때 본문은 무엇이었나. 표를 종류마다
    두면 읽는 화면도 라우트도 둘이 되고, 둘 중 하나에만 이력이 달리는 날이 옵니다. 실제로
    그랬습니다: ``email_template_revisions`` 는 쌓이는데 정책 문서는 아무 이력도 없었고,
    그쪽 몫이라던 ``knowledge_document_revisions`` 는 만들어만 놓고 아무도 안 썼습니다.

    ``document_id`` 는 순수 정수입니다(FK 없음) — 이력은 원본보다 오래 삽니다. 종류마다
    다른 부속 칸(언어·채널·모드·제목…)은 ``extra`` JSON 한 칸에 넣습니다: 두 종류의 칸을
    다 세우면 어느 행에서든 절반이 NULL 이고, 종류가 셋이 되면 또 늘어납니다.
    """

    __tablename__ = "document_revisions"

    KIND_EMAIL_TEMPLATE = "email_template"
    KIND_POLICY_SOURCE = "policy_source"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    document_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # 사람이 안 바꾸는 신원. 템플릿은 ``key``, 정책 문서는 ``doc_key``. 원본 행이 사라진
    # 뒤에도 「무엇의 이력인가」를 말할 수 있어야 합니다.
    doc_key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    # created / edited / deleted / restored, 또는 마이그레이션이 남긴 한 줄.
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited_by: Mapped[str | None] = mapped_column(String, nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class DomainProfile(Base):
    """Cached company profile analyzed from an email domain."""

    __tablename__ = "domain_profiles"

    domain: Mapped[str] = mapped_column(String(255), primary_key=True)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    services: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_market: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_hint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    homepage_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    homepage_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    homepage_fetch_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )


class CustomerProfile(Base):
    """Operator-owned CRM fields that HubSpot does not reliably provide yet."""

    __tablename__ = "customer_profiles"

    contact_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True
    )
    customer_state: Mapped[str] = mapped_column(String(32), nullable=False, default="negotiation")
    pipeline_stage: Mapped[str] = mapped_column(String(32), nullable=False, default="new")
    lead_temperature: Mapped[str | None] = mapped_column(String(16), nullable=True)
    next_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_seq: Mapped[str | None] = mapped_column(String(128), nullable=True)
    current_plan: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 티켓 세부 내역의 「플랜 정보」가 읽는 나머지 셋 (0094). 그 패널은 허브스팟을 그때그때
    # 읽다가 우리 행을 읽는 것으로 바뀌었고, 다섯 칸이 한 카드에 서는데 둘만 여기 있으면
    # 그 카드가 두 곳에서 값을 모아야 한다.
    plan_tier: Mapped[str | None] = mapped_column(String(64), nullable=True)
    space_seq: Mapped[str | None] = mapped_column(String(128), nullable=True)
    plan_seq: Mapped[str | None] = mapped_column(String(128), nullable=True)
    qualification: Mapped[str | None] = mapped_column(String(16), nullable=True)
    lost_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class CustomerInteraction(Base):
    """Normalized manual or synchronized touchpoint across customer channels."""

    __tablename__ = "customer_interactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    # ``note`` is "no single direction" — a record written up as one exchange. The form
    # offers 발송·수신·주고받음 and defaults to it, so an untouched record keeps that
    # meaning; the other two are the values HubSpot-synced rows already carry, on purpose
    # (a third vocabulary would make old and new rows say the same thing differently).
    direction: Mapped[str] = mapped_column(String(16), nullable=False, default="note")
    handler: Mapped[str | None] = mapped_column(String(120), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(300), nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    artifact_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 수주 고객의 몇 차 계약에 대한 기록인지. 비어 있으면 협상 단계(계약 전) 기록입니다 —
    # 이 타임라인은 고객 단위라 계약보다 먼저 시작합니다.
    contract_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    happened_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class ContractRecord(Base):
    """Contract, invoice, and payment facts used by customer success and renewal views."""

    __tablename__ = "contract_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Snapshot of the inquiry's stable Sheets key. Never derive an order from the
    # contact-level legacy key when a contact has several inquiries.
    sheet_client_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    plan: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="KRW")
    payment_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    contract_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    payment_due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    language_pairs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    unit_price: Mapped[str | None] = mapped_column(String(128), nullable=True)
    quote_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    invoice_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Sheet-specific operational fields stay flexible without duplicating the
    # entire external workbook schema as database columns.
    sheet_fields: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sheet_order_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sheet_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class User(Base):
    """Operator who can access the web UI (Google OAuth).

    This table is the ONLY operator directory — no env-var allowlist exists.
    Identity = verified Google email on ALLOWED_EMAIL_DOMAIN. ``approved`` is the
    access gate: the first sign-in on an empty table becomes an approved admin, and
    everyone after lands here as approved=False until an admin approves them in
    /settings/users. ``name`` auto-attributes knowledge edits and message approvals.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    picture: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# --------------------------------------------------------------------------- #
# 수주 고객 — 고객(Client) 하나 아래 계약이 쌓이는 장부
#
# ``Contact`` 는 **이메일 신원**이라 이 자리에 못 씁니다. Outbound·Interactive·AX 고객은
# 문의를 보낸 적이 없어 이메일도 티켓도 없고, 반대로 한 회사에 담당자가 여럿이면 Contact 는
# 여럿인데 고객은 하나입니다. 그래서 ``Client`` 가 따로 있고, 인바운드 고객만 Contact 를
# 가리킵니다.
#
# 계약에 딸린 것(플랜·크레딧·결제·클레임)은 ``Client ID + 계약 차수`` 로 묶입니다. 소통
# 히스토리만 예외로 **고객 단위**인데, 협상 단계 대화가 계약보다 먼저 쌓이기 때문입니다 —
# 그래서 새 테이블을 만들지 않고 기존 ``CustomerInteraction`` 을 그대로 씁니다.
# --------------------------------------------------------------------------- #
class Client(Base):
    """수주 고객 한 곳. 재계약해도 이 행은 그대로입니다."""

    __tablename__ = "clients"

    # 화면과 시트가 함께 쓰는 번호 그대로가 기본 키입니다. 번호대가 곧 고객 종류라
    # (1000 Inbound / 2000 GTM Outbound / 3000 Interactive / 4000 AX / 9000 레거시),
    # 종류는 **저장하지 않고** 이 값에서 파생합니다 — 두 군데 두면 서로 달라집니다.
    client_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(64), nullable=True)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    department: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 고객사 **측** 담당자입니다. 우리 쪽 담당은 ``owner``.
    contact_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    contact_info: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_won_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # **장부에서 내린 날.** NULL 이면 정상입니다. 계약이 없어 활성 고객이 아닌데 행과
    # 번호는 살려 둬야 하는 고객이 여기 옵니다 — Won 에 잘못 올라갔다가 다른 단계로
    # 옮겨진 건. 지우지 않는 이유는 **번호**입니다: 그 번호를 문의·연락처가 들고 있고
    # 워크북의 다른 탭과 Inbound DB 가 그 행을 조회해 회사명을 가져옵니다. 지우면 그
    # 연결이 통째로 끊깁니다. 계약이 들어오면 이 칸이 비워지고 다시 올라옵니다.
    retired_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # 플랜 상태(사용중 / 세팅중 / 사용 중단)는 **열이 아닙니다.** 계약 기간에서 나옵니다 —
    # `won.plan_status`. 저장해 두던 시절에는 계약이 끝나도 누가 손으로 바꿔 주기 전까지
    # 「사용중」이 남았고, 시트에도 그 값이 그대로 실려 나갔습니다(이관 0067).
    owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # 인바운드 고객만 채워집니다. 나머지는 NULL 이 정상입니다.
    contact_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    contracts: Mapped[list["ClientContract"]] = relationship(
        back_populates="client", cascade="all, delete-orphan", order_by="ClientContract.seq"
    )


class ClientContract(Base):
    """계약 한 건. 재계약은 새 고객이 아니라 같은 고객의 다음 차수입니다.

    Perso 계정·플랜은 계약과 1:1 이라 여기 같이 둡니다. 시트에서 탭이 갈려 있는 것은
    스프레드시트 사정이지 관계가 아닙니다 — 테이블을 나누면 항상 붙어 다니는 조인이 하나
    늘 뿐입니다.
    """

    __tablename__ = "client_contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("clients.client_id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 인바운드로 들어온 계약에만 붙습니다. 1차는 티켓이 있고 2차는 없는 것이 정상입니다.
    ticket_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    deal_type: Mapped[str] = mapped_column(String(8), nullable=False, default="MRR")
    starts_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    ends_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # 복수 선택입니다. 화면에서 " + " 로 이어 보여주고 저장은 배열로 — 문자열로 저장하면
    # "직접 계약 / DocuSign + 세금계산서 발행" 을 다시 쪼개야 필터가 됩니다.
    doc_types: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 계약 크레딧은 **입력**입니다. 계약서에 적히는 것이 금액과 크레딧이고, 분당 단가가
    # 그 둘에서 나옵니다(`won.unit_price`). 방향이 반대였던 시절에는 반올림한 단가로
    # 계산한 크레딧이 계약서의 크레딧과 어긋났습니다(이관 0068).
    credits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="KRW")
    # **통화마다 채워지는 칸은 하나뿐입니다.** 원화 계약은 공급가만 받고 총액은 +10% 로
    # 계산하고(`won.total_amount`), 그 외 통화는 부가세가 없어 총액만 받습니다. 둘 다
    # 채우면 어느 쪽이 기준인지가 계약마다 달라집니다 — 저장 경로가 안 쓰는 쪽을 비웁니다.
    amount_incl_vat: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    amount_excl_vat: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    # **원화 계약에서만 뜻이 있습니다:** 계약서에 적힌 금액이 VAT 포함인가. 계약서마다
    # 다르게 적히는데(공급가 + 부가세 별기 / 총액 일괄), 어느 쪽인지 모르면 분당 단가가
    # 계약마다 10% 씩 달라집니다. 켜면 받은 금액이 곧 총액이자 단가의 기준이고, 끄면
    # 예전과 같이 공급가를 받아 총액을 +10% 로 계산합니다. 다른 통화는 부가세가 없어
    # 이 값을 보지 않습니다 — 총액만 받습니다.
    vat_included: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # **부가세가 붙는 계약인가.** 기준은 통화가 아니라 고객입니다 — 국내 법인이면 해당,
    # 그 외에는 미해당. 한동안 `won.is_krw` 가 이 판단을 대신했는데(원화면 부가세가 있다),
    # 통화와 늘 같이 가지는 않습니다. 해당이면 포함·미포함 두 금액이 다 저장되고, 미해당이면
    # 금액은 하나뿐이라 `vat_included` 는 볼 것이 없습니다.
    #
    # **NULL 은 「아직 안 고름」입니다** — 그때는 예전 규칙대로 통화로 추정합니다
    # (`won.vat_applicable`). 이 칸이 생기기 전의 행 수백 개를 이관이 손대지 않아도 금액이
    # 안 움직이는 이유이고, 새 폼은 언제나 값을 보냅니다. NOT NULL DEFAULT false 로 두면
    # 그 옛 원화 계약이 전부 「미해당」이 되어 총액이 10% 씩 내려앉습니다.
    vat_applicable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # 중도 해지일. 플랜은 만료일과 이 날짜 중 **빠른 쪽**에서 끝납니다.
    terminated_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # **그 계약에 적용할 환율과 기준 날짜.** 결제 회차에도 같은 이름의 칸이 있는데 뜻이
    # 다릅니다: 저쪽은 **입금액을 환산한** 환율(그날 실제로 받은 돈), 이쪽은 **계약 금액을
    # 환산할** 환율입니다. 비워 두면 저장할 때 계약일 고시가로 채웁니다.
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    fx_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # 크레딧 사용량 — 예상 환불 금액의 분자입니다. **자동으로 안 채웁니다**: 제품 쪽에서
    # 가져오는 경로가 아직 없고, 없는 값을 0 으로 두면 「하나도 안 썼으니 전액 환불」이
    # 되어 해지월 매출이 통째로 음수가 됩니다. 비어 있으면 환불액을 계산하지 않습니다.
    credits_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payment_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    installments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_payment_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    billing_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # `renewal_plan` · `stop_reason` · `memo` 가 여기 있었습니다 — 상세 화면의 「갱신 · 비고」
    # 패널이 쓰던 세 칸입니다. 콘솔에서 뺐고 열도 지웠습니다(이관 0073). 워크북의 그 세 열은
    # 그대로 남아 손으로 적는 자리가 됩니다 — 시트는 운영자의 것입니다.
    # YYYY-MM. 비우면 계약 시작월부터 인식합니다 (MRR 만 해당).
    revenue_from: Mapped[str | None] = mapped_column(String(7), nullable=True)

    # --- Perso 계정 · 플랜 (계약과 1:1) ---
    plan: Mapped[str | None] = mapped_column(String(32), nullable=True)
    plan_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    perso_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    plan_starts_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    plan_ends_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    invite_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    queue_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    concurrent_jobs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    space_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    space_seq: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    client: Mapped["Client"] = relationship(back_populates="contracts")
    credit_grants: Mapped[list["ContractCreditGrant"]] = relationship(
        back_populates="contract",
        cascade="all, delete-orphan",
        order_by="ContractCreditGrant.no",
    )
    payments: Mapped[list["ContractPayment"]] = relationship(
        back_populates="contract",
        cascade="all, delete-orphan",
        order_by="ContractPayment.no",
    )

    __table_args__ = (
        # 같은 고객에 같은 차수가 둘 있으면 어느 것이 2차인지 화면이 못 정합니다.
        Index("ux_client_contract_seq", "client_id", "seq", unique=True),
    )


class ContractCreditGrant(Base):
    """크레딧 지급 회차. 계약 크레딧과 **별개**입니다 — 테스트·보상으로 계약분을 넘길 수 있습니다."""

    __tablename__ = "contract_credit_grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("client_contracts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 예정일이기도 하고 실제 지급일이기도 합니다. 미지급 중 가장 빠른 날 = 다음 지급일.
    grant_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    granted_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)

    contract: Mapped["ClientContract"] = relationship(back_populates="credit_grants")


class ContractPayment(Base):
    """분납 회차 하나."""

    __tablename__ = "contract_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("client_contracts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    paid_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # **그 날의** 환율을 행에 박아 둡니다. 오늘 환율로 과거 입금을 다시 환산하면 지난달
    # 매출이 이번 달에 바뀝니다. 주말·공휴일 입금은 직전 영업일(보통 금요일) 고시가입니다.
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    fx_on: Mapped[str | None] = mapped_column(String(10), nullable=True)

    contract: Mapped["ClientContract"] = relationship(back_populates="payments")



class PendingWon(Base):
    """수주 전환 대기 — Won 으로 바뀐 티켓이 쌓이는 곳.

    바로 고객 목록에 넣지 않는 이유: 계약 정보가 없는 행이 요약 카드의 활성 고객 수와 예상
    MRR 을 오염시킵니다. 그리고 Won → Negotiating 롤백은 여기서 내리면 끝입니다.
    """

    __tablename__ = "pending_won"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 티켓에 물려 있던 Client ID. 인바운드 문의 시점에 발급되므로 보통 이미 있습니다.
    client_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    conversation_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    # HubSpot 파이프라인의 PoC / Contract / Renewal. 수주 유형(MRR/PoC)은 이 값에서
    # 자동으로 정하지 않고 담당자가 고릅니다 — Contract·Renewal 이 둘 다 MRR 이라
    # 되묻지 않으면 틀린 채로 굳습니다.
    won_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    won_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # pending / done / dismissed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
