# 012 — Report agent (daily + weekly)

## Goal
Implement per `plan/03_report_agent.md`. Emits Markdown, posts to Slack/Teams, persists copy.

## Steps
1. `src/agents/report.py — generate(kind: "daily"|"weekly") -> str`.
2. SQL aggregations: counts by status, score histogram, top sources, replied count.
3. LLM `report/narrative.md` for the opening paragraph (gracefully degrade to template if LLM fails).
4. Post via Slack/Teams; save copy to `data/reports/YYYY-MM-DD-{kind}.md`.

## Verification
- `tests/test_report.py` seeds tiny DB, asserts the markdown contains expected counts and at least one section header.

## Done when
- Daily + weekly both produce output; output saved to disk.
