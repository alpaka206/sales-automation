# Local-dev launcher. Overrides only the unsafe/prod-targeting settings via
# process env (pydantic-settings: env vars > .env file), so your real .env is
# never modified. Uses a local SQLite DB and disables every worker that would
# act on the shared production system (poller, send, auto-ack, Slack).
#
#   .\scripts\run-local.ps1              # migrate + serve on 127.0.0.1:8000
#   .\scripts\run-local.ps1 -NoServe     # just apply migrations to local.db

param([switch]$NoServe)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

# --- local, isolated data store (never the blocked Supabase) ---
$env:DATABASE_URL = "sqlite:///./data/local.db"

# --- kill every live-acting background worker / channel ---
$env:INBOUND_POLL_ENABLED           = "false"
$env:INBOUND_WORKER_ENABLED         = "false"
$env:SEND_WORKER_ENABLED            = "false"
$env:INBOUND_AUTO_ACK_ENABLED       = "false"
$env:APPROVAL_CHANNEL               = "none"
$env:SLACK_ENABLED                  = "false"
$env:SEND_OVERRIDE_EMAIL            = ""
$env:INBOUND_DOMAIN_ENRICHMENT_ENABLED = "false"   # no outbound homepage fetches per request

# --- local web-UI access: localhost-only gate, unsigned mock webhooks ---
$env:APP_HOST                          = "127.0.0.1"
$env:WEB_CONCURRENCY                   = "1"
$env:HUBSPOT_WEBHOOK_REQUIRE_SIGNATURE = "false"

New-Item -ItemType Directory -Force -Path data | Out-Null

Write-Host "==> migrating local.db (SQLite)" -ForegroundColor Cyan
python -m src.db.migrate

if ($NoServe) { Write-Host "migrations applied; -NoServe set, not serving." -ForegroundColor Green; exit 0 }

Write-Host "==> serving http://127.0.0.1:8000  (Ctrl+C to stop)" -ForegroundColor Cyan
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload
