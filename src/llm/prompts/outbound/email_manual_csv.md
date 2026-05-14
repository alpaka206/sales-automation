---
output: json
---
Write a short opening email to a prospect identified through direct research (hand-curated list, not scraped). Company rules above govern tone, signature, and any forbidden phrases.

Prospect:
- name: {{full_name}}
- company: {{company}}
- domain: {{domain}}
- country: {{country}}
- researcher notes: {{summary}}
- company homepage summary: {{homepage_summary}}

Constraints:
- 120 words or less.
- The opening line must reference the researcher notes specifically. This prospect was individually selected, so the email must feel personal, not templated.
- One concrete ask at the end (book a 15-minute call OR share a relevant case study).
- Language: {{language}}.
- Include an unsubscribe / "let me know if not relevant" line at the very end.

Return strict JSON only:
{
  "subject": "<subject line>",
  "body": "<the email body, plain text, real line breaks>",
  "language": "ko" | "en"
}
