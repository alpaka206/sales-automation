"""The 답변 검토 queue: which rows appear, in what order, and how they are labelled.

The list was rebuilt around two questions an operator actually asks — "what still
needs me?" and "who has been waiting longest?" — so the chips are status buckets
rather than one chip per status, and the columns describe the INQUIRY rather than our
draft of the reply.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from src.api.web.routes import messages as messages_route
from src.api.web.routes.messages import LIST_STATUS_BUCKETS, _messages_list_context
from src.db.models import Contact, Conversation, Message


def _naive_utc(**delta) -> datetime:
    return (datetime.now(timezone.utc) - timedelta(**delta)).replace(tzinfo=None)


@pytest.fixture()
def queue(db_session_factory, monkeypatch):
    """One conversation per (stage, status) case the chips have to separate."""
    monkeypatch.setattr(messages_route, "SessionLocal", db_session_factory)
    with db_session_factory() as session:
        rows = [
            # (email, stage, status, days the customer has been waiting)
            ("new-pending@example.com", "new", "pending_approval", 0),
            ("new-drafting@example.com", "new", "drafting", 2),
            ("nego-failed@example.com", "negotiation", "send_failed", 9),
            ("nego-sent@example.com", "negotiation", "sent", 1),
            ("queued@example.com", "new", "approved", 1),
            ("unknown@example.com", "new", "delivery_unknown", 1),
        ]
        for email, stage, status, waited in rows:
            contact = Contact(normalized_email=email, email=email, full_name=email)
            session.add(contact)
            session.flush()
            conv = Conversation(
                contact_id=contact.id,
                stage=stage,
                inquiry_subject=f"문의 {email}",
                last_incoming_at=_naive_utc(days=waited),
            )
            session.add(conv)
            session.flush()
            session.add(
                Message(
                    conversation_id=conv.id,
                    direction="outgoing",
                    channel="email",
                    subject="RE: 우리 답변 제목",
                    body="draft",
                    status=status,
                )
            )
        session.commit()
    return db_session_factory


def _emails(**kwargs) -> set[str]:
    return {row["email"] for row in _messages_list_context(**kwargs)["messages"]}


def test_awaiting_bucket_is_everything_a_human_must_still_act_on(queue):
    assert _emails(status="awaiting") == {
        "new-pending@example.com",
        "new-drafting@example.com",
        "nego-failed@example.com",
    }


def test_queued_and_delivery_unknown_are_not_in_the_queue(queue):
    """approved has nothing to decide; delivery_unknown is resolved on 운영 로그."""
    assert "approved" not in LIST_STATUS_BUCKETS["awaiting"]
    assert "delivery_unknown" not in LIST_STATUS_BUCKETS["awaiting"]
    assert "delivery_unknown" not in LIST_STATUS_BUCKETS["sent"]
    everything = _emails(status="awaiting") | _emails(status="sent")
    assert "queued@example.com" not in everything
    assert "unknown@example.com" not in everything


def test_rejected_sits_with_completed_not_with_waiting(queue):
    """거절 is a finished outcome, so it belongs under 발송 완료."""
    assert "rejected" in LIST_STATUS_BUCKETS["sent"]
    assert "rejected" not in LIST_STATUS_BUCKETS["awaiting"]


def test_stage_chip_filters_the_bucket(queue):
    assert _emails(status="awaiting", stage="negotiation") == {"nego-failed@example.com"}
    assert _emails(status="awaiting", stage="new") == {
        "new-pending@example.com",
        "new-drafting@example.com",
    }


def test_rows_carry_the_inquiry_subject_not_our_reply_subject(queue):
    rows = _messages_list_context(status="awaiting")["messages"]
    assert all(row["subject"].startswith("문의 ") for row in rows)
    assert not any("우리 답변 제목" in row["subject"] for row in rows)


def test_default_order_is_oldest_first(queue):
    """The queue is worked FIFO, so the default must not be newest-first."""
    ctx = _messages_list_context()
    assert ctx["filter_sort"] == "oldest"
    created = [row["received_at"] for row in ctx["messages"]]
    assert created == sorted(created)
    newest = [row["received_at"] for row in _messages_list_context(sort="newest")["messages"]]
    assert newest == sorted(newest, reverse=True)


def test_unknown_filter_values_fall_back_instead_of_reaching_sql(queue):
    """These are interpolated into the template's polling URL, so they are allow-listed."""
    ctx = _messages_list_context(status="' OR 1=1--", stage="nonsense", sort="sideways")
    assert (ctx["filter_status"], ctx["filter_stage"], ctx["filter_sort"]) == (
        "awaiting",
        "",
        "oldest",
    )


def test_priority_dot_reflects_how_long_the_customer_waited(queue):
    """Rendered straight from the context: the in-memory DB is per-connection, so a
    TestClient request would reach a different (empty) database."""
    from src.api.web.routes._shared import templates

    html = templates.get_template("messages_list.html").render(
        _messages_list_context(status="awaiting")
    )
    # 0 days waited -> green, 2 days -> orange, 9 days -> red.
    assert "wait-dot--ok" in html
    assert "wait-dot--warn" in html
    assert "wait-dot--danger" in html


def test_stage_column_shows_the_label_not_the_raw_key(queue):
    """The column is headed "Stage" and must read New / Negotiating.

    The board, the dashboard and this list all render stages; each one that builds
    its own mapping is a place the wording can drift, so all three use PIPELINE_STAGES.
    """
    from src.api.web.routes._shared import templates

    html = templates.get_template("messages_list.html").render(
        _messages_list_context(status="awaiting")
    )
    assert ">New<" in html
    assert ">Negotiating<" in html
    assert ">negotiation<" not in html
