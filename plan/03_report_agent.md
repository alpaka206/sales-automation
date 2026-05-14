# 03 — Report Agent

## Purpose

Generate daily and weekly digests so the team can see what the system did without opening the DB.

## Daily report

Triggered by n8n cron at `DAILY_REPORT_HOUR` (default 18:00 local).

Content (in this order):

1. **Top line**
   - Inbound today: X (auto-replied via approval Y, awaiting approval Z, rejected W)
   - Outbound today: X drafted / Y sent / Z replied / W bounced
   - Average reply rate (7-day rolling)

2. **High-priority items**
   - Inbound with score ≥ 80 that are awaiting approval
   - Replies received in the last 24h that haven't been acted on

3. **Risks / anomalies**
   - Bounce count > 0 → list addresses
   - LLM classification produced `spam` for messages that looked legit on rule-based score (review needed)
   - Any approval older than 24h not acted on

4. **Pipeline snapshot**
   - Counts by `conversations.stage` (initial / replied / meeting_booked / closed_won / closed_lost)

## Weekly report

Triggered by n8n cron Saturday morning (`WEEKLY_REPORT_DOW`).

Adds:
- 7-day funnel: contacted → opened? → replied → meeting → won
- Top performing source (by reply rate)
- Top performing prompt variant (if A/B is running — `messages.prompt_variant`)
- LLM cost estimate (token usage × current rate)

## Output format

Single markdown string. Posted to:
- Slack: as a single message with `mrkdwn`
- Teams: as MessageCard
- Email: optional, to `REPORT_EMAIL_TO` if set

Also persisted to `data/reports/YYYY-MM-DD.md` for history.

## Implementation note

The report agent does NOT need the LLM for numbers — those are SQL queries. The LLM is only used for the narrative paragraph at the top ("Today the system processed 12 inbound inquiries, most of which were partnership requests from APAC...") via `src/llm/prompts/report/narrative.md`. If `LLM_PROVIDER` is failing, fall back to a template-only narrative.

## Acceptance test

`tests/test_report.py` seeds a tiny DB with 5 inbound + 3 outbound + 1 reply, runs the report generator, asserts the markdown contains the expected counts and at least one high-priority item.
