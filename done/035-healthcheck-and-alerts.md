# 035 — Health-check + Slack/Teams alert on failures

## Why

The system has many fragile external dependencies — the Claude CLI
session can expire, HubSpot tokens get rotated, SMTP credentials get
revoked, the Anthropic API key can be wrong, etc. Today these failures
only surface when someone notices a missing approval card or reads
the server log. We want a periodic health-check that catches
these silently-broken states and pings Slack/Teams when something is
wrong.

## What to do

1. New module `src/common/healthcheck.py` exposing
   `run_healthchecks() -> HealthReport` where `HealthReport` is a
   pydantic model: `{checks: list[CheckResult], overall_status: str}`.
   Each `CheckResult` has `name`, `status` (`PASS`/`WARN`/`FAIL`),
   `detail`, `latency_ms`.

2. Implement these checks:
   - **claude_cli_token** — run `claude -p "ping" --output-format json`
     with a 10s timeout. `FAIL` on auth errors, non-zero exit, or
     timeout. Detect token-expiry by matching stderr/exit-code
     patterns (`Not authenticated`, `401`).
   - **anthropic_api_key** — only if `LLM_PROVIDER=anthropic_api`.
     Issue a 1-token completion. `FAIL` on 401, `WARN` on 429.
   - **hubspot_token** — `GET /crm/v3/objects/contacts?limit=1`.
     `FAIL` on 401/403.
   - **smtp_login** — only if `EMAIL_PROVIDER=smtp`. Open and close
     SMTP connection (no send). `FAIL` on auth failure.
   - **db_connectivity** — open SQLAlchemy session, run `SELECT 1`.
   - **disk_space** — `WARN` if `data/` partition has < 500 MB free.

3. CLI entrypoint: `python -m sales healthcheck` prints a table and
   exits non-zero on any `FAIL`. Wire it into `src/cli.py` next to
   the existing `doctor` command (note: `doctor` checks config
   _shape_, `healthcheck` checks live connectivity — keep them
   separate).

4. Scheduling:
   - Add an n8n workflow `n8n_workflows/healthcheck.json` that runs
     every 15 min via Schedule Trigger, hits a new FastAPI endpoint
     `POST /internal/healthcheck` (token-gated by
     `INTERNAL_API_TOKEN`), and on any `FAIL` posts to Slack/Teams
     using the existing `integrations/senders/slack.py` and
     `teams.py` notifier.
   - The FastAPI endpoint calls `run_healthchecks()` and returns the
     report as JSON.

5. Alert format: one card per failed check with the check name,
   detail, and a link to the server log location. De-dupe by
   maintaining a small `data/healthcheck_state.json` so the same
   failure does not page every 15 min — only on state transitions
   (PASS→FAIL, FAIL→PASS).

## Acceptance criteria

- `python -m sales healthcheck` prints check statuses and exits 0 on
  all-PASS, 1 on any FAIL.
- `POST /internal/healthcheck` returns the report when called with the
  internal token.
- Unit tests cover each check with mocked clients (no live API calls).
- Integration test for the alert flow: simulate a `FAIL` and assert
  the Slack notifier is called with the expected payload.
- An exported `n8n_workflows/healthcheck.json` is committed.

## Verify

```bash
python -m sales healthcheck
pytest tests/test_healthcheck.py tests/test_healthcheck_endpoint.py -v
```

## Notes

- The `INTERNAL_API_TOKEN` env var may already exist from earlier
  work; if not, add it to `.env.example` and `src/common/config.py`.
- Slack and Teams notifiers both exist (`src/integrations/senders/
  slack.py`, `teams.py`); fan out to both when both channels are
  configured, otherwise to whichever is available.
