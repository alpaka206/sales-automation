# 023 — LLM cost tracking module

## Why

Plan 05 specifies a `src/llm/pricing.py` module for tracking token usage
and estimating costs. The weekly report is supposed to include "LLM cost
estimate" but there's no way to measure it. The `anthropic_api` provider
returns token counts in the response, but we discard them.

## What to do

1. Create `src/llm/pricing.py`:
   - Define a dict of model → cost-per-1K-input / cost-per-1K-output tokens.
   - `estimate_cost(model, input_tokens, output_tokens) -> float`
   - `format_cost(amount: float) -> str` (e.g. "$0.012")

2. In `src/llm/client.py`, after each LLM call:
   - Extract token counts from the provider response when available
     (`anthropic_api` provides `usage.input_tokens` / `usage.output_tokens`).
   - For `claude_cli` and `ollama`, estimate from prompt/response character lengths.
   - Store cumulative totals in a module-level counter (or lightweight DB table `llm_usage`).

3. In `src/agents/report.py`, add an `_llm_cost_summary()` method:
   - Query cumulative usage since the report period start.
   - Add a "## LLM Usage" section to the report output.

4. Add `src/db/models.py` → `LLMUsage` table (optional, simpler: just a
   JSON file in `data/llm_usage.jsonl` appended per call).

## Acceptance criteria

- Each LLM call logs token usage (exact or estimated).
- Report includes LLM cost estimate section.
- Existing tests pass.

## Verify

```bash
pytest tests/ -v -q
```

Add `tests/test_pricing.py` with unit tests for `estimate_cost` and `format_cost`.
