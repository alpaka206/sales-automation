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

## Workflows / 워크플로우

| # | Name | 설명 | Trigger | Endpoint |
|---|------|------|---------|----------|
| 01 | Inbound Webhook | HubSpot 인바운드 문의 수신 → BE 분석 요청 | HubSpot webhook POST | `/webhook/hubspot/inbound` |
| 02 | Outbound Cron | 매일 09시 아웃바운드 발굴 실행 | Daily 09:00 | `/run/outbound` |
| 03 | Reply Check | 매시간 수신 답장 확인 및 후속 발송 판단 | Hourly | `/run/reply_check` |
| 04 | Approval Card | 승인 대기 메시지를 Slack 카드로 전송 | BE webhook POST | Slack message |
| 05 | Daily Report | 매일 18시 일간 활동 리포트 생성 | Daily 18:00 | `/run/report?kind=daily` |
| 06 | Weekly Report | 매주 토요일 주간 리포트 생성 | Saturday 09:00 | `/run/report?kind=weekly` |
| 07 | Healthcheck | 시스템 상태 점검 (DB·LLM·API 연결 확인) | Manual / Cron | `/healthz` |

## Auth

All HTTP Request nodes include `X-Internal-Token` header. The FastAPI backend rejects requests without a valid token.
