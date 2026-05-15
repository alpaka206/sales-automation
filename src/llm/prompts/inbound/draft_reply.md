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

{{knowledge_docs}}

Constraints:
- Match the language of the inbound message unless the company rules say otherwise.
- Be concise. No more than 5 short paragraphs.
- Do not quote prices, delivery dates, or contractual terms verbatim from the knowledge base. You may *acknowledge* that pricing/policy information is available and offer a meeting or follow-up email with specifics.
- If the knowledge base contains a fact directly relevant to the question (e.g. plan tiers, refund policy, supported regions), reference it qualitatively — never invent facts that aren't in the knowledge base or the inbound message.
- Sign with the team signature defined in company rules.

Return strict JSON only:
{
  "subject": "<subject line, empty string for whatsapp>",
  "body": "<the reply, plain text, real line breaks>",
  "language": "ko" | "en",
  "tone_notes": "<optional one-liner about tone choices you made>"
}
