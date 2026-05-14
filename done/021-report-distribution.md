# 021 — Report distribution (Slack / Teams / email)

## Why

`ReportAgent.generate()` returns Markdown and saves to `data/reports/`, but
the report is never delivered to anyone. The plan requires posting to Slack
(mrkdwn), Teams (MessageCard), and optionally email (`REPORT_EMAIL_TO`).

## What to do

1. Add config fields in `src/common/config.py`:
   - `REPORT_SLACK_CHANNEL_ID: str = ""` (may differ from approval channel)
   - `REPORT_EMAIL_TO: str = ""` (comma-separated list, optional)

2. Add `.env.example` entries for the new vars.

3. Add a method `ReportAgent._distribute(report: str, kind: str)`:
   - If `REPORT_SLACK_CHANNEL_ID` is set (or fallback to `SLACK_APPROVAL_CHANNEL_ID`),
     post the Markdown via `slack.post_message(channel, text)`.
   - If `TEAMS_WEBHOOK_URL` is set, post as a Teams MessageCard.
   - If `REPORT_EMAIL_TO` is set, send via SMTP sender.
   - Each channel is best-effort: log errors but don't fail the report.

4. Add `src/integrations/slack.py::post_message(channel, text)` — a simpler
   sibling of `post_approval_card` that just sends a `chat.postMessage` with
   plain mrkdwn text.

5. Wire in `src/api/main.py`: after `agent.generate()`, call `agent.distribute()`.
   Or have `generate()` call `_distribute()` itself at the end.

## Acceptance criteria

- Report is posted to Slack channel if token + channel configured.
- Report is posted to Teams if webhook configured.
- Report is emailed if `REPORT_EMAIL_TO` is set.
- If no channels configured, report is still generated and saved to file.
- API endpoint returns the report Markdown as before.

## Verify

```bash
pytest tests/test_report.py -v
```

Add a test that mocks `slack.post_message`, `teams.post_message`, and
`senders.smtp.send_smtp` to verify distribution is called.
