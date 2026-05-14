# 020 — Wire up reply-check API endpoint

## Why

`POST /run/reply_check` is a placeholder that logs and returns `{"status": "started"}`
without calling `reply_check.run()`. The n8n cron workflow calls this endpoint
hourly, so nothing happens — no replies are detected and no follow-ups are drafted.

## What to do

1. In `src/api/main.py`, replace the stub `run_reply_check`:
   ```python
   @app.post("/run/reply_check")
   def run_reply_check() -> dict:
       from ..agents.reply_check import run
       stats = run()
       return {"status": "ok", **stats}
   ```

2. Add approval notification for follow-up drafts:
   - In `src/agents/reply_check.py`, after `_draft_followup()` creates a new
     `Message` with `status=pending_approval`, call the `notify_approval()` helper
     (from todo 019) so approvers know there's a follow-up to review.
   - If todo 019 isn't done yet, inline a try/except Slack call.

3. Ensure the reply_check `run()` function handles edge cases:
   - No sent messages → returns `{"checked": 0, "replied": 0, "followup_drafted": 0}`.
   - DB errors → log and continue (don't crash the whole batch).

## Acceptance criteria

- `POST /run/reply_check` calls `reply_check.run()` and returns real stats.
- Follow-up drafts trigger approval notifications.
- Existing reply_check tests still pass.

## Verify

```bash
pytest tests/test_reply_check.py tests/test_smoke.py -v
```
