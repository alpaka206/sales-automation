---
output: json
---
Write a short opening email to a cold prospect. Company rules above govern tone, signature, and any forbidden phrases.

Prospect:
- name: {{full_name}}
- company: {{company}}
- domain: {{domain}}
- country: {{country}}
- one-liner about them: {{summary}}
- company homepage summary: {{homepage_summary}}

Constraints:
- 120 words or less.
- Open with a specific reason you reached out, not a generic compliment.
- One concrete ask at the end (book a 15-minute call OR share a relevant case study).
- Include an unsubscribe / "let me know if not relevant" line at the very end.

## Language enforcement
- You MUST write the entire email in {{language}}.
- If your draft is in a different language, translate it before responding.
- Subject + body + signature all in {{language}}.
- If {{language}} is not "ko", adapt the signature naturally (e.g. "Kyuwon Kim / perso / Intern").

Return strict JSON only:
{
  "subject": "<subject line>",
  "body": "<the email body, plain text, real line breaks>",
  "language": "<ISO 639-1 code of the language you actually used>"
}
