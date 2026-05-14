# 011 — Reply check job + follow-up drafts

## Goal
Hourly job that detects replies on sent messages and queues follow-ups when silent for N days.

## Steps
1. `src/agents/reply_check.py — run()`:
   - For each `messages` row WHERE status=sent AND replied=false AND sent_at > now()-30d:
     - Query HubSpot engagements list since `sent_at` for that contact.
     - If any inbound engagement from `to_address`: set `replied=true`, update conversation.last_incoming_at.
     - Else if `now() - sent_at > FOLLOWUP_AFTER_DAYS` and conversation.follow_up_count < 2:
       - Draft via LLM prompt `outbound/followup.md` (gets the previous thread).
       - Insert new `messages` row status=pending_approval, channel=email.
       - Notify approver.
       - Increment `conversation.follow_up_count`.
2. Add prompt file `src/llm/prompts/outbound/followup.md`.

## Verification
- `tests/test_reply_check.py` seeds 1 message without reply but inside window → no follow-up; 1 message past window → follow-up drafted.

## Done when
- Reply detection + follow-up enqueued path both tested.
