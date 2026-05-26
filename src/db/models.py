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
    whatsapp_opt_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    conversations: Mapped[list[Conversation]] = relationship(back_populates="contact")


class Prospect(Base):
    __tablename__ = "prospects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    normalized_email: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True, unique=True
    )
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    company: Mapped[str | None] = mapped_column(String, nullable=True)
    domain: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    country: Mapped[str | None] = mapped_column(String, nullable=True)
    icp_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    icp_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="collected")
    contact_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    follow_up_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_prospects_domain_fullname", "domain", "full_name"),
    )

    conversations: Mapped[list[Conversation]] = relationship(back_populates="prospect")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    prospect_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("prospects.id", ondelete="SET NULL"), nullable=True
    )
    topic: Mapped[str | None] = mapped_column(String, nullable=True)
    stage: Mapped[str] = mapped_column(String, nullable=False, default="initial")
    last_outgoing_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_incoming_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    hubspot_ticket_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    contact: Mapped[Contact] = relationship(back_populates="conversations")
    prospect: Mapped[Prospect | None] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(back_populates="conversation")


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
    language: Mapped[str] = mapped_column(String, nullable=False, default="ko")
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending_approval")
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


class CountrySendWindow(Base):
    """Per-country optimal sending time windows for outbound scheduling."""

    __tablename__ = "country_send_windows"

    country_code: Mapped[str] = mapped_column(String, primary_key=True)
    country_name: Mapped[str] = mapped_column(String, nullable=False)
    timezone: Mapped[str] = mapped_column(String, nullable=False)
    hours_start: Mapped[int] = mapped_column(Integer, nullable=False)
    hours_end: Mapped[int] = mapped_column(Integer, nullable=False)
    avoid_days_of_week: Mapped[list | None] = mapped_column(JSON, nullable=True)


class KnowledgeDocument(Base):
    """Stores knowledge base documents for web UI editing and LLM prompt context."""

    __tablename__ = "knowledge_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    categories: Mapped[list | None] = mapped_column(JSON, nullable=True)
    scope: Mapped[str] = mapped_column(String, nullable=False, default="both")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class OutboundIntent(Base):
    """Stores natural-language queries routed to outbound sources."""

    __tablename__ = "outbound_intents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_query: Mapped[str] = mapped_column(Text, nullable=False)
    routed_source: Mapped[str] = mapped_column(String, nullable=False)
    routed_filters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending_user_input")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


class ICPRule(Base):
    """Per-source ICP scoring criteria in markdown."""

    __tablename__ = "icp_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    criteria_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class EmailSuppression(Base):
    """Tracks unsubscribed/bounced/complaint emails to prevent re-sending."""

    __tablename__ = "email_suppression"

    email: Mapped[str] = mapped_column(String, primary_key=True)
    reason: Mapped[str] = mapped_column(String, nullable=False, default="unsubscribe")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False, index=True)
