"""Pydantic request models for the API layer."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class HubSpotWebhookEvent(BaseModel):
    """Single event from a HubSpot webhook payload."""

    subscriptionType: str
    objectId: int
    occurredAt: int | None = None
    eventId: int | None = None
    propertyName: str | None = None
    propertyValue: str | None = None


class InboundWebhookBody(BaseModel):
    """Legacy internal format — kept for backward compatibility."""

    event_type: str
    object_id: str
    occurred_at: str | None = None


class OutboundRunBody(BaseModel):
    """Request to run one outbound source. `filters` is source-specific (query, region, ...)."""

    source: str
    filters: dict | None = None


class ApprovalBody(BaseModel):
    """Approve/edit/reject action on a pending message (see /approve/{message_id})."""

    approver: str
    action: Literal["approve", "edit", "reject"]
    edited_body: str | None = None
    reason: str | None = None
    # HMAC token bound to message_id; required unless APPROVAL_REQUIRE_TOKEN=false.
    token: str | None = None
