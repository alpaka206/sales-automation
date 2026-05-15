---
output: json
---
Write a short opening email to a prospect found via web search. Company rules above govern tone and signature.

Prospect:
- organization: {{ full_name }}
- category: {{ category }}
- search snippet: {{ summary }}
- domain: {{ domain }}
- company homepage summary: {{ homepage_summary }}

Category guidelines:
- university: reference their research or department. Academic tone.
- conference: reference the event or society. Professional tone.
- religious: respectful, non-promotional tone. Mention community value.
- other: standard professional tone.

Constraints:
- Reference how you found them (web search context from the snippet).
- 120 words or less.
- One concrete ask at the end.
- Language: {{ language }}.
- Include an unsubscribe line at the end.

Return strict JSON only:
{
  "subject": "<subject line>",
  "body": "<the email body, plain text>",
  "language": "ko" | "en"
}
