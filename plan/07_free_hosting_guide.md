# 07 — Free Hosting Guide

Everything in this project can run on a dev laptop with no paid services. The table below lists what to use when you want to push beyond localhost.

## Components and free tiers

| Component | Local (default) | Free cloud option | Free-tier limits to watch |
|---|---|---|---|
| **n8n** | `npx n8n` on laptop | n8n Cloud has no free tier as of 2026. Use **Render** or **Railway** with the official `n8nio/n8n` Docker image (Render free has spin-down, Railway gives ~$5/mo trial credit). | Render free spins down after 15 min inactivity — fine for cron-based work if cron lives elsewhere. |
| **FastAPI BE** | `uvicorn src.api.main:app` | **Render** Web Service (free), **Fly.io** Machines (free tier), **Deta Space** | Render free spins down; first request after sleep is slow. |
| **DB** | SQLite file `data/app.db` | **Supabase** (500MB Postgres + 50K MAU free), **Neon** (3GB Postgres branch free), **Turso** (libSQL, free) | Supabase pauses after a week of inactivity. |
| **LLM** | `claude` CLI (your existing Claude Code login) | Anthropic API (`ANTHROPIC_API_KEY`) — pay per token | CLI is free as long as you have a Claude account with usage allowance. |
| **Email** | Gmail SMTP via App Password (free, 500/day) | **SendGrid** (100/day free), **Brevo** (300/day free), **Resend** (3K/mo free) | Gmail 500/day is plenty for HITL flows; if cold-mailing later, switch to SendGrid/Resend. |
| **Slack** | Slack workspace (free) | same | Free workspaces have 90-day message history — keep approval audit in DB, not Slack. |
| **WhatsApp** | n/a until approved | **Meta Cloud API** has 1,000 free conversations/month after biz verification | Plan as a phase-2 deliverable. |

## Recommended path

**Day 1 — local:**
- Run BE with `uvicorn`.
- Run n8n with `npx n8n`.
- SQLite + Gmail App Password + Slack.
- Everything works on `localhost`.

**Day 2 — make it reachable (so HubSpot webhooks can hit you):**
- Use `cloudflared tunnel` (free) to expose `localhost:8000` and `localhost:5678` over HTTPS, or use `ngrok` free tier.
- Configure HubSpot webhook URL → `https://your-tunnel.example.com/webhook/hubspot/inbound`.

**Day 3 — move BE off your laptop:**
- Push to GitHub.
- Connect Render → free Web Service. Set env vars in Render dashboard.
- Update n8n base URL → Render URL.

**Day 4 — move DB:**
- Create a Supabase project (free).
- Update `DATABASE_URL` → Supabase Postgres URI.
- Run migrations against it.

## Things that are tempting but not free

- HubSpot Marketing Hub paid features (Sequences, AI tools). We don't need them — we drive sends via the API directly.
- Twilio for WhatsApp. Use Meta Cloud API directly when approved.
- Apollo / Lusha / ZoomInfo for email enrichment. Stay manual / CSV for MVP.

## Cost ceiling

If you stay in MVP scope (inbound + approval + simple outbound from manual CSV), monthly cost is **$0**. Anthropic API is the only thing that costs once you flip `LLM_PROVIDER=anthropic_api` — budget ~$3–10/month for ~1k messages/month at Sonnet rates.
