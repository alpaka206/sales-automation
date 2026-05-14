# 06 — n8n Workflows

n8n acts as the **orchestrator** for triggers, retries, scheduling, branching, and approval cards. The FastAPI BE holds business logic and DB; n8n calls into it via HTTP.

## Workflows to export (commit JSON to `n8n_workflows/`)

### `01_inbound_webhook.json`
- **Trigger:** HubSpot Webhook node, events: `contact.creation`, `contact.propertyChange:lifecyclestage`, optionally `email.received` if you have Conversations API.
- **Action:** `HTTP Request → POST {APP}/webhook/hubspot/inbound`
- **On failure:** Retry 3× with exponential backoff, then write to `errors` Slack channel.

### `02_outbound_cron.json`
- **Trigger:** Cron node, daily at 09:00 local.
- **Branch:** for each enabled source (`youtube`, `linkedin_csv`, `manual_csv`):
  - `HTTP Request → POST {APP}/run/outbound { source, filters }`

### `03_reply_check.json`
- **Trigger:** Cron node, hourly.
- **Action:** `HTTP Request → POST {APP}/run/reply_check`

### `04_approval_card.json`
- **Trigger:** Webhook node, called by BE with payload `{message_id, subject, snippet, score, category, slack_user}`.
- **Action:** Slack node sends Block Kit message with Approve / Edit / Reject buttons.
- Buttons hit Slack interactivity URL → returns to n8n → n8n calls back `{APP}/approve/{id}` etc.

### `05_daily_report.json`
- **Trigger:** Cron 18:00.
- **Action:** `HTTP Request → POST {APP}/run/report?kind=daily` → Slack post.

### `06_weekly_report.json`
- **Trigger:** Cron Saturday 09:00.
- **Action:** `HTTP Request → POST {APP}/run/report?kind=weekly` → Slack post.

## Auth between n8n and BE

Shared secret header `X-Internal-Token` set via env on both sides (`INTERNAL_API_TOKEN`). BE rejects calls without it.

## How to run n8n locally (free)

```bash
npx n8n
# UI at http://localhost:5678
```

Persists to `~/.n8n/` by default. Set `N8N_BASIC_AUTH_*` for a basic password.

## Importing workflows

Open n8n UI → top-right ⋯ → Import from File → pick `n8n_workflows/01_*.json`.

After import, set:
- HubSpot credentials (use the private app token from `.env`)
- Slack credentials (Slack OAuth token)
- The base URL of your BE (`http://localhost:8000` locally)
