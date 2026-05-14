# 007 — Inbound agent (real implementation)

## Goal
Implement `src/agents/inbound.py` per `plan/01_inbound_agent.md`. Wire it into `POST /webhook/hubspot/inbound`.

## Steps
1. `InboundAgent.handle(event: dict)`:
   - Idempotency: dedupe on `(event.object_id, event.occurred_at)` via a small `processed_events` table or a `set()` cache (fine for MVP).
   - Fetch contact + last 5 engagements via HubSpotClient.
   - Call LLM `inbound/classify.md` → category + reasoning.
   - Score: rule-based base + LLM adjustment from `inbound/score_adjust.md`.
   - Decide channel (email vs whatsapp per spec).
   - Call LLM `inbound/draft_reply.md` → subject/body/language.
   - Persist Contact (upsert), Conversation (get or create), Message (pending_approval).
   - Notify approver (Slack stub for now — see todo 010).
2. Write the three prompt files under `src/llm/prompts/inbound/`. Keep them short and skeleton-y; iterate later.

## Verification
- `tests/test_inbound_flow.py` end-to-end with mocked HubSpotClient, mocked LLM (canned JSON), mocked Slack: asserts DB rows + Slack call.

## Done when
- Test passes. Webhook route returns 200 within 5s on a stubbed setup.
