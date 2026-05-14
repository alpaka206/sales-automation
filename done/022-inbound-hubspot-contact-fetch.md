# 022 — Inbound agent: real HubSpot contact fetch

## Why

`InboundAgent._fetch_contact()` only echoes fields from the webhook event
payload. The plan requires fetching full contact data from HubSpot:
- Name, email, phone, company, country, lifecycle stage, owner
- Last N emails on the contact timeline (N=5)
- Associated deal/ticket summary

Without this, classification and scoring operate on minimal data, and the
LLM drafts replies without knowing the conversation history.

## What to do

1. In `src/integrations/hubspot.py`, add or complete:
   - `get_contact(contact_id) -> ContactDTO` — fetches full properties.
   - `get_recent_emails(contact_id, limit=5) -> list[EngagementDTO]` — fetches
     email engagement bodies/subjects (current code returns empty DTOs).
   - `get_associated_deals(contact_id) -> list[DealDTO]` (new).
   - All methods should handle `HubSpotNotConfigured` gracefully.

2. In `src/agents/inbound.py`, update `_fetch_contact()`:
   - Try to call HubSpot client to get full contact + email history + deals.
   - If HubSpot not configured, fall back to event payload (current behavior).
   - Return enriched dict with `recent_emails` and `deal_summary` keys.

3. Pass enriched data into `_classify()` and `_draft_reply()` prompts:
   - Add `recent_emails` and `deal_summary` template variables to prompts.
   - Update `src/llm/prompts/inbound/classify.md` and `draft_reply.md` to
     include these if present.

4. Handle async: HubSpot client methods are async. Either:
   - Make `_fetch_contact` synchronous by using `httpx.Client` (sync) in a
     dedicated sync method on HubSpotClient, or
   - Refactor inbound agent to use async (bigger change — prefer sync wrapper).

## Acceptance criteria

- With `HUBSPOT_API_KEY` set, inbound agent fetches full contact, recent emails, and deals from HubSpot.
- Without `HUBSPOT_API_KEY`, inbound agent falls back to event payload.
- Prompts receive enriched context when available.
- All existing tests pass (they mock HubSpot, so no network calls).

## Verify

```bash
pytest tests/test_inbound_flow.py tests/test_hubspot.py -v
```

Add a test that mocks `HubSpotClient.get_contact` returning full data and
verifies the enriched dict is passed to the LLM.
