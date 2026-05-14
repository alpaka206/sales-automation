# 031 — Anthropic API prompt caching

## Why

Every LLM call prepends company_rules text (~1KB). With prompt caching
enabled on the Anthropic API, this shared prefix is cached server-side,
reducing latency and cost for subsequent calls within the cache TTL.

## What to do

1. In `src/llm/providers/anthropic_api.py`, update `call_anthropic()`:
   - Split the prompt into a system message (company_rules prefix) and a
     user message (the actual prompt).
   - Add `cache_control: {"type": "ephemeral"}` to the system message
     block so Anthropic caches it.
   - This requires the `anthropic` SDK to support the `cache_control`
     parameter (v0.39+).

2. In `src/llm/prompts/__init__.py`, expose a function to get just the
   company_rules prefix separately from the prompt body.

3. Update `LLMResult` to include `cache_read_input_tokens` and
   `cache_creation_input_tokens` if available from the API response.

## Acceptance criteria

- Anthropic API calls use system message with cache_control.
- Token usage logging includes cache hit info when available.
- Existing tests pass (mock still returns text).

## Verify

```bash
pytest tests/test_llm_client.py tests/test_pricing.py -v
```
