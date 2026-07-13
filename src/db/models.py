"""SQLAlchemy ORM models for the sales automation system."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hubspot_contact_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    normalized_email: Mapped[str] = mapped_column(String, nullable=False, index=True)
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
    whatsapp_opt_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
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
    # Vestigial: the outbound agent (and its prospects table) was removed. Kept as a
    # plain nullable column so existing rows survive; always NULL for inbound threads.
    prospect_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    topic: Mapped[str | None] = mapped_column(String, nullable=True)
    stage: Mapped[str] = mapped_column(String, nullable=False, default="initial")
    last_outgoing_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_incoming_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    hubspot_ticket_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # ISO 639-1 language the customer wrote in — the language every reply in this
    # thread must go out in (enforced in code at send time).
    inquiry_language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Rolling LLM-maintained summary + the customer's standing requests. These are
    # regenerated as the thread evolves (unlike the append-only progress log).
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_requests: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    replied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    smtp_message_id: Mapped[str | None] = mapped_column(String, nullable=True)
    in_reply_to: Mapped[str | None] = mapped_column(String, nullable=True)
    hubspot_engagement_id: Mapped[str | None] = mapped_column(String, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    whatsapp_attempted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    whatsapp_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    whatsapp_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (Index("ix_messages_status_scheduled", "status", "scheduled_at"),)

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


class EmailSuppression(Base):
    """Tracks unsubscribed/bounced/complaint emails to prevent re-sending."""

    __tablename__ = "email_suppression"

    email: Mapped[str] = mapped_column(String, primary_key=True)
    reason: Mapped[str] = mapped_column(String, nullable=False, default="unsubscribe")
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
    """Operator who can access the web UI (Google OAuth, allowlisted).

    Identity = verified Google email on ALLOWED_EMAIL_DOMAIN. ``approved`` is the
    allowlist gate (bootstrap admins + WEB_UI_ALLOWED_EMAILS are auto-approved on first
    login; others land here as approved=False until an admin approves them). ``name`` is
    used to auto-attribute knowledge/ICP edits and message approvals.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    picture: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
