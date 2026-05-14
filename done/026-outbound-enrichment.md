# 026 — Outbound prospect enrichment (homepage fetch)

## Why

Plan 02 specifies an enrichment step: before drafting, fetch the prospect's
company homepage (5s timeout), summarize it with the LLM, and feed the
summary into the email draft prompt. This makes cold emails more relevant.

## What to do

1. Create `src/agents/outbound/enrichment.py`:
   - `enrich_prospect(candidate: ProspectCandidate, llm: LLMClient) -> dict`
   - If `candidate.domain` is set, fetch `https://{domain}` with httpx (5s
     timeout, follow redirects, accept text/html).
   - Extract visible text (strip HTML tags, limit to 3000 chars).
   - Call LLM with `outbound/enrich_homepage` prompt to get a 2-sentence summary.
   - Return `{"homepage_summary": "...", "enrichment_source": "homepage"}`.
   - On any failure (timeout, non-200, LLM error), return empty dict.

2. Create `src/llm/prompts/outbound/enrich_homepage.md`:
   - Takes `{{domain}}`, `{{homepage_text}}`.
   - Returns plain text (no JSON schema) — a 2-sentence summary of what the
     company does.

3. Wire into `OutboundAgent._process_candidate()`:
   - After ICP scoring, before drafting.
   - Pass the `homepage_summary` into the draft email prompt as a template var.
   - Update `outbound/email_generic.md` and any source-specific prompts to
     include `{{homepage_summary}}` if present.

## Acceptance criteria

- Outbound drafts receive homepage context when domain is available.
- 5s timeout prevents slow pages from blocking the pipeline.
- If enrichment fails, drafting proceeds with empty summary.
- Existing outbound tests pass.

## Verify

```bash
pytest tests/test_outbound_flow.py -v
```

Add `tests/test_enrichment.py` that mocks httpx and verifies the
enrichment output.
