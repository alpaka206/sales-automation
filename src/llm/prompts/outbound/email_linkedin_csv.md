---
output: json
---
Write a short opening email to a prospect found via LinkedIn. Company rules above govern tone and signature.

Prospect:
- name: {{ full_name }}
- company: {{ company }}
- role/title context: {{ summary }}
- country: {{ country }}
- company homepage summary: {{ homepage_summary }}

Constraints:
- Reference their role and company specifically.
- 120 words or less.
- One concrete ask at the end (15-minute call or share a case study).
- Include an unsubscribe line at the end.

## Language enforcement
- You MUST write the entire email in {{ language }}.
- If your draft is in a different language, translate it before responding.
- Subject + body + signature all in {{ language }}.
- If {{ language }} is not "ko", adapt the signature naturally (e.g. "Kyuwon Kim / perso / Intern").

Return strict JSON only:
{
  "subject": "<subject line>",
  "body": "<the email body, plain text>",
  "language": "<ISO 639-1 code of the language you actually used>"
}
