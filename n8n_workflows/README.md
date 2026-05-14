# n8n Workflows

Starter workflow templates for the sales automation system.

## Import

1. Start n8n: `npx n8n` (UI at http://localhost:5678)
2. Top-right menu -> Import from File -> select the JSON file
3. Set credentials and environment variables (see below)

## Environment Variables (set in n8n Settings -> Variables)

- `BE_BASE_URL`: Your FastAPI backend URL (e.g. `http://localhost:8000`)
- `INTERNAL_API_TOKEN`: Must match `INTERNAL_API_TOKEN` in your `.env`
- `SLACK_APPROVAL_CHANNEL_ID`: Slack channel for approval cards

## Credentials to Configure

- **Slack**: OAuth token for posting approval cards (workflow 04)
- **HubSpot**: Only needed if using HubSpot trigger nodes directly

## Workflows

| # | Name | Trigger | Endpoint |
|---|------|---------|----------|
| 01 | Inbound Webhook | HubSpot webhook POST | `/webhook/hubspot/inbound` |
| 02 | Outbound Cron | Daily 09:00 | `/run/outbound` |
| 03 | Reply Check | Hourly | `/run/reply_check` |
| 04 | Approval Card | BE webhook POST | Slack message |
| 05 | Daily Report | Daily 18:00 | `/run/report?kind=daily` |
| 06 | Weekly Report | Saturday 09:00 | `/run/report?kind=weekly` |

## Auth

All HTTP Request nodes include `X-Internal-Token` header. The FastAPI backend rejects requests without a valid token.
