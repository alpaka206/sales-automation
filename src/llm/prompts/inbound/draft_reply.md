---
output: json
---
Draft a reply to this inbound message. Tone and policy are governed by the company rules above.

Context:
- contact: {{contact_name}} ({{company}}, {{country}})
- category: {{category}}
- score: {{score}}
- language to reply in: {{language}}

Most recent inbound (verbatim):
"""
{{last_message}}
"""

{{enrichment_context}}

Constraints:
- Match the language of the inbound message unless the company rules say otherwise.
- Be concise. No more than 5 short paragraphs.
- Do not promise pricing, delivery dates, or contracts. Offer a meeting or next step instead.
- Sign with the team signature defined in company rules.

Return strict JSON only:
{
  "subject": "<subject line, empty string for whatsapp>",
  "body": "<the reply, plain text, real line breaks>",
  "language": "ko" | "en",
  "tone_notes": "<optional one-liner about tone choices you made>"
}
