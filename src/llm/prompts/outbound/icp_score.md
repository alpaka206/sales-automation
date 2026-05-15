---
output: json
---
Score this prospect against our ICP. Company rules above describe our ICP and constraints.

Prospect:
- name: {{full_name}}
- company: {{company}}
- domain: {{domain}}
- country: {{country}}
- source: {{source}}
- extra context: {{extra}}

Return strict JSON only:
{
  "score": <integer 0-100>,
  "rationale": "<one or two sentences>",
  "risks": ["<short risk>", ...],
  "language_guess": "<ISO 639-1 two-letter code: ko, en, ja, es, pt, zh, de, fr, etc.>"
}
