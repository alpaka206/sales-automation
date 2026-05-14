# 02 — Outbound Agent

## Purpose

Discover prospects from configurable sources, score them against an ICP, draft a source-aware opening email, dedupe, queue for approval, send, and watch for replies / follow-ups.

## Sources (pluggable)

Each source is a class implementing `BaseSource`:

```python
class BaseSource(Protocol):
    name: str                                       # "youtube" | "linkedin_csv" | "manual_csv"
    def discover(self, filters: dict) -> list[ProspectCandidate]: ...
```

### `youtube`
- Uses YouTube Data API v3.
- Filters: `query: str`, `region_code: str`, `min_subscribers: int`, `topic: str`.
- Quota: 100 units per `search.list` call; total daily quota 10,000. Cache results in `data/cache/youtube/`.
- Output: channel title, channel URL, country, subscriber count, recent video summary.
- Email discovery: parse channel `description` for `mailto:` or `@`. If none found, mark as `email_unknown` and skip send (record for manual review).

### `linkedin_csv`
- Reads a CSV exported by the user from Sales Navigator etc.
- We **do not scrape** LinkedIn. The CSV path is a user-provided file.
- Filters: column names mapping.

### `manual_csv`
- Generic CSV with `name, email, company, domain, country, notes` columns.

Adding a new source = new file in `src/integrations/<name>.py` + entry in `src/agents/outbound/source_registry.py`.

## Dedup

Before any LLM call:
1. Compute `normalized_email = email.lower().split("+")[0]` (strip plus-aliasing).
2. `SELECT * FROM prospects WHERE normalized_email = ?`
3. If exists AND `last_contacted_at > now() - OUTBOUND_COOLDOWN_DAYS days` → skip.
4. Secondary check: same `(domain, full_name)` within cooldown → skip.

## Per-prospect pipeline

```
candidate ──► enrich ──► icp_score ──► above_threshold? ──► draft ──► persist ──► notify approver
                                            │
                                            └─ no ──► drop, record decision
```

### Enrich
- Optional homepage fetch (httpx, 5s timeout). LLM summarizes "What does this company do?" in 2 sentences.
- Optional YouTube channel about-section summary (for youtube source).

### ICP score
Prompt: `src/llm/prompts/outbound/icp_score.md`
Output JSON:
```json
{
  "score": 78,
  "rationale": "...",
  "risks": ["..."],
  "language_guess": "ko" | "en"
}
```

### Draft
- Source-specific prompt file. Naming convention:
  - `src/llm/prompts/outbound/email_youtube.md`
  - `src/llm/prompts/outbound/email_linkedin_csv.md`
  - `src/llm/prompts/outbound/email_manual_csv.md`
- All prompts are auto-prefixed with `company_rules/*.md` concatenated.
- Output: same shape as inbound draft (`subject`, `body`, `language`).

### Approval & send
Identical to inbound flow — same `messages` table, same Slack card, same `/approve` endpoint.

## Reply tracking

After send:
- `messages.sent_at = now()`
- `messages.status = sent`
- `prospects.last_contacted_at = now()`

Cron job (n8n hourly): for each sent message in last 30 days with `replied=false`:
1. Query HubSpot for inbound emails from `to_address` since `sent_at`.
2. If reply found → `replied=true`, link conversation, do NOT follow up.
3. If not and `sent_at + FOLLOWUP_AFTER_DAYS < now()` and `follow_up_count < 2` → enqueue follow-up draft.

## Follow-up

Same agent, different prompt (`src/llm/prompts/outbound/followup.md`). Prompt receives the previous thread context. Max 2 follow-ups per conversation by default.

## Acceptance test

`tests/test_outbound_flow.py` runs the outbound agent with a stubbed source that returns 3 candidates (1 dup, 1 below threshold, 1 valid). Asserts:
- 1 message inserted with `status=pending_approval`
- 1 prospect skipped (dup), 1 dropped (low score)
- Slack mock called once
