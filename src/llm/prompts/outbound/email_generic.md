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

Constraints:
- 120 words or less.
- Open with a specific reason you reached out, not a generic compliment.
- One concrete ask at the end (book a 15-minute call OR share a relevant case study).
- Language: {{language}}.
- Include an unsubscribe / "let me know if not relevant" line at the very end.

Return strict JSON only:
{
  "subject": "<subject line>",
  "body": "<the email body, plain text, real line breaks>",
  "language": "ko" | "en"
}
