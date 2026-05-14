---
output: json
---
Write a follow-up email. The prospect has not replied to our previous message.
Company rules above govern tone and forbidden phrases.

Context:
- name: {{ full_name }}
- company: {{ company }}
- previous subject: {{ previous_subject }}
- days since last email: {{ days_since }}
- follow-up number: {{ followup_number }}

Constraints:
- Keep it shorter than the first email (3 paragraphs max).
- Reference the previous email without being pushy.
- Offer one clear next step.
- Language: {{ language }}.

Return strict JSON only:
{
  "subject": "<subject line - typically Re: original subject>",
  "body": "<the follow-up body, plain text>",
  "language": "ko" | "en"
}
