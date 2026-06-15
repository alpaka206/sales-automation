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

- The reader is a Korean administrator. Write the descriptive fields — `industry`, `services`, `target_market`, and `notes` — in **natural Korean (한국어)**. Keep `company_name` in its original/official form (do NOT translate or transliterate brand names), and keep `size_hint` / `confidence` as the exact enum values below.
- Base your answer on the homepage data and/or the web search findings above. BOTH are real evidence — you may rely on the search findings even when `fetch_status` is not "ok".
- Set `confidence` by how well the available evidence identifies the company: "high" when the homepage or search clearly identifies it, "medium" when only partial/indirect, "low" when little or conflicting.
- If NEITHER the homepage nor the search findings give usable signal, set `confidence` to "low", `company_name` to null, and `notes` to "신호 부족".
- Do NOT guess or hallucinate beyond what the homepage data or search findings support.
- `size_hint` must be one of: "startup", "smb", "midmarket", "enterprise", "unknown".
- `confidence` must be one of: "high", "medium", "low".

## Output schema

Return strict JSON only:
```json
{
  "company_name": "<string or null — 원문 그대로, 번역 금지>",
  "industry": "<한국어 짧은 구, 최대 ~100자, 예: 'B2B SaaS — 관측성(옵저빌리티)'>",
  "services": "<한국어 1~3문장, 무엇을 하는 회사인지 설명>",
  "target_market": "<한국어 짧은 구, 최대 ~100자, 누구에게 판매하는지>",
  "size_hint": "startup | smb | midmarket | enterprise | unknown",
  "confidence": "high | medium | low",
  "notes": "<한국어 한 줄 추가 맥락, 또는 null>"
}
```
