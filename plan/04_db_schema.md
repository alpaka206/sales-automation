# 04 — Database Schema

SQLAlchemy 2.x declarative. SQLite by default, swappable via `DATABASE_URL`.

## Tables

### `contacts`
| col | type | notes |
|---|---|---|
| id | int PK | |
| hubspot_contact_id | str unique | |
| email | str | nullable but indexed |
| normalized_email | str indexed | lowercase, plus-stripped |
| full_name | str | |
| company | str | |
| domain | str indexed | |
| country | str | |
| lifecycle_stage | str | |
| score | int | current best score |
| whatsapp_opt_in | bool | default false |
| created_at | datetime | |
| updated_at | datetime | |

### `prospects` (outbound only — pre-contact)
| col | type | notes |
|---|---|---|
| id | int PK | |
| source | str | "youtube" / "linkedin_csv" / etc. |
| source_ref | str | e.g. youtube channel id |
| email | str | may be null until enriched |
| normalized_email | str indexed nullable | |
| full_name | str | |
| company | str | |
| domain | str indexed | |
| country | str | |
| icp_score | int | |
| icp_rationale | text | |
| status | str | "candidate" / "drafted" / "skipped_dup" / "skipped_lowscore" |
| last_contacted_at | datetime nullable | |
| follow_up_count | int default 0 | |
| created_at | datetime | |

A row may be linked to a `contacts` row once a reply comes back (foreign key `contact_id` nullable).

### `conversations`
| col | type | notes |
|---|---|---|
| id | int PK | |
| contact_id | int FK contacts.id | |
| prospect_id | int FK prospects.id nullable | |
| topic | str | LLM-summarized topic |
| stage | str | "initial" / "replied" / "meeting" / "won" / "lost" |
| last_outgoing_at | datetime | |
| last_incoming_at | datetime | |
| created_at | datetime | |

### `messages`
| col | type | notes |
|---|---|---|
| id | int PK | |
| conversation_id | int FK | |
| direction | str | "inbound" / "outbound" |
| channel | str | "email" / "whatsapp" |
| from_address | str | |
| to_address | str | |
| subject | str nullable | |
| body | text | |
| language | str | "ko" / "en" |
| status | str | "pending_approval" / "approved" / "sent" / "rejected" / "bounced" / "received" |
| score_snapshot | int nullable | |
| prompt_variant | str nullable | for A/B |
| draft_provider | str | "gemini_vertex" |
| approved_by | str nullable | slack user id of approver |
| approved_at | datetime nullable | |
| sent_at | datetime nullable | |
| replied | bool default false | |
| hubspot_engagement_id | str nullable | for reconciliation |
| created_at | datetime | |

### `approvals` (audit trail)
| col | type | notes |
|---|---|---|
| id | int PK | |
| message_id | int FK | |
| approver | str | |
| action | str | "approve" / "edit" / "reject" |
| diff | text nullable | for edits |
| reason | str nullable | |
| created_at | datetime | |

### `events` (generic audit)
| col | type | notes |
|---|---|---|
| id | int PK | |
| kind | str | "llm_call" / "send" / "webhook" / "error" |
| payload | json | |
| created_at | datetime | |

## Migrations

Use a tiny in-house migration system rather than Alembic to keep deps slim:
- Each migration is a `src/db/migrations/0001_xxx.py` with `up()` only.
- Tracker table `_migrations(name TEXT PK, applied_at TIMESTAMP)`.
- `scripts/init_db.py` applies any pending migrations.

If Alembic ends up easier, switch — Claude can decide and document in commit message.

## Seed

`scripts/seed_dev.py` inserts 3 fake contacts, 2 fake prospects, 4 fake messages so the report agent has something to display before real data exists.
