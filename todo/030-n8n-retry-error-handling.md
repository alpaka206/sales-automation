# 030 — n8n workflow retry and error routing

## Why

Plan 06 specifies: "On failure: Retry 3x with exponential backoff, then
write to errors Slack channel." Currently only the inbound webhook has
linear retry (5s fixed wait); the other 5 workflows have no retry or error
routing at all.

## What to do

1. For each workflow JSON in `n8n_workflows/`:
   - Add retry config with exponential backoff to the HTTP Request nodes
     (3 retries, starting at 5s).
   - Add an error-trigger node that posts to a configurable Slack channel
     (`SLACK_ERROR_CHANNEL_ID` env var, falls back to approval channel).

2. Update `.env.example` with `SLACK_ERROR_CHANNEL_ID=` entry.

3. Update `tests/test_n8n_exports.py` to verify retry config is present
   in every workflow that calls the API.

## Acceptance criteria

- All 6 workflow JSONs include retry configuration.
- At least the 3 core workflows (inbound, outbound, reply_check) have
  error-trigger nodes.
- n8n export tests pass.

## Verify

```bash
pytest tests/test_n8n_exports.py -v
```
