# 010 — Approval flow + email/whatsapp senders

## Goal
Implement the actual send once approved. Slack approval card. SMTP and HubSpot email providers behind a single interface.

## Steps
1. `src/integrations/slack.py` — `post_approval_card(message)` builds Block Kit with Approve / Edit / Reject buttons (interactivity URL = n8n webhook).
2. `src/integrations/teams.py` — analogous, builds MessageCard.
3. `src/agents/approval.py — approve(message_id, approver)`:
   - Load message, must be `pending_approval`.
   - Set status `approved` + record in `approvals`.
   - Call `senders.send(message)`.
   - On success: `status=sent`, `sent_at=now()`, log engagement to HubSpot.
4. `src/integrations/senders/__init__.py — send(message)` dispatches on `settings.EMAIL_PROVIDER`:
   - `hubspot` → `HubSpotClient.send_email`
   - `smtp` → `src/integrations/senders/smtp.py` using stdlib `smtplib` + STARTTLS.
5. `src/integrations/senders/whatsapp.py` — stub raising `NotImplementedError` unless `WHATSAPP_ENABLED=true`.

## Verification
- `tests/test_approval.py` with mocked sender + HubSpot → approve flips status, calls sender once, logs engagement.

## Done when
- Approve / reject paths covered, both providers exercised in tests via env override.
