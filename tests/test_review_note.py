"""검토 필요 — which of the waiting drafts is the risky one.

Not a second approval gate. Every detailed reply already waits for a human and always
has; that is pinned in tests/test_safe_mode.py. What this adds is a reason to open one
draft before the others, from the two things the policy calls ambiguous (§1, §2-4).
"""

from __future__ import annotations

import pytest

from src.agents.inbound import _review_note


@pytest.mark.parametrize(
    ("category", "had_documents"),
    [("pricing_question", True), ("purchase_inquiry", True), ("other", True),
     ("languages", True), ("partnership", True)],
)
def test_an_ordinary_draft_says_nothing(category, had_documents):
    """The badge is worth something only while it is rare. A note on every row is a
    column of noise the operator learns to skip."""
    assert _review_note(category, had_documents) is None


def test_a_draft_with_no_document_behind_it_is_flagged():
    """§2-4: 모르는 건 지어내지 않는다. A reply written with nothing to draw on is
    precisely the case that rule exists for, and it looks identical to a well-sourced
    one from the outside."""
    note = _review_note("pricing_question", False)
    assert note and "근거를 찾지 못했" in note


@pytest.mark.parametrize("category", ["spam", "support", "recruiting"])
def test_a_lead_that_is_not_a_lead_is_flagged(category):
    """§1 qualifies these out. A sales-shaped reply to a CS problem is the wrong reply,
    and the queue gives no other sign that this row is different."""
    assert _review_note(category, True) is not None


def test_both_reasons_are_reported_together():
    """Two problems is not the same as one, and picking a winner hides half of it."""
    note = _review_note("support", False)
    assert "CS" in note and "근거를 찾지 못했" in note


def test_it_is_not_an_approval_gate():
    """The flag must never gate sending — that would make it a second approval, and
    approval is already the human's. It only annotates."""
    import inspect

    from src.agents import inbound

    source = inspect.getsource(inbound.InboundAgent._finalize_draft)
    assert 'msg.status = "pending_approval"' in source
    # No branch anywhere makes status depend on the note.
    assert "review_note" in source and "if msg.review_note" not in source
