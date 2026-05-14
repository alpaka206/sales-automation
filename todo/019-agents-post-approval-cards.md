# 019 — Agents post approval notification after drafting

## Why

Both the inbound and outbound agents draft messages and store them with
`status=pending_approval`, but **neither agent notifies anyone**. The Slack
`post_approval_card()` function exists but is never called. Without this,
approvers have no way to know a draft is waiting.

## What to do

1. In `src/agents/inbound.py`, after `_persist()` returns the `message_id`:
   - Call `slack.post_approval_card(message_id, subject, body_snippet, score, category, channel)`.
   - Wrap in try/except so a Slack failure doesn't crash the inbound webhook.
   - If `SlackNotConfigured`, try `teams.post_approval_card(...)` as fallback.
   - If neither is configured, just log a warning.

2. In `src/agents/outbound/agent.py`, after the message is persisted:
   - Same pattern: try Slack → try Teams → log warning.

3. Add a small helper `src/agents/_notify.py` with:
   ```python
   def notify_approval(message_id, subject, body_snippet, score, category, channel):
       """Try Slack, then Teams, then log."""
   ```
   Both agents call this helper to avoid duplicating the try/except chain.

4. In `src/integrations/teams.py`, confirm `post_approval_card()` exists and
   has the same signature. If it's a stub, flesh it out to send a Teams
   Adaptive Card via `TEAMS_WEBHOOK_URL`.

## Acceptance criteria

- After inbound agent processes a webhook, Slack gets an approval card (or Teams if Slack not configured).
- After outbound agent drafts a message, same notification fires.
- If neither Slack nor Teams is configured, agent still succeeds (warning logged).
- Existing tests still pass.

## Verify

```bash
pytest tests/ -v -q
```

Add tests in `tests/test_notify.py` that mock `slack.post_approval_card` and
`teams.post_approval_card`, verify the helper dispatches correctly.
