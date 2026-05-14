---
output: json
---
Write a short opening email to a prospect found via LinkedIn. Company rules above govern tone and signature.

Prospect:
- name: {{ full_name }}
- company: {{ company }}
- role/title context: {{ summary }}
- country: {{ country }}

Constraints:
- Reference their role and company specifically.
- 120 words or less.
- One concrete ask at the end (15-minute call or share a case study).
- Language: {{ language }}.
- Include an unsubscribe line at the end.

Return strict JSON only:
{
  "subject": "<subject line>",
  "body": "<the email body, plain text>",
  "language": "ko" | "en"
}
