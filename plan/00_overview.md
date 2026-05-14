# 00 — Overview

## Three agents

| Agent | Trigger | Output |
|---|---|---|
| **Inbound** | New HubSpot contact / form / inbound email | Approval card → email or WhatsApp reply |
| **Outbound** | Cron (daily / weekly) per source | Personalized first-contact email after approval |
| **Report** | Cron (daily 18:00, weekly Sat) | Markdown digest posted to Slack/Teams |

## Non-goals (explicit)

- No fully autonomous send. Every outgoing message has a human gate in MVP.
- No LinkedIn scraping. Source: official API or human-curated CSV only.
- No WhatsApp marketing blast. Inbound reply + record only, until Meta Business is approved.
- No multi-tenant. Single company, single team for now.

## End-to-end inbound flow

```
HubSpot form submission
   │ (webhook → n8n)
   ▼
n8n: POST /webhook/hubspot/inbound  →  FastAPI
   │
   ▼
inbound_agent.handle(contact_payload):
   1. fetch full contact + recent emails from HubSpot
   2. classify (purchase / partnership / support / spam) via LLM
   3. score (0–100) via LLM + simple rules
   4. draft reply (email or whatsapp, channel chosen by contact context)
   5. store message (status=pending_approval)
   6. notify approval channel (Slack/Teams)
   ▼
Reviewer clicks Approve in Slack
   │ (webhook → FastAPI /approve/{id})
   ▼
sender.send(message)  →  HubSpot Engagements API or SMTP
   │
   ▼
log to HubSpot timeline + mark message=sent in DB
```

## End-to-end outbound flow

```
Cron (n8n, daily) → POST /run/outbound { source: "youtube" | "linkedin_csv" | ... , filters: {...} }
   │
   ▼
outbound_agent.run(source, filters):
   1. source adapter returns candidate prospects (dicts)
   2. dedup against DB (`prospects` table)
   3. enrich (optional: web fetch homepage, summarize)
   4. LLM scores ICP fit (0–100, JSON schema)
   5. above-threshold → LLM drafts opening email with source-specific prompt
   6. store as pending_approval
   7. notify approval channel
```

Reply check job (cron):
- Every hour, for each `messages` row with `status=sent` and `replied=false` and `sent_at > now() - 30 days`:
  - Query HubSpot for inbound emails from `to_address` since `sent_at`.
  - If found → set `replied=true`, link the inbound to the same conversation.
  - If `sent_at + FOLLOWUP_AFTER_DAYS < now()` and `replied=false` → enqueue follow-up draft.

## Tech choices (locked in)

- Python 3.11+, FastAPI, SQLAlchemy 2.x, pydantic v2
- SQLite locally; DATABASE_URL swap for Postgres later
- httpx (async) for HubSpot/YouTube
- n8n for scheduling, webhooks, branching, approval cards, retries
- ralph_loop + claude CLI for development

## What we do NOT use

- No Celery / Redis (n8n + cron is enough at this scale)
- No Docker (per user request — local Python venv is fine)
- No LinkedIn scraper, no email finder service in MVP
