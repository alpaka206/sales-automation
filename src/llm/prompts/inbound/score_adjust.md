---
output: json
---
You are scoring an inbound lead. A rule-based system already assigned a base score.
Your job is to adjust it by -20 to +20 based on qualitative signals.

Contact:
- name: {{ contact_name }}
- company: {{ company }}
- country: {{ country }}

Category: {{ category }}
Base score: {{ base_score }}

Most recent inbound message:
"""
{{ last_message }}
"""

Consider urgency, purchase intent, service fit, and specificity of the request.

Return strict JSON only:
{
  "adjustment": <integer from -20 to 20>,
  "reasoning": "<one sentence>"
}
