# 05 — LLM Layer

## Goal

A single entry point — `complete()` — that all agents call. The only provider is Gemini on Vertex AI. Prompts are markdown files, not Python string literals. JSON outputs are validated against pydantic schemas.

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

## Provider — Gemini on Vertex AI (`src/llm/providers/gemini_vertex.py`)

```python
from google import genai
from google.oauth2 import service_account

creds = service_account.Credentials.from_service_account_info(
    json.loads(settings.GOOGLE_CREDENTIALS_JSON),
    scopes=["https://www.googleapis.com/auth/cloud-platform"],
)
client = genai.Client(
    vertexai=True,
    project=settings.GOOGLE_CLOUD_PROJECT or creds.project_id,
    location=settings.GOOGLE_CLOUD_LOCATION,
    credentials=creds,
)
resp = client.models.generate_content(model=settings.GEMINI_MODEL, contents=prompt, config=...)
```

- Authentication is a **service-account JSON** in `GOOGLE_CREDENTIALS_JSON` — no API key.
- `company_rules` go in as the `system_instruction`; the rendered prompt is the user content.
- Streaming is not required (replies < 1k tokens).
- For JSON-shaped output: append explicit instructions to return only valid JSON, then validate against the schema. On failure: retry once with a stronger reminder.

## Failure handling

- One automatic retry on transient errors (timeout, 5xx, JSON parse fail).
- Hard failure → raise `LLMError`, the caller decides whether to mark the message `errored` (don't approve) or drop the run.
- Every call writes one row into `events` table (`kind=llm_call`, payload = `{prompt_name, provider, latency_ms, ok, error_excerpt}`) — never the full prompt or response (PII).

## Cost tracking

- Capture `prompt_token_count` / `candidates_token_count` from the response's `usage_metadata`, multiply by the hardcoded rate table in `src/llm/pricing.py`, and store on the usage record.

## Testing

- `tests/test_llm_client.py` covers:
  - Prompt rendering with placeholders
  - company_rules concatenation
  - JSON schema validation (happy path + malformed retry)
  - Gemini Vertex call builds the client from `GOOGLE_CREDENTIALS_JSON` and calls `generate_content` (mock the `google-genai` client)

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
