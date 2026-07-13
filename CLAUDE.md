# CLAUDE.md — Project Context

## What this project is

A sales automation system with two agents:

1. **Inbound Agent** — listens for HubSpot inbound inquiries, analyzes them with an LLM, drafts a reply (email or WhatsApp), and sends it after human approval.
2. **Report Agent** — aggregates inbound activity into daily and weekly reports.

(An Outbound prospecting agent existed earlier and was **removed entirely** on 2026-07-13 — see git history / branch `chore/remove-outbound-agent`.)

This is **not** a "fully autonomous AI agent" — it is a **workflow automation system** where an LLM handles judgment-heavy steps (classification, scoring, drafting) and Python code (including in-process background workers) handles deterministic steps (triggering, sending, recording).

## High-level architecture

```
HubSpot (CRM, source of truth)
       │
       ▼
Background workers in FastAPI (inbound_poller, send_worker)
       │           ▲
       ▼           │ approve/reject
   FastAPI BE  ───►  Slack / Web UI
       │
       ├──► LLM layer (Gemini on Vertex AI)
       │
       ├──► SQLite DB  (contacts, conversations, messages)
       │
       └──► Senders (HubSpot Engagements API, SMTP, WhatsApp stub)
```

## Repo layout

```
sales-automation/
├── CLAUDE.md              # this file
├── README.md              # human onboarding doc
├── .env.example           # required environment variables
├── .gitignore
├── plan/                  # design specs — Claude reads, rarely edits
├── todo/                  # ordered list of fine-grained tasks to do next
├── done/                  # finished todos (kept for history)
├── company_rules/         # business / brand / tone rules (markdown)
├── knowledge_base/        # product facts (pricing, policies, FAQ) — selected by category
├── src/
│   ├── api/               # FastAPI app and routes
│   ├── agents/            # inbound / report orchestration
│   ├── llm/               # LLM client (Gemini on Vertex AI)
│   ├── integrations/      # hubspot, slack, smtp, whatsapp
│   ├── db/                # SQLAlchemy models, migrations, repositories
│   └── common/            # logging, config, prompt loading, helpers
├── scripts/               # init_db, dev helpers
├── tests/                 # pytest tests
├── data/                  # local SQLite file lives here (gitignored)
└── logs/                  # app logs (gitignored)
```

## Conventions

- **Python 3.11+**, FastAPI, SQLAlchemy 2.x, pydantic v2, pytest.
- Package manager: `uv` if available, otherwise plain `pip` + venv.
- Style: `ruff` for lint, `black` for format. Run before commit.
- Imports: stdlib → third-party → local, separated by blank lines.
- No global state except a single `Settings` object loaded from `.env`.

## LLM access

The LLM client lives in `src/llm/client.py` and exposes a single `complete(prompt_name, variables=None, schema=None, tier="flash"|"pro") -> str | BaseModel` function.

The only provider is **Gemini on Vertex AI** (`src/llm/providers/gemini_vertex.py`), via the `google-genai` SDK. Authentication uses a service-account JSON in `GOOGLE_CREDENTIALS_JSON` (no API key); the project comes from `GOOGLE_CLOUD_PROJECT` or the JSON's `project_id`, region from `GOOGLE_CLOUD_LOCATION`.

**Hybrid model tiers:** `tier="flash"` (default) uses `GEMINI_MODEL` (`gemini-2.5-flash`) for light judgment — classification, scoring, doc routing, enrichment. `tier="pro"` uses `GEMINI_MODEL_PRO` (`gemini-2.5-pro`) for customer-facing drafting — inbound replies.

All prompts live as `.md` files in `src/llm/prompts/`. They are loaded by name, not hardcoded as strings.

**Knowledge selection:** the inbound agent picks knowledge docs with an LLM router — `src/llm/knowledge.py:select_relevant_docs` builds a compact index (title + summary + tags) of `active` docs and asks the flash model which are relevant to the actual inquiry, falling back to deterministic `categories` matching (`load_relevant_docs`) on any failure.

## Email sending

Selected via env var `EMAIL_PROVIDER`:

- `hubspot`: HubSpot Engagements API (`/crm/v3/objects/emails`)
- `smtp`: plain SMTP (Gmail App Password works)

Always log the sent message into HubSpot timeline regardless of provider (via `integrations/hubspot.log_engagement`).

## WhatsApp

Stub interface only for now. Real WhatsApp Cloud API integration is parked behind a feature flag `WHATSAPP_ENABLED=false` until Meta Business approval is in place. Drafts are stored in DB and surfaced for human approval, but `send()` raises `NotImplementedError` unless the flag is on and the token is set.

## Human-in-the-loop

No outgoing message goes out without approval **in the first iteration of the product**. Approval flow:

1. Agent drafts message → stores in `messages` table with status `pending_approval`.
2. BE posts a Slack card with the draft and a link to approve (and exposes Approve / Edit / Reject in the web UI at `/messages/{id}`).
3. Approval webhook hits FastAPI `/approve/{message_id}` → status flips to `approved` → sender goes.

`AUTO_SEND_THRESHOLD` env var lets us later auto-send when LLM confidence is above a threshold (default: never, `1.01`).

## Free / local stack

This project must run end-to-end on a developer laptop with no paid services. Cloud deploy is optional. See `plan/07_free_hosting_guide.md` for free-tier deploy options when ready.

## Testing strategy

- Unit tests for: prompt rendering, LLM client adapters (mock subprocess), HubSpot client (mock httpx), dedup logic.
- Integration test for end-to-end inbound flow with a fake HubSpot webhook payload and a stub LLM that returns canned JSON.
- No tests against live HubSpot — all mocked.

## What lives where (quick map for Claude)

- New prompt → `src/llm/prompts/<area>/<name>.md` + reference in code with `load_prompt("<area>/<name>")`.
- New rule that affects message tone → `company_rules/<n>_<topic>.md`, then the prompt template includes it automatically.
- New factual reference doc (pricing, policy, FAQ, product info) → copy `knowledge_base/_TEMPLATE.md` to `knowledge_base/<name>.md`, fill the frontmatter (`categories`, `summary`, `tags`, `status: active`, ...), then `python scripts/import_knowledge_base.py`. The inbound agent's LLM router (`src/llm/knowledge.py:select_relevant_docs`) reads `summary`+`tags` to pick docs. The `/knowledge` web-editing UI was **removed** on 2026-07-13 (the `knowledge_documents` table + selection logic stay); knowledge is managed via the markdown import for now, with a Notion-sourced sync planned.
- New DB field → SQLAlchemy model in `src/db/models.py`, migration in `src/db/migrations/` (additive `ALTER TABLE ... ADD COLUMN`, SQLite+Postgres compatible). After adding, run `python scripts/init_db.py` against the Supabase Postgres too — local SQLite tests won't catch a missing-column error there.
