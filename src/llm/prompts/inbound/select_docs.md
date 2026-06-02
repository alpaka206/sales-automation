---
output: json
---
You are routing an inbound customer inquiry to the knowledge base documents that
will help draft an accurate reply.

Inbound inquiry (verbatim):
"""
{{inquiry}}
"""

Pre-computed category for this inquiry: {{category}}

Available knowledge base documents (index only — bodies are not shown):
{{doc_index}}

Task:
- Select ONLY the documents whose content is directly useful for replying to this
  specific inquiry. Judge by meaning, not just the category label.
- Prefer precision over recall: pick the few documents that actually answer the
  question. Do not select a document just because it shares the category.
- It is fine to select 0 documents if none are relevant (e.g. spam, or a generic
  greeting). It is fine to select several if the inquiry spans topics.
- Use the exact `slug` values from the index.

Return strict JSON only:
{
  "slugs": ["<slug>", "..."],
  "reasoning": "<one short sentence on why these docs>"
}
