---
output: json
---
You are classifying an inbound message that arrived in our HubSpot inbox.

Contact summary:
- name: {{contact_name}}
- company: {{company}}
- country: {{country}}
- lifecycle stage: {{lifecycle_stage}}

Most recent inbound message (verbatim):
"""
{{last_message}}
"""

{{enrichment_context}}

Return strict JSON only:
{
  "category": "purchase_inquiry" | "partnership" | "pricing_question" | "support" | "recruiting" | "spam" | "other",
  "reasoning": "<one short sentence in the same language as the message>"
}
