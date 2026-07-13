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


class ApprovalBody(BaseModel):
    """Approve/edit/reject action on a pending message (see /approve/{message_id})."""

    approver: str
    action: Literal["approve", "edit", "reject"]
    edited_body: str | None = None
    reason: str | None = None
    # HMAC token bound to message_id; required unless APPROVAL_REQUIRE_TOKEN=false.
    token: str | None = None
