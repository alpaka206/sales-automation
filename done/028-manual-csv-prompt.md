# 028 — Source-specific prompt for manual_csv

## Why

Plan 02 explicitly lists `email_manual_csv.md` as a required source-specific
prompt. Currently the outbound agent falls back to `email_generic.md` for
manual CSV prospects. A dedicated prompt can reference the fact that these
leads were hand-curated and tailor the opening line accordingly.

## What to do

1. Create `src/llm/prompts/outbound/email_manual_csv.md`:
   - Similar structure to `email_generic.md` but mention the prospect was
     identified through direct research (not scraped).
   - Include `{{homepage_summary}}` variable.
   - Emphasize personalization based on `{{summary}}` (the notes field).

2. Add a test in `tests/test_outbound_flow.py` that verifies the manual_csv
   prompt is selected when the source is `manual_csv`.

## Acceptance criteria

- `src/llm/prompts/outbound/email_manual_csv.md` exists.
- Outbound agent uses it instead of generic fallback for manual_csv source.
- Existing tests pass.

## Verify

```bash
pytest tests/test_outbound_flow.py -v
```
