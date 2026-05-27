---
output: json
---
You are analyzing a company based on its email domain and (optionally) homepage metadata.

## Input

- Domain: {{domain}}
- Company name hint (from CRM, may be empty/inaccurate): {{hint_company}}
- Homepage title: {{homepage_title}}
- Homepage meta description: {{homepage_description}}
- Homepage keywords: {{homepage_keywords}}
- Homepage fetch status: {{fetch_status}}

## Rules

- If `fetch_status` is NOT "ok", set `confidence` to "low".
- If you cannot determine the company with reasonable certainty, set `confidence` to "low", `company_name` to null, and `notes` to "insufficient signal".
- Do NOT guess or hallucinate company information. Only report what the available data supports.
- `size_hint` must be one of: "startup", "smb", "midmarket", "enterprise", "unknown".
- `confidence` must be one of: "high", "medium", "low".

## Output schema

Return strict JSON only:
```json
{
  "company_name": "<string or null>",
  "industry": "<free text, e.g. 'B2B SaaS — observability'>",
  "services": "<1-3 sentences describing what they do>",
  "target_market": "<who they sell to>",
  "size_hint": "startup | smb | midmarket | enterprise | unknown",
  "confidence": "high | medium | low",
  "notes": "<one line of extra context, or null>"
}
```
