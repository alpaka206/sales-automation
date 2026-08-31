"""The 회신 및 검토 queue: which rows appear, in what order, and how they are labelled.

The list was rebuilt around two questions an operator actually asks — "what still
needs me?" and "who has been waiting longest?" — so the chips are status buckets
rather than one chip per status, and the columns describe the INQUIRY rather than our
draft of the reply.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from src.api.routes import messages as messages_route
from src.api.routes.messages import LIST_STATUS_BUCKETS, _messages_list_context
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
                    subject="RE: 우리 답변 제목",
                    body="draft",
                    status=status,
                )
            )
        session.commit()
    return db_session_factory


def _emails(**kwargs) -> set[str]:
    return {row["email"] for row in _messages_list_context(**kwargs)["messages"]}


def test_awaiting_is_new_only_whatever_the_status_says(queue):
    """발송 대기 is New, always — and this is the query, not just the chip.

    Drafts are only ever generated for New tickets (InboundAgent returns
    "skipped_not_new" for anything else), so a waiting draft on a later stage means the
    ticket moved on: somebody answered it in HubSpot while ours sat here. Listing it asks
    the operator to send a reply the customer already received.

    That includes a draft whose SEND failed, like nego-failed here. It is not lost — a
    failed send belongs to 운영 로그 ▸ 복구, which owns retrying it.
    """
    assert _emails(status="awaiting") == {
        "new-pending@example.com",
        "new-drafting@example.com",
    }
    assert "nego-failed@example.com" not in _emails(status="awaiting")


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
    assert _emails(status="awaiting", stage="new") == {
        "new-pending@example.com",
        "new-drafting@example.com",
    }
    assert _emails(status="sent", stage="negotiation") == {"nego-sent@example.com"}
    # Negotiating is not a 발송 대기 chip any more, so asking for it there falls back to
    # 전체 rather than filtering — the bucket still holds the row, it just has no chip.
    assert _emails(status="awaiting", stage="negotiation") == _emails(status="awaiting")


def test_stage_chips_differ_by_status_bucket(queue):
    """The two buckets sit at opposite ends of the pipeline.

    발송 대기 can only hold tickets nobody has answered (New); sending is what moves a
    ticket past New, so 발송 완료 offers the downstream stages instead. One shared chip
    row offered New to 발송 완료 (always empty) and hid Won/Lost/Closed from it.

    발송 대기 gets NO chip row: once the Negotiating chip was dropped it was down to
    전체 and New over the same rows, and a filter that cannot filter is worse than none.
    """
    awaiting = _messages_list_context(status="awaiting")["stage_chips"]
    sent = _messages_list_context(status="sent")["stage_chips"]
    assert awaiting == []
    assert [key for key, _ in sent] == [
        "",
        "meeting_link_sent",
        "negotiation",
        "reminder_sent",
        "won",
        "closed_lost",
        "closed",
    ]
    # Labels and order are the board's, not a second hand-written list.
    assert [label for _, label in sent] == [
        "전체",
        "Qualified",
        "Negotiating",
        "Reminder Sent",
        "Won",
        "Lost",
        "Concluded",
    ]


def test_stage_carried_over_from_the_other_bucket_falls_back_to_all(queue):
    """The bucket chips keep the current stage in their href, so 발송 대기(New) → 발송 완료
    arrives with stage=new — a combination that can never match. Show everything the
    bucket has instead of an empty table."""
    ctx = _messages_list_context(status="sent", stage="new")
    assert ctx["filter_stage"] == ""
    assert _emails(status="sent", stage="new") == _emails(status="sent")


def test_rows_carry_the_inquiry_subject_not_our_reply_subject(queue):
    rows = _messages_list_context(status="awaiting")["messages"]
    assert all(row["subject"].startswith("문의 ") for row in rows)
    assert not any("우리 답변 제목" in row["subject"] for row in rows)


def test_the_column_never_shows_the_re_prefix_we_added(db_session_factory, monkeypatch):
    """문의 제목 is the HubSpot subject, and "RE:" is ours, not theirs.

    A ticket with no stored inquiry_subject (drafting rows can predate it) falls back to
    our reply subject — which is built as "RE: <original>" — so the prefix we added has
    to come back off. A "Re:" the CUSTOMER wrote is part of their subject and stays.
    """
    monkeypatch.setattr(messages_route, "SessionLocal", db_session_factory)
    with db_session_factory() as session:
        for email, inquiry_subject in (
            ("fallback@example.com", None),
            ("their-own-re@example.com", "Re: 지난주 견적 건"),
        ):
            contact = Contact(normalized_email=email, email=email, full_name=email)
            session.add(contact)
            session.flush()
            conv = Conversation(
                contact_id=contact.id, stage="new", inquiry_subject=inquiry_subject
            )
            session.add(conv)
            session.flush()
            session.add(
                Message(
                    conversation_id=conv.id,
                    direction="outgoing",
                    subject="RE: 더빙 단가 문의",
                    body="draft",
                    status="pending_approval",
                )
            )
        session.commit()

    subjects = {
        row["email"]: row["subject"] for row in _messages_list_context()["messages"]
    }
    assert subjects["fallback@example.com"] == "더빙 단가 문의"
    assert subjects["their-own-re@example.com"] == "Re: 지난주 견적 건"


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


def test_priority_is_measured_from_the_customers_last_message(queue):
    """The dot's colour is decided in the screen from ``waiting_since`` (QueueTable.tsx);
    what the server owes it is that value, measured from the CUSTOMER's last message and
    not from our draft — "how long have they been waiting?"."""
    ctx = _messages_list_context(status="awaiting")
    waited = {
        row["email"]: (ctx["now"] - row["waiting_since"]).days for row in ctx["messages"]
    }
    assert waited["new-pending@example.com"] == 0     # → green
    assert waited["new-drafting@example.com"] == 2    # → orange
    # nego-failed is not here to check: 발송 대기 is New only.


def test_the_stage_labels_come_from_the_board_not_a_second_list(queue):
    """The column is headed "Stage" and must read New / Negotiating, never the raw key.

    The board, the dashboard and this list all show stages; each one that builds its own
    mapping is a place the wording can drift, so the server ships one map and every
    screen looks the label up in it.
    """
    labels = _messages_list_context(status="awaiting")["stage_labels"]
    assert labels["new"] == "New"
    assert labels["negotiation"] == "Negotiating"


def test_a_korean_inquiry_offers_no_original_view() -> None:
    """한국어로 온 문의에는 「원문 보기」가 뜨지 않습니다 (2026-08-19 운영자 지시).

    전에는 **제목만** 영문이어도 번역 UI 가 켜졌습니다. 한국어 본문에 제목이
    "Custom Quote" 인 문의가 흔한데, 그때 「원문 보기」를 눌러 봐야 같은 한국어 본문이
    나옵니다 — 제목 한 줄이 영문인 것은 읽는 데 걸림돌이 아닙니다.

    본문이 비어 있을 때만 제목으로 판단합니다: 그때는 제목이 곧 문의 전부입니다.
    """
    from src.llm.translate import needs_korean

    def needs_ko(body: str, subject: str) -> bool:
        # 라우트가 쓰는 규칙 그대로. 여기서 갈라지면 화면이 다시 옛 동작으로 돌아갑니다.
        return needs_korean(body) or (not body.strip() and needs_korean(subject))

    assert needs_ko("영어 문의입니다만 영문", "Custom Quote") is False
    assert needs_ko("We need dubbing for 600 minutes.", "Custom Quote") is True
    assert needs_ko("", "Custom Quote") is True
    assert needs_ko("", "맞춤 견적 문의") is False


def test_a_hand_written_follow_up_shows_even_on_a_later_stage(db_session_factory, monkeypatch):
    """**수동 후속 회신은 「New 만」 규칙의 예외입니다** (2026-08-31).

    위 규칙의 뜻은 「자동 초안은 New 에서만 생기므로, 그 뒤 단계에 남은 대기 초안은 이미
    늦은 것」입니다. 운영자가 협상 중인 티켓에 직접 쓴 회신은 늦은 것이 아니라 지금 하는
    일이고, 걸러 내면 쓰다 만 초안을 다시 찾을 길이 그 티켓 화면 하나뿐입니다.
    """
    monkeypatch.setattr(messages_route, "SessionLocal", db_session_factory)
    with db_session_factory() as session:
        contact = Contact(normalized_email="nego@example.com", email="nego@example.com",
                          full_name="협상 중")
        session.add(contact)
        session.flush()
        conv = Conversation(contact_id=contact.id, stage="negotiation",
                            inquiry_subject="협상 문의", last_incoming_at=_naive_utc(days=1))
        session.add(conv)
        session.flush()
        session.add(Message(conversation_id=conv.id, direction="outgoing",
                            subject="RE: 협상 문의", body="직접 쓴 글",
                            status="pending_approval", prompt_variant="manual"))
        session.commit()

    assert _emails(status="awaiting") == {"nego@example.com"}
