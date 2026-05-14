---
output: json
---
Write a short opening email to a YouTube channel owner. Company rules above govern tone and signature.

Prospect:
- channel name: {{ full_name }}
- subscribers: {{ summary }}
- country: {{ country }}
- domain: {{ domain }}

Constraints:
- Reference their YouTube channel specifically.
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
