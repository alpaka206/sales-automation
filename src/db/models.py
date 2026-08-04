"""SQLAlchemy ORM models for the sales automation system."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    false as sa_false,
    Boolean,
    DateTime,
    Float,
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
    # was ever worth showing. The category is now transient — it still routes knowledge
    # docs and adjusts the lead score inside one inbound run, but nothing stores it.
    inquiry_subject: Mapped[str | None] = mapped_column(String, nullable=True)
    stage: Mapped[str] = mapped_column(String, nullable=False, default="initial")
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
    # Stable key written into this inquiry's sheet row. Unlike a physical row
    # number it survives operator sorting and inserted rows. Uniqueness is
    # enforced by migration 0035 (a unique index), not at the model level — the
    # 0035 migration test must be able to create_all then insert legacy dupes.
    sheet_client_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
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
    # The Korean the review screen shows for a foreign-language inbound bubble. Stored
    # because a body never changes, so its translation never changes — and the only cache
    # before this was process memory, which Render empties every time the service sleeps
    # (migration 0045). None means "not translated yet", not "no translation needed".
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
    # Delivery and CRM/Sheets synchronization are separate commits: SMTP may
    # succeed while a downstream system is temporarily unavailable.
    post_send_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    post_send_sync_attempted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    post_send_sync_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    post_send_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    replied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    smtp_message_id: Mapped[str | None] = mapped_column(String, nullable=True)
    in_reply_to: Mapped[str | None] = mapped_column(String, nullable=True)
    hubspot_engagement_id: Mapped[str | None] = mapped_column(String, nullable=True)
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
            "ux_messages_one_auto_ack_per_conversation",
            "conversation_id",
            unique=True,
            sqlite_where=text("prompt_variant = 'auto_ack'"),
            postgresql_where=text("prompt_variant = 'auto_ack'"),
        ),
        Index(
            "ix_messages_post_send_sync",
            "status",
            "post_send_synced_at",
            "post_send_sync_attempted_at",
        ),
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    approvals: Mapped[list[Approval]] = relationship(back_populates="message")


class ConversationProgress(Base):
    """Append-only, dated processing log for a conversation ("처리경과").

    The operator's rule: existing entries are NEVER edited — only new ones are
    appended. So there is INSERT-only code against this table (no UPDATE/DELETE),
    and each row stamps its own ``created_at``. ``kind`` is a short machine tag
    (inbound / auto_ack / draft / reply / note), ``detail`` the human line shown.
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


class KnowledgeDocument(Base):
    """Stores knowledge base documents for web UI editing and LLM prompt context.

    ``summary`` and ``tags`` feed the LLM document router (knowledge.py) so the
    model can pick the right docs from a compact index without reading every
    body. ``author``/``version``/``status`` give operators provenance, and every
    edit snapshots the prior state into ``knowledge_document_revisions``.
    """

    __tablename__ = "knowledge_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    categories: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[str] = mapped_column(String, nullable=False, default="both")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class KnowledgeDocumentRevision(Base):
    """Append-only history of knowledge document edits.

    A new row is written *before* each update with the document's prior content,
    so the full change history survives even if the live document is later
    edited or deleted. ``document_id`` is kept as a plain int (no hard FK) so
    revisions outlive their source document.
    """

    __tablename__ = "knowledge_document_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    slug: Mapped[str] = mapped_column(String, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str] = mapped_column(String, nullable=False)
    categories: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[str] = mapped_column(String, nullable=False, default="both")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class PolicySource(Base):
    """A Notion page this console treats as policy, plus the last copy it read.

    The registry an operator maintains on 정책 문서: a label ("Business 플랜 정책") and a
    Notion URL. Policy is owned in Notion, so the console never edits ``body`` — the sync
    overwrites it from the page. Keeping the copy here is what makes a Notion outage a
    non-event: drafting reads this row, never the network.

    ``mode`` decides how the copy is used:
      ``rules``     — always applied, concatenated into the LLM system instruction
                      (this is what ``company_rules/*.md`` used to be).
      ``knowledge`` — offered to the per-inquiry document router, i.e. upserted into
                      ``knowledge_documents`` under ``slug``.
    """

    __tablename__ = "policy_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String, nullable=False)
    notion_url: Mapped[str] = mapped_column(Text, nullable=False)
    # 32-hex, derived from the URL on save so a re-typed URL cannot create a duplicate.
    notion_page_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String, nullable=False, default="knowledge")
    # Ordering for mode='rules': the system instruction is read top to bottom.
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    # The synced copy. NULL until the first successful sync.
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Last failure, kept alongside the good copy so the screen can show "동기화 실패,
    # 지난 사본 사용 중" instead of either hiding the problem or dropping the policy.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class EmailTemplate(Base):
    """Editable email building-block templates (signature, greeting, footer, ...).

    Stores reusable snippets keyed by ``key``, optionally scoped per ``language``
    ("ko" | "en" | "all"). Only ``active`` rows are surfaced to the send path via
    the lookup helper. Every edit snapshots the prior state into
    ``email_template_revisions``, mirroring the knowledge-base pattern.
    """

    __tablename__ = "email_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    language: Mapped[str] = mapped_column(String, nullable=False, default="all")
    channel: Mapped[str] = mapped_column(String, nullable=False, default="email")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    # The signature every new draft is stamped with. A row, not a constant in
    # src/agents/inbound.py — changing who signs the company's mail is an operator
    # decision, and it used to need a code change (migration 0046). A partial unique
    # index in the database keeps exactly one row true.
    # server_default too, not just the Python default: the older migrations seed this
    # table with raw INSERTs that name their own columns, and those cannot know about a
    # column added later.
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_false()
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    author: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class EmailTemplateRevision(Base):
    """Append-only history of email template edits.

    A new row is written *before* each update with the template's prior content,
    so the change history survives even if the live template is later edited or
    deleted. ``template_id`` is a plain int (no hard FK) so revisions outlive
    their source template.
    """

    __tablename__ = "email_template_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    key: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    language: Mapped[str] = mapped_column(String, nullable=False, default="all")
    channel: Mapped[str] = mapped_column(String, nullable=False, default="email")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited_by: Mapped[str | None] = mapped_column(String, nullable=True)
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
    # Only the HubSpot sync sets a real direction now. A hand-written record is the whole
    # exchange summarized once, which has no single direction, so it stores the default —
    # ``handler`` is what a manual record is asked for instead (migration 0044).
    direction: Mapped[str] = mapped_column(String(16), nullable=False, default="note")
    handler: Mapped[str | None] = mapped_column(String(120), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(300), nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    artifact_url: Mapped[str | None] = mapped_column(Text, nullable=True)
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


class LLMUsage(Base):
    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cache_read_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_creation_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False, index=True
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
