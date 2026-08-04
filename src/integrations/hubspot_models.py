"""Typed DTOs for HubSpot CRM objects (contacts, engagements, deals, tickets)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ContactDTO(BaseModel):
    """A HubSpot contact, flattened to the fields the agents actually use."""

    id: str
    email: str | None = None
    firstname: str | None = None
    lastname: str | None = None
    company: str | None = None
    phone: str | None = None
    country: str | None = None
    lifecyclestage: str | None = None


class EngagementDTO(BaseModel):
    """A timeline engagement (email, note, etc.) on a contact."""

    id: str
    type: str
    subject: str | None = None
    body: str | None = None
    timestamp: datetime | None = None


class DealDTO(BaseModel):
    """A deal associated with a contact — used to give the LLM sales context."""

    id: str
    name: str | None = None
    stage: str | None = None
    amount: str | None = None


class TicketDTO(BaseModel):
    """A support/inbound ticket — carries the inquiry body for ticket-based inbound."""

    id: str
    subject: str | None = None
    content: str | None = None
    pipeline: str | None = None
    pipeline_stage: str | None = None
    priority: str | None = None
    source_type: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    primary_contact_id: str | None = None
    # HubSpot's own `hs_all_associated_contact_emails`: the address(es) the ticket is
    # held against. This is the one the reply must go to — the operator's rule is that
    # the answer belongs to the ticket, not to whatever the contact record happens to
    # say. HubSpot returns a semicolon/comma-separated list when several contacts are
    # associated; `contact_email` exposes the first one.
    contact_emails: str | None = None

    @property
    def contact_email(self) -> str | None:
        """First address on the ticket, or None when it carries none."""
        raw = (self.contact_emails or "").replace(";", ",")
        for part in raw.split(","):
            cleaned = part.strip()
            if cleaned:
                return cleaned
        return None
