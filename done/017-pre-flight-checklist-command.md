# 017 — `python -m sales doctor` pre-flight checklist

## Goal
A one-shot CLI that tells the operator what's configured and what's missing — so they know what to fill in before going live.

## Steps
1. `src/cli.py` — argparse with subcommands. First subcommand: `doctor`.
2. `doctor` checks:
   - `.env` exists; key vars present (warn on missing)
   - `data/app.db` exists; if not, suggest `python scripts/init_db.py`
   - `claude` CLI on PATH? (run `claude --version` with timeout 3s)
   - If `LLM_PROVIDER=anthropic_api`: `ANTHROPIC_API_KEY` non-empty
   - HubSpot token: optional ping `GET /crm/v3/owners` if token set
   - Slack token: optional `auth.test`
   - YouTube key: optional `search.list?q=test&maxResults=1`
   - n8n: ping `${N8N_URL}/healthz` if set
3. Prints a table with ✅ / ⚠️ / ❌ per item and exits 0 only if all required items are ✅.

## Verification
- `python -m sales doctor` runs against an empty config and exits non-zero with clear messages.
- Add to README under "퇴근 전 5분 세팅".

## Done when
- Doctor command exists, runnable, exits with correct code.
