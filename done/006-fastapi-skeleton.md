# 006 — FastAPI skeleton + webhook routes

## Goal
A running FastAPI app with the routes the agents and n8n need.

## Routes
- `GET /healthz` → `{"ok": true}`
- `POST /webhook/hubspot/inbound` — body `{event_type, object_id, occurred_at}`. Enqueues inbound flow (sync for now; just return 200 fast).
- `POST /run/outbound` — body `{source, filters}`. Triggers outbound agent.
- `POST /run/reply_check` — no body. Triggers reply check job.
- `POST /run/report?kind=daily|weekly` — runs report generator.
- `POST /approve/{message_id}` — body `{approver, action: approve|edit|reject, edited_body?, reason?}`.

Middleware:
- Reject any request without `X-Internal-Token` matching `settings.INTERNAL_API_TOKEN`, EXCEPT `/healthz`.
- Add request-id middleware that puts a UUID into `logging.contextvars`.

## Verification
- `uvicorn src.api.main:app --reload` boots cleanly.
- `tests/test_api_health.py` covers /healthz and token rejection.

## Done when
- All endpoints exist, even if the handlers just call placeholder agent methods that log and return ok.
