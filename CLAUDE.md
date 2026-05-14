# CLAUDE.md — Project Context

## What this project is

A sales automation system with three agents:

1. **Inbound Agent** — listens for HubSpot inbound inquiries, analyzes them with an LLM, drafts a reply (email or WhatsApp), and sends it after human approval.
2. **Outbound Agent** — discovers prospects from configurable sources (YouTube, LinkedIn, manual CSV), runs source-specific prompts, dedupes against the DB, sends opening emails, and waits for replies.
3. **Report Agent** — aggregates the activity of the two agents above into daily and weekly reports.

This is **not** a "fully autonomous AI agent" — it is a **workflow automation system** where an LLM handles judgment-heavy steps (classification, scoring, drafting) and n8n / Python code handles deterministic steps (triggering, sending, recording, follow-ups).

## High-level architecture

```
HubSpot (CRM, source of truth)
       │
       ▼
n8n  (event triggers, schedules, branching, retries)
       │           ▲
       ▼           │ approve/reject
   FastAPI BE  ───►  Slack / Teams
       │
       ├──► LLM layer (Claude CLI subprocess OR Anthropic API)
       │
       ├──► SQLite DB  (prospects, messages, conversations, follow-ups)
       │
       └──► Senders (HubSpot Engagements API, SMTP, WhatsApp stub)
```

## Repo layout

```
sales-automation/
├── PROMPT.md              # Ralph Loop master prompt — entrypoint for the loop
├── CLAUDE.md              # this file
├── README.md              # human onboarding doc
├── .env.example           # required environment variables
├── .gitignore
├── plan/                  # design specs — Claude reads, rarely edits
├── todo/                  # ordered list of fine-grained tasks to do next
├── done/                  # finished todos (kept for history)
├── company_rules/         # business / brand / tone rules (markdown)
├── src/
│   ├── api/               # FastAPI app and routes
│   ├── agents/            # inbound / outbound / report orchestration
│   ├── llm/               # LLM client abstraction (CLI subprocess + API)
│   ├── integrations/      # hubspot, youtube, linkedin, slack, smtp, whatsapp
│   ├── db/                # SQLAlchemy models, migrations, repositories
│   └── common/            # logging, config, prompt loading, helpers
├── n8n_workflows/         # exported n8n workflow JSON (commit these)
├── scripts/               # ralph_loop, init_db, dev helpers
├── tests/                 # pytest tests
├── data/                  # local SQLite file lives here (gitignored)
└── logs/                  # ralph_history.log, app logs (gitignored)
```

## Conventions

- **Python 3.11+**, FastAPI, SQLAlchemy 2.x, pydantic v2, pytest.
- Package manager: `uv` if available, otherwise plain `pip` + venv.
- Style: `ruff` for lint, `black` for format. Run before commit.
- Imports: stdlib → third-party → local, separated by blank lines.
- No global state except a single `Settings` object loaded from `.env`.

## LLM access

The LLM client lives in `src/llm/client.py` and exposes a single `complete(prompt: str, schema: type[BaseModel] | None = None) -> str | BaseModel` function.

Provider selection by env var `LLM_PROVIDER`:

- `claude_cli` (default): shells out to `claude -p "<prompt>" --output-format json`
- `anthropic_api`: uses `anthropic` SDK with `ANTHROPIC_API_KEY`

All prompts live as `.md` files in `src/llm/prompts/`. They are loaded by name, not hardcoded as strings.

## Email sending

Selected via env var `EMAIL_PROVIDER`:

- `hubspot`: HubSpot Engagements API (`/crm/v3/objects/emails`)
- `smtp`: plain SMTP (Gmail App Password works)

Always log the sent message into HubSpot timeline regardless of provider (via `integrations/hubspot.log_engagement`).

## WhatsApp

Stub interface only for now. Real WhatsApp Cloud API integration is parked behind a feature flag `WHATSAPP_ENABLED=false` until Meta Business approval is in place. Drafts are stored in DB and surfaced for human approval, but `send()` raises `NotImplementedError` unless the flag is on and the token is set.

## Human-in-the-loop

No outbound message goes out without approval **in the first iteration of the product**. Approval flow:

1. Agent drafts message → stores in `messages` table with status `pending_approval`.
2. n8n posts a Slack/Teams card with Approve / Edit / Reject buttons.
3. Approval webhook hits FastAPI `/approve/{message_id}` → status flips to `approved` → sender goes.

`AUTO_SEND_THRESHOLD` env var lets us later auto-send when LLM confidence is above a threshold (default: never, `1.01`).

## Dedup rule (Outbound)

A prospect is identified by **normalized email** (lowercased, plus-stripped) as primary key. Secondary: `(domain, full_name)`. The outbound agent must check `prospects` table before drafting; if a row exists with `last_contacted_at` within `OUTBOUND_COOLDOWN_DAYS` (default 90), skip.

## Reply detection

For follow-ups: store `last_outgoing_message_at` per conversation. A poll job (n8n cron) checks the HubSpot inbox for emails received after that timestamp from the same address. If found → mark `replied=true`, do NOT send follow-up. If not found after N days (`FOLLOWUP_AFTER_DAYS`, default 4) → draft follow-up.

## Free / local stack

This project must run end-to-end on a developer laptop with no paid services. Cloud deploy is optional. See `plan/07_free_hosting_guide.md` for free-tier deploy options when ready.

## Testing strategy

- Unit tests for: prompt rendering, LLM client adapters (mock subprocess), HubSpot client (mock httpx), dedup logic.
- Integration test for end-to-end inbound flow with a fake HubSpot webhook payload and a stub LLM that returns canned JSON.
- No tests against live HubSpot/YouTube/LinkedIn — all mocked.

## What lives where (quick map for Claude)

- New prospect source (e.g. Crunchbase) → `src/integrations/<source>.py` + `src/agents/outbound/source_registry.py` entry.
- New prompt → `src/llm/prompts/<area>/<name>.md` + reference in code with `load_prompt("<area>/<name>")`.
- New rule that affects message tone → `company_rules/<n>_<topic>.md`, then the prompt template includes it automatically.
- New DB field → SQLAlchemy model in `src/db/models.py`, migration in `src/db/migrations/`.
