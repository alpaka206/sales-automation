"""Typed DTOs for HubSpot CRM objects (contacts, engagements, deals, tickets)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ContactDTO(BaseModel):
    id: str
    email: str | None = None
    firstname: str | None = None
    lastname: str | None = None
    company: str | None = None
    phone: str | None = None
    country: str | None = None
    lifecyclestage: str | None = None


class EngagementDTO(BaseModel):
    id: str
    type: str
    subject: str | None = None
    body: str | None = None
    timestamp: datetime | None = None


class DealDTO(BaseModel):
    id: str
    name: str | None = None
    stage: str | None = None
    amount: str | None = None


class TicketDTO(BaseModel):
    id: str
    subject: str | None = None
    content: str | None = None
    pipeline_stage: str | None = None
    priority: str | None = None
    source_type: str | None = None
    created_at: datetime | None = None
    primary_contact_id: str | None = None
