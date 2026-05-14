# 05 — LLM Layer

## Goal

A single entry point — `complete()` — that all agents call. Provider is decided by `LLM_PROVIDER` env var. Prompts are markdown files, not Python string literals. JSON outputs are validated against pydantic schemas.

## Public API

```python
# src/llm/client.py
class LLMClient:
    def complete(
        self,
        prompt_name: str,                 # e.g. "inbound/draft_reply"
        variables: dict[str, Any],        # rendered into the prompt template
        schema: type[BaseModel] | None,   # if given, parsed and validated
        max_tokens: int = 2000,
    ) -> str | BaseModel: ...
```

## Prompt loading

- `src/llm/prompts/<area>/<name>.md`
- Files use simple `{{var_name}}` placeholders.
- Auto-prepended once at the top: the concatenated contents of `company_rules/*.md` in filename order.
- The prompt file may include a `---` front-matter block with `output: json` to signal JSON expected.

## Providers

### `claude_cli` (default, no API key needed)

```python
def _call_claude_cli(prompt: str) -> str:
    res = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "text"],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "CLAUDE_DISABLE_TELEMETRY": "1"},
    )
    return res.stdout
```

- Streaming is not required for our use case (replies < 1k tokens).
- For JSON-shaped output: append explicit instructions to return only valid JSON, then try `json.loads`. On failure: retry once with stronger reminder.

### `anthropic_api`

- Standard `anthropic.Anthropic().messages.create(...)`.
- Use `claude-sonnet-4-6` by default.
- If `schema` is given and the SDK version supports tool-use response shaping, use it; otherwise fall back to "return JSON" + parse.

## Failure handling

- One automatic retry on transient errors (timeout, 5xx, JSON parse fail).
- Hard failure → raise `LLMError`, the caller decides whether to mark the message `errored` (don't approve) or drop the run.
- Every call writes one row into `events` table (`kind=llm_call`, payload = `{prompt_name, provider, latency_ms, ok, error_excerpt}`) — never the full prompt or response (PII).

## Cost tracking

- For `anthropic_api`: capture `input_tokens` and `output_tokens` from response, multiply by hardcoded rate table in `src/llm/pricing.py`, store on the event.
- For `claude_cli`: tokens unknown, record `tokens=null`.

## Testing

- `tests/test_llm_client.py` covers:
  - Prompt rendering with placeholders
  - company_rules concatenation
  - JSON schema validation (happy path + malformed retry)
  - Provider selection via env var
  - Subprocess call uses `claude -p` with correct args (mock `subprocess.run`)

## Sample prompt file

```markdown
---
output: json
---
You are classifying inbound sales inquiries.

The contact:
- name: {{contact_name}}
- company: {{company}}
- country: {{country}}
- last message: {{last_message}}

Return JSON only, matching:
{
  "category": "purchase_inquiry" | "partnership" | "pricing_question" | "support" | "recruiting" | "spam" | "other",
  "reasoning": "<one short sentence>"
}
```
