# Project Context

PERSO Inbound is a FastAPI workflow for inbound inquiry handling and customer operations.

## Invariants

- HubSpot tickets are accepted only from `HUBSPOT_TICKET_STAGE_NEW` when a ticket ID exists.
- The first receipt acknowledgement may send automatically; the detailed reply always requires human approval.
- SMTP performs real delivery. HubSpot's CRM email object is used only to log a successful delivery.
- Slack approval notifications are emitted only after a detailed draft is ready.
- WhatsApp is best-effort and requires an explicit opt-in property plus a phone number.
- `Message.direction` uses `inbound` for received messages and `outgoing` for our replies.
- Personal email domains are never grouped as one company.
- Existing conversation progress rows are append-only.

## Stack

- Python 3.11+, FastAPI, SQLAlchemy, Jinja/HTMX
- Gemini on Vertex AI (`flash` for routing/classification, `pro` for customer replies)
- SQLite locally; PostgreSQL-compatible migrations
- SMTP delivery, HubSpot CRM synchronization, optional Slack and WhatsApp

## Data flow

`HubSpot webhook / 10-minute poll → immediate acknowledgement → Gemini + policy docs → review queue + Slack → SMTP → HubSpot timeline + ticket stage`

Customer operations reuse the same Contact and Conversation records. `CustomerProfile`, `CustomerInteraction`, and `ContractRecord` add manual pipeline fields, cross-channel history, contracts, payments, and renewal insights without duplicating the inbound pipeline.

## Development

```powershell
.\.venv\Scripts\python.exe -m src.db.migrate
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
```

Do not send live messages in tests. `tests/conftest.py` disables background integrations regardless of the developer `.env`.
