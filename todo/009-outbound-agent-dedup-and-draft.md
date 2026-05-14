# 009 — Outbound agent: dedup + scoring + draft

## Goal
Run candidates through dedup, ICP scoring, draft, persist, notify.

## Steps
1. `src/agents/outbound/agent.py — OutboundAgent.run(source_name, filters)`:
   - Resolve source via registry.
   - For each candidate:
     - Normalize email, check `prospects` table for cooldown.
     - If dup → store with `status=skipped_dup` and skip.
     - Else: LLM `outbound/icp_score.md` → store rationale.
     - If score < `ICP_THRESHOLD` (default 50, settings.ICP_THRESHOLD) → `status=skipped_lowscore`, skip.
     - Else: LLM `outbound/email_<source>.md` (fall back to `outbound/email_generic.md` if source-specific not present).
     - Persist `prospects` row (status=drafted), `messages` row (pending_approval), notify approver.
2. Create the prompt files: `outbound/icp_score.md`, `outbound/email_generic.md`, `outbound/email_manual_csv.md`.

## Verification
- `tests/test_outbound_flow.py` with stub source (3 candidates: 1 dup, 1 low score, 1 valid) → asserts DB state matches plan.

## Done when
- Test passes. The valid candidate produces a pending_approval message + Slack notification.
