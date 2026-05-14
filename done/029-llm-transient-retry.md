# 029 — LLM transient error retry with backoff

## Why

Plan 05 specifies: "One automatic retry on transient errors (timeout, 5xx,
JSON parse fail)." The JSON parse retry exists, but HTTP timeouts and 5xx
from the Anthropic API are not retried. This causes spurious pipeline
failures on transient network issues.

## What to do

1. In `src/llm/client.py`, wrap `_dispatch()` with a retry decorator or
   inline retry loop:
   - Catch `httpx.TimeoutException`, `httpx.HTTPStatusError` (5xx only),
     `ClaudeCLIError` (timeout variant), and `RuntimeError` from providers.
   - Retry once after a 2-second wait.
   - Log the retry attempt.
   - If the retry also fails, raise the original error.

2. Do NOT retry on 4xx (auth errors, bad request) — those are permanent.

3. Add tests that mock a transient failure followed by success.

## Acceptance criteria

- A single transient failure (timeout or 5xx) is retried and recovered.
- Permanent errors (4xx, unknown provider) are NOT retried.
- Existing tests pass.

## Verify

```bash
pytest tests/test_llm_client.py -v
```
