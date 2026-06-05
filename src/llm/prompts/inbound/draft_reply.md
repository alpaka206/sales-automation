---
output: json
---
Draft a reply to this inbound message. Tone and policy are governed by the company rules above.

Context:
- contact: {{contact_name}} ({{company}}, {{country}})
- category: {{category}}
- score: {{score}}

Most recent inbound (verbatim):
"""
{{last_message}}
"""

{{enrichment_context}}

{{knowledge_docs}}

Constraints:
- LANGUAGE (mandatory): Reply in the EXACT same language as the customer's inbound message above. Detect the language from the message text itself — NOT from the contact's country. If the inbound is in English, reply in English; if Japanese, reply in Japanese; if Korean, reply in Korean; and so on. Never switch languages.
- Be concise. No more than 5 short paragraphs.
- Do not quote prices, delivery dates, or contractual terms verbatim from the knowledge base. You may *acknowledge* that pricing/policy information is available and offer a meeting or follow-up email with specifics.
- If the knowledge base contains a fact directly relevant to the question (e.g. plan tiers, refund policy, supported regions), reference it qualitatively — never invent facts that aren't in the knowledge base or the inbound message.
- Sign with the team signature defined in company rules.

Return strict JSON only:
{
  "subject": "<subject line, empty string for whatsapp>",
  "body": "<the reply, plain text, real line breaks>",
  "language": "<ISO 639-1 code of the language you wrote the reply in, e.g. en, ko, ja, vi, th, zh>",
  "tone_notes": "<optional one-liner about tone choices you made>"
}
