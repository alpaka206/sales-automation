# 01 — Inbound Agent

## Purpose

Read inbound inquiries that arrive in HubSpot (form submissions, inbound emails routed to a HubSpot inbox, or new contacts created from website). Classify, score, draft a reply, queue for approval, send after approval, and log everything back to HubSpot.

## Inputs

A webhook payload from n8n that contains at minimum:

```json
{
  "event_type": "contact.creation" | "email.received" | "form.submission",
  "object_id": "<hubspot contact or thread id>",
  "occurred_at": "2026-05-14T10:32:00Z"
}
```

The agent then **pulls full data** from HubSpot itself rather than trusting webhook payload — this keeps it idempotent if the webhook fires twice.

## Steps

1. **Fetch context**
   - HubSpot Contact: name, email, phone, company, country, lifecycle stage, owner, recent properties.
   - Last N emails on the contact timeline (N=5).
   - If a deal/ticket is associated, fetch its summary too.

2. **Classify** — LLM returns one of:
   `purchase_inquiry`, `partnership`, `pricing_question`, `support`, `recruiting`, `spam`, `other`
   Prompt: `src/llm/prompts/inbound/classify.md`
   Output schema:
   ```json
   { "category": "purchase_inquiry", "reasoning": "..." }
   ```

3. **Score (0–100)**
   - Rule-based base score: country in target list (+15), known competitor domain (-30), generic gmail/yahoo (-10), enterprise domain (+15).
   - LLM adjustment: urgency, fit, signal of budget → ±0–20.
   - Final clipped to [0, 100].
   - Output stored on `contacts.score` and `messages.score_snapshot`.

4. **Pick channel**
   - If contact has a phone with country code AND `whatsapp_opt_in=true` AND `WHATSAPP_ENABLED=true` → channel = whatsapp.
   - Else → channel = email.
   - Else (no email either, edge case) → channel = none, flag for human.

5. **Draft reply**
   - Prompt: `src/llm/prompts/inbound/draft_reply.md`
   - The prompt receives:
     - Contact summary
     - Last incoming message text
     - Category + score
     - All `company_rules/*.md` concatenated
   - Output:
     ```json
     {
       "subject": "...",        // for email only; empty for whatsapp
       "body": "...",           // plain text, no html
       "language": "ko" | "en",
       "tone_notes": "..."      // optional, surfaced in approval card
     }
     ```

6. **Persist** (DB)
   - Upsert `contacts` row.
   - Insert `messages` row: `direction=outbound`, `status=pending_approval`, `channel=email|whatsapp`, all draft fields.
   - Insert `conversations` row if not exists (one per contact × topic).

7. **Notify approver**
   - Build a Slack Block Kit message (or Teams card): subject, snippet, score, category, language, Approve / Edit / Reject buttons.
   - Post via `integrations/slack.py` or `integrations/teams.py`.

8. **Handle approval webhook**
   - `POST /approve/{message_id}` → status=approved → send.
   - `POST /reject/{message_id}` → status=rejected, log reason.
   - `POST /edit/{message_id}` with new body → status back to pending_approval, re-notify.

## Send

- `sender.send_email(message)`: uses HubSpot Engagements API or SMTP based on `EMAIL_PROVIDER`.
- `sender.send_whatsapp(message)`: stub — raises `NotImplementedError` unless `WHATSAPP_ENABLED=true` and creds exist.
- Always also calls `integrations.hubspot.log_engagement` to record on the contact timeline.

## Acceptance test

A `tests/test_inbound_flow.py` that:
1. POSTs a fake webhook to `/webhook/hubspot/inbound`.
2. Asserts a DB row exists in `messages` with `status=pending_approval`.
3. Asserts Slack mock was called once.
4. POSTs `/approve/{id}` with mocked sender, asserts status=sent and HubSpot log call was made.
