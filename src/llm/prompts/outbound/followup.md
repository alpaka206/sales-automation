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

## Language enforcement
- You MUST write the entire email in {{ language }}.
- If your draft is in a different language, translate it before responding.
- Subject + body + signature all in {{ language }}.
- If {{ language }} is not "ko", adapt the signature naturally (e.g. "Kyuwon Kim / perso / Intern").

Return strict JSON only:
{
  "subject": "<subject line - typically Re: original subject>",
  "body": "<the follow-up body, plain text>",
  "language": "<ISO 639-1 code of the language you actually used>"
}
