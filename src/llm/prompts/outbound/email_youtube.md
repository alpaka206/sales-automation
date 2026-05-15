---
output: json
---
Write a short opening email to a YouTube channel owner. Company rules above govern tone and signature.

Prospect:
- channel name: {{ full_name }}
- subscribers: {{ summary }}
- country: {{ country }}
- domain: {{ domain }}
- company homepage summary: {{ homepage_summary }}

Constraints:
- Reference their YouTube channel specifically.
- 120 words or less.
- One concrete ask at the end.
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
