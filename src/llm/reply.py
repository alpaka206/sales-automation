"""Code-enforced Korean drafting for reply bodies.

Hard rule: the DRAFT the operator reviews is always Korean — :func:`ensure_korean`
translates it if the drafting model wrote in another language, washing the layout
regardless of model output.

(The separate send-time guarantee that a reply leaves in the inquiry's language
lives in :func:`src.integrations.senders.enforce_send_language`, which works off the
message's stored language/target metadata.)
"""

from __future__ import annotations

from ..common.textwash import text_wash
from .client import LLMClient
from .translate import needs_korean, to_korean


def ensure_korean(body: str | None, *, llm: LLMClient | None = None) -> str:
    """Return ``body`` in Korean (washed). Translates only if it isn't already.

    Used so the operator-facing draft is always Korean even when the drafting
    model ignored the instruction and replied in the customer's language.
    """
    washed = text_wash(body)
    if not washed:
        return ""
    if not needs_korean(washed):
        return washed
    ko = to_korean(washed, llm=llm)
    return text_wash(ko) if ko else washed
