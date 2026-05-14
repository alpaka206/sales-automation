# 005 — HubSpot client

## Goal
Thin async wrapper around HubSpot CRM v3 + Conversations API. Cover only the calls we need.

## Steps
1. `src/integrations/hubspot.py` — class `HubSpotClient(token: str)` using httpx.AsyncClient.
2. Methods:
   - `get_contact(id_or_email) -> ContactDTO`
   - `update_contact(id, props: dict)`
   - `list_contact_engagements(contact_id, since: datetime, limit=10) -> list[EngagementDTO]`
   - `create_email_engagement(contact_id, subject, body, sent_at)` — used for logging sent emails into timeline.
   - `send_email(contact_id, subject, body, from_email)` — uses Engagements API single-send endpoint (requires Marketing Hub if sending NEW emails — document fallback to SMTP if account doesn't allow).
3. Pydantic DTOs for inputs/outputs (`ContactDTO`, `EngagementDTO`).
4. Use `httpx.AsyncClient(base_url="https://api.hubapi.com", headers={"Authorization": f"Bearer {token}"})`.

## Verification
- `tests/test_hubspot.py` uses `respx` to mock the HubSpot endpoints. Cover get_contact (200, 404), create_email_engagement, list_contact_engagements pagination.

## Done when
- Tests pass. Token absent → methods raise `HubSpotNotConfigured` cleanly so the rest of the app boots.
