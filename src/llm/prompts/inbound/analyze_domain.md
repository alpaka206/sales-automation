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
- Homepage body excerpt: {{homepage_body}}
- Homepage fetch status: {{fetch_status}}
- Web search findings: {{search_findings}}

## Rules

- Base your answer on the homepage data and/or the web search findings above. BOTH are real evidence — you may rely on the search findings even when `fetch_status` is not "ok".
- Set `confidence` by how well the available evidence identifies the company: "high" when the homepage or search clearly identifies it, "medium" when only partial/indirect, "low" when little or conflicting.
- If NEITHER the homepage nor the search findings give usable signal, set `confidence` to "low", `company_name` to null, and `notes` to "insufficient signal".
- Do NOT guess or hallucinate beyond what the homepage data or search findings support.
- `size_hint` must be one of: "startup", "smb", "midmarket", "enterprise", "unknown".
- `confidence` must be one of: "high", "medium", "low".

## Output schema

Return strict JSON only:
```json
{
  "company_name": "<string or null>",
  "industry": "<short phrase, max ~100 chars, e.g. 'B2B SaaS — observability'>",
  "services": "<1-3 sentences describing what they do>",
  "target_market": "<short phrase, max ~100 chars, who they sell to>",
  "size_hint": "startup | smb | midmarket | enterprise | unknown",
  "confidence": "high | medium | low",
  "notes": "<one line of extra context, or null>"
}
```
