"""The 회신 및 검토 queue: which rows appear, in what order, and how they are labelled.

The list was rebuilt around two questions an operator actually asks — "what still
needs me?" and "who has been waiting longest?" — so the chips are status buckets
rather than one chip per status, and the columns describe the INQUIRY rather than our
draft of the reply.

**답변 대기 spans every stage** (2026-10-08 운영자: 「상대에게 답장이 왔으면 어떤 단계든 표시되도록」): New holds the
draft to review, as it always did; every later stage holds a customer who wrote after our last sales email.
발송 완료 is still about our messages.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from src.agents.followup_sequence import REMINDER_VARIANTS
from src.api.routes import messages as messages_route
from src.api.routes.messages import AWAITING_REPLY_DAYS, LIST_STATUS_BUCKETS, _messages_list_context
from src.db.models import Approval, Contact, Conversation, CustomerInteraction, Message


def _naive_utc(**delta) -> datetime:
    return (datetime.now(timezone.utc) - timedelta(**delta)).replace(tzinfo=None)


def _ticket(session, email: str, stage: str, subject: str | None = None) -> Conversation:
    contact = Contact(normalized_email=email, email=email, full_name=email)
    session.add(contact)
    session.flush()
    conv = Conversation(contact_id=contact.id, stage=stage, hubspot_ticket_id=f"T-{email}",
                        inquiry_subject=f"문의 {email}" if subject is None else subject)
    session.add(conv)
    session.flush()
    return conv


def _inquiry(session, conv: Conversation, days: float) -> None:
    """접수가 만든 문의 행 — 대화의 첫 고객 말. 티켓도 그때 섰다."""
    conv.created_at = _naive_utc(days=days)
    session.add(Message(conversation_id=conv.id, direction="inbound", body=f"문의 본문 {conv.id}",
                        status="received", created_at=_naive_utc(days=days)))


def _customer_replied(session, conv: Conversation, days: float, *, contact_id: int | None = None) -> None:
    """New 이후의 고객 말은 접점 기록에 산다 — 허브스팟 스레드 수집 · 개인함 · 손으로 적은 「수신」."""
    session.add(CustomerInteraction(contact_id=contact_id or conv.contact_id, conversation_id=conv.id,
                                    channel="이메일", direction="inbound", summary=f"고객 답장 {days}",
                                    external_id=f"hubspot:conv:{conv.id}-{days}", happened_at=_naive_utc(days=days)))


def _we_wrote(session, conv: Conversation, days: float, status: str = "sent", variant: str | None = None) -> Message:
    at = _naive_utc(days=days)
    msg = Message(conversation_id=conv.id, direction="outgoing", subject="RE: 우리 답변 제목", body="우리 글",
                  status=status, prompt_variant=variant, created_at=at,
                  sent_at=at if status == "sent" else None)
    session.add(msg)
    session.flush()
    return msg


def _rejected(session, msg: Message, days: float) -> None:
    """검토 화면의 「거절」 — 상태와 함께 그 시각이 승인 기록에 남는다(`approval.reject`)."""
    msg.status = "rejected"
    session.add(Approval(message_id=msg.id, approver="op", action="reject", created_at=_naive_utc(days=days)))


WAITING = {
    "new-pending@example.com",
    "new-drafting@example.com",
    "new-old-draft@example.com",
    "new-after-old-sales@example.com",
    "new-mail-after-draft@example.com",
    "new-answered-outside@example.com",
    "nego-replied@example.com",
    "won-replied@example.com",
    "nego-rejected-then-wrote@example.com",
    "contacted-reminded@example.com",
}


@pytest.fixture()
def queue(db_session_factory, monkeypatch):
    """One conversation per case the two buckets have to separate."""
    monkeypatch.setattr(messages_route, "SessionLocal", db_session_factory)
    with db_session_factory() as session:
        # New — the draft to review is the row, as before.
        conv = _ticket(session, "new-pending@example.com", "new")
        _inquiry(session, conv, days=0.2)
        _we_wrote(session, conv, days=0.1, status="pending_approval")
        conv = _ticket(session, "new-drafting@example.com", "new")
        _inquiry(session, conv, days=2)
        _we_wrote(session, conv, days=2, status="drafting")
        # A New draft nobody touched for 40 days — the reply window is not for New (옛 발송 대기에도 없었다).
        conv = _ticket(session, "new-old-draft@example.com", "new")
        _inquiry(session, conv, days=40)
        _we_wrote(session, conv, days=40, status="pending_approval")
        # The rep's personal mail from weeks ago landed on this contact's newest ticket (mailbox_sync) — the new
        # inquiry is still the one waiting for its first reply.
        conv = _ticket(session, "new-after-old-sales@example.com", "new")
        session.add(CustomerInteraction(contact_id=conv.contact_id, conversation_id=conv.id, channel="이메일",
                                        direction="outgoing", handler="rep@estsoft.com", summary="지난 메일",
                                        external_id="gmail:old-1", happened_at=_naive_utc(days=20)))
        _inquiry(session, conv, days=0.3)
        _we_wrote(session, conv, days=0.25, status="pending_approval")
        # New whose draft was rejected and the customer wrote again — the New screen draws neither the log nor
        # 「메일 발송」, so a row there would be a dead end. New is the draft to review.
        # Outside mail landing on a New ticket AFTER its draft does not take the draft away: a rep's personal mail
        # (attached to the contact's newest ticket, whatever it is about), or an answer sent from the HubSpot inbox
        # that left the stage at New — the old list showed both drafts, and approval re-checks the conversation.
        conv = _ticket(session, "new-mail-after-draft@example.com", "new")
        _inquiry(session, conv, days=1)
        _we_wrote(session, conv, days=0.9, status="pending_approval")
        session.add(CustomerInteraction(contact_id=conv.contact_id, conversation_id=conv.id, channel="이메일",
                                        direction="outgoing", handler="rep@estsoft.com", summary="다른 건 메일",
                                        external_id="gmail:later-1", happened_at=_naive_utc(days=0.5)))
        conv = _ticket(session, "new-answered-outside@example.com", "new")
        _inquiry(session, conv, days=2)
        _we_wrote(session, conv, days=1.9, status="pending_approval")
        session.add(CustomerInteraction(contact_id=conv.contact_id, conversation_id=conv.id, channel="이메일",
                                        direction="outgoing", handler="perso.ai@estsoft.com", summary="받은편지함 답",
                                        external_id="hubspot:conv:inbox-1", happened_at=_naive_utc(days=1.5)))
        _customer_replied(session, conv, days=1)
        conv = _ticket(session, "new-rejected@example.com", "new")
        _inquiry(session, conv, days=3)
        _rejected(session, _we_wrote(session, conv, days=2.5, status="pending_approval"), days=2)
        _customer_replied(session, conv, days=1)
        conv = _ticket(session, "new-no-draft@example.com", "new")  # e.g. an adopted ticket whose thread came in
        _inquiry(session, conv, days=1)
        _customer_replied(session, conv, days=0.5)
        # Negotiating: they answered our email, our follow-up failed to send — still waiting on us.
        conv = _ticket(session, "nego-replied@example.com", "negotiation")
        _inquiry(session, conv, days=14)
        _we_wrote(session, conv, days=12)
        _customer_replied(session, conv, days=9)
        _we_wrote(session, conv, days=8, status="send_failed", variant="manual")
        # Negotiating, but we answered their last message — nothing waits.
        conv = _ticket(session, "nego-answered@example.com", "negotiation")
        _inquiry(session, conv, days=6)
        _we_wrote(session, conv, days=5)
        _customer_replied(session, conv, days=3)
        _we_wrote(session, conv, days=1)
        # Closed Won customer wrote back twice and nobody has drafted yet — the ticket is the row.
        conv = _ticket(session, "won-replied@example.com", "won")
        _inquiry(session, conv, days=25)
        _we_wrote(session, conv, days=20)
        _customer_replied(session, conv, days=4)
        _customer_replied(session, conv, days=2)
        # Our answer is on its way (approved) even though they wrote again after we drafted it.
        conv = _ticket(session, "nego-queued@example.com", "negotiation")
        _inquiry(session, conv, days=10)
        _we_wrote(session, conv, days=8)
        _we_wrote(session, conv, days=3, status="approved", variant="manual")
        _customer_replied(session, conv, days=2)
        # Delivery unknown (복구 tab owns it) — approved after their reply, so it answered it.
        conv = _ticket(session, "nego-unknown@example.com", "negotiation")
        _inquiry(session, conv, days=10)
        _we_wrote(session, conv, days=8)
        _customer_replied(session, conv, days=3)
        _we_wrote(session, conv, days=2.5, status="delivery_unknown", variant="manual").approved_at = _naive_utc(days=1)
        # Rejected AFTER reading their follow-up — the draft row is older than that message, the decision is not.
        conv = _ticket(session, "lost-rejected@example.com", "closed_lost")
        _inquiry(session, conv, days=10)
        _we_wrote(session, conv, days=8)
        _customer_replied(session, conv, days=3)
        draft = _we_wrote(session, conv, days=2.9, status="pending_approval", variant="manual")
        _customer_replied(session, conv, days=2.5)
        _rejected(session, draft, days=2)
        # Rejected, then the customer wrote again — nobody decided on that one.
        conv = _ticket(session, "nego-rejected-then-wrote@example.com", "negotiation")
        _inquiry(session, conv, days=10)
        _we_wrote(session, conv, days=8)
        _customer_replied(session, conv, days=6)
        _rejected(session, _we_wrote(session, conv, days=5.5, status="pending_approval", variant="manual"), days=5)
        _customer_replied(session, conv, days=1)
        # Contacted: their reply (written between our email and reminder 1) came in late. Reminders are not our
        # words — not the baseline (the worker still moves last_outgoing_at for them) and never the row's draft.
        # (This is the moment before `advance_if_customer_replied` runs — or after it failed: moving the ticket
        # to Negotiating deletes the unsent reminder.)
        conv = _ticket(session, "contacted-reminded@example.com", "meeting_link_sent")
        _inquiry(session, conv, days=10)
        _we_wrote(session, conv, days=6)
        _we_wrote(session, conv, days=3, variant=REMINDER_VARIANTS[0])
        conv.last_outgoing_at = _naive_utc(days=3)
        _customer_replied(session, conv, days=4)
        _we_wrote(session, conv, days=1, status="send_failed", variant=REMINDER_VARIANTS[1])
        # A months-old reply nobody answered — the previous rep's era, outside the window.
        conv = _ticket(session, "old-lost@example.com", "closed_lost")
        _inquiry(session, conv, days=250)
        _we_wrote(session, conv, days=200)
        _customer_replied(session, conv, days=AWAITING_REPLY_DAYS + 90)
        # The same, but someone else's recent message hangs off the conversation — it is not this customer's.
        conv = _ticket(session, "nego-stale@example.com", "negotiation")
        _inquiry(session, conv, days=60)
        _we_wrote(session, conv, days=50)
        _customer_replied(session, conv, days=AWAITING_REPLY_DAYS + 10)
        other = Contact(normalized_email="other@example.com", email="other@example.com", full_name="O")
        session.add(other)
        session.flush()
        _customer_replied(session, conv, days=1, contact_id=other.id)
        # Negotiating with no sales email we can see (backfill) — which message is a reply is unknowable.
        conv = _ticket(session, "backfill-nego@example.com", "negotiation")
        _customer_replied(session, conv, days=5)
        # No HubSpot ticket (a workbook-only inquiry) — 「메일 발송」 has no way to answer it.
        conv = _ticket(session, "ticketless@example.com", "won")
        conv.hubspot_ticket_id = None
        _inquiry(session, conv, days=10)
        _we_wrote(session, conv, days=8)
        _customer_replied(session, conv, days=2)
        session.commit()
    return db_session_factory


def _emails(**kwargs) -> set[str]:
    return {row["email"] for row in _messages_list_context(**kwargs)["messages"]}


def _rows(**kwargs) -> dict[str, dict]:
    return {row["email"]: row for row in _messages_list_context(**kwargs)["messages"]}


def test_new_holds_the_draft_and_later_stages_hold_the_customers_reply(queue):
    """2026-10-08 운영자: 「답변 대기중인 문의에 원래 new 에 해당하는 것만 왔는데 / 그게 아니라 이제 상대에게
    답장이 왔으면 어떤 단계든 표시되도록 해줘」 — New 의 검토할 초안은 그대로, 협의 중 · 수주 · Contacted 고객의
    답장이 더해진다."""
    assert _emails(status="awaiting") == WAITING


def test_a_row_opens_its_draft_or_the_ticket(queue):
    """초안이 있으면 그 초안, 없으면 티켓 화면 — New 를 지난 그 화면에 「메일 발송」이 있다."""
    rows = _rows(status="awaiting")
    assert rows["new-pending@example.com"]["href"].startswith("/messages/")
    assert rows["new-pending@example.com"]["status"] == "pending_approval"
    assert rows["nego-replied@example.com"]["status"] == "send_failed"
    won = rows["won-replied@example.com"]
    assert (won["id"], won["status"], won["href"]) == (None, "received", f"/tickets/{won['conversation_id']}")
    sent = _rows(status="sent")
    assert all(row["href"] == f"/messages/{row['id']}" for row in sent.values())


def test_new_is_still_the_draft_to_review(queue):
    """New 는 옛 「발송 대기」 그대로다 — 오래된 초안도, 지난 메일이 붙은 티켓의 새 문의 초안도 선다. 초안 없는 New 행은
    없다: 그 화면은 초안을 읽고 보내는 화면이라 열어 봐야 할 일이 없다(거절 뒤에 고객이 또 써도)."""
    waiting = _emails(status="awaiting")
    assert {
        "new-old-draft@example.com", "new-after-old-sales@example.com",
        "new-mail-after-draft@example.com", "new-answered-outside@example.com",
    } <= waiting
    assert "new-rejected@example.com" not in waiting
    assert "new-no-draft@example.com" not in waiting


def test_a_reply_on_its_way_or_already_decided_stays_out(queue):
    """나가는 중이면 안 선다. 사람이 정한 초안은 **정한 뒤로** 고객이 새로 쓴 말이 있을 때만 선다 — 검토 화면이
    「거절하면 … 빠집니다」라고 적는다. 정한 때는 초안 행의 시각이 아니라 거절 · 승인한 때다."""
    waiting = _rows(status="awaiting")
    assert "nego-queued@example.com" not in waiting
    assert "nego-unknown@example.com" not in waiting
    assert "lost-rejected@example.com" not in waiting
    assert waiting["nego-rejected-then-wrote@example.com"]["status"] == "received"
    assert "approved" not in LIST_STATUS_BUCKETS["awaiting"]
    assert "delivery_unknown" not in LIST_STATUS_BUCKETS["awaiting"] + LIST_STATUS_BUCKETS["sent"]


def test_reminders_are_not_our_reply(queue):
    """리마인더는 기준선도(발송 워커가 `last_outgoing_at` 을 밀어도) 행의 초안도 아니다 — 실패한 리마인더를 행으로
    걸면 막 답장한 고객에게 재촉 메일을 다시 보내라는 셈이다."""
    row = _rows(status="awaiting")["contacted-reminded@example.com"]
    assert (row["status"], row["href"]) == ("received", f"/tickets/{row['conversation_id']}")


def test_a_reply_row_opens_a_ticket_screen_that_can_answer_it(db_session_factory, monkeypatch):
    """「고객 회신 도착」은 티켓 화면을 연다 — 그 화면이 우리 마지막 메일보다 먼저 쓰다 만 초안을 「현재」 글로 세우면
    편집기가 그 낡은 글을 열고 「메일 발송」이 숨는다(10-06 운영 사본에 그런 대화 2건). 「메일 발송」이 그 초안을 밀린
    것으로 닫는 자(`followup_sequence.open_draft`, 2026-09-28)와 같은 자로 거른다."""
    monkeypatch.setattr(messages_route, "SessionLocal", db_session_factory)
    with db_session_factory() as session:
        conv = _ticket(session, "abandoned@example.com", "negotiation")
        _inquiry(session, conv, days=30)
        stale = _we_wrote(session, conv, days=20, status="pending_approval", variant="manual")
        session.add(CustomerInteraction(contact_id=conv.contact_id, conversation_id=conv.id, channel="이메일",
                                        direction="outgoing", handler="perso.ai@estsoft.com", summary="화면에서 보낸 답",
                                        external_id="hubspot:conv:out-1", happened_at=_naive_utc(days=10)))
        _customer_replied(session, conv, days=2)
        session.commit()
        conv_id, stale_id = conv.id, stale.id

    row = _rows(status="awaiting")["abandoned@example.com"]
    assert (row["status"], row["href"]) == ("received", f"/tickets/{conv_id}")
    current = messages_route._message_detail_context(conversation_id=conv_id)["msg"]
    assert current is not None and current["id"] != stale_id
    # 그 초안을 그대로 열면(목록의 초안 링크 · 직접 주소) 그 글이 현재다 — 버리는 것은 「메일 발송」이 한다.
    assert messages_route._message_detail_context(stale_id)["msg"]["id"] == stale_id


def test_old_replies_backfill_and_ticketless_conversations_stay_out(queue):
    """고객의 말이 기간(`AWAITING_REPLY_DAYS`) 밖이면 안 선다 — 2026-10-06 운영 사본에서 기간 없이 재면 36건이 섰고 전부
    81~335일 전 Concluded · Lost 였다. 다른 사람의 줄은 이 고객의 말이 아니다. 우리 영업 이메일이 안 보이는 대화(백필)는
    어느 말이 답장인지 모르고, 허브스팟 티켓이 없는 대화는 「메일 발송」이 답할 길이 없다."""
    waiting = _emails(status="awaiting")
    for email in ("old-lost", "nego-stale", "backfill-nego", "ticketless"):
        assert f"{email}@example.com" not in waiting


def test_rejected_sits_with_completed_not_with_waiting(queue):
    """거절 is a finished outcome, so it belongs under 발송 완료."""
    assert "rejected" in LIST_STATUS_BUCKETS["sent"]
    assert "rejected" not in LIST_STATUS_BUCKETS["awaiting"]


def test_stage_chip_filters_the_bucket(queue):
    assert _emails(status="awaiting", stage="new") == {
        "new-pending@example.com",
        "new-drafting@example.com",
        "new-old-draft@example.com",
        "new-after-old-sales@example.com",
        "new-mail-after-draft@example.com",
        "new-answered-outside@example.com",
    }
    assert _emails(status="awaiting", stage="won") == {"won-replied@example.com"}
    sent = _messages_list_context(status="sent", stage="negotiation")["messages"]
    assert sent and {row["stage"] for row in sent} == {"negotiation"}


def test_both_buckets_offer_every_stage_they_can_hold(queue):
    """답변 대기 can hold any stage, so it offers every chip; 발송 완료 never has a New row."""
    awaiting = _messages_list_context(status="awaiting")["stage_chips"]
    sent = _messages_list_context(status="sent")["stage_chips"]
    assert [key for key, _ in awaiting] == [
        "", "new", "meeting_link_sent", "negotiation", "won", "closed_lost", "closed",
    ]
    assert [key for key, _ in sent] == [
        "",
        "meeting_link_sent",
        "negotiation",
        "won",
        "closed_lost",
        "closed",
    ]
    # Labels and order are the board's, not a second hand-written list.
    assert [label for _, label in sent] == [
        "전체",
        "Contacted",
        "Negotiating",
        "Closed Won",
        "Closed Lost",
        "Concluded",
    ]


def test_stage_carried_over_from_the_other_bucket_falls_back_to_all(queue):
    """The bucket chips keep the current stage in their href, so 답변 대기(New) → 발송 완료
    arrives with stage=new — a combination that can never match. Show everything the
    bucket has instead of an empty table."""
    ctx = _messages_list_context(status="sent", stage="new")
    assert ctx["filter_stage"] == ""
    assert _emails(status="sent", stage="new") == _emails(status="sent")


def test_rows_carry_the_inquiry_subject_not_our_reply_subject(queue):
    rows = _messages_list_context(status="awaiting")["messages"]
    assert rows and all(row["subject"].startswith("문의 ") for row in rows)
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
            conv = _ticket(session, email, "new")
            conv.inquiry_subject = inquiry_subject
            _inquiry(session, conv, days=1)
            session.add(Message(conversation_id=conv.id, direction="outgoing",
                                subject="RE: 더빙 단가 문의", body="draft", status="pending_approval"))
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
    assert created == sorted(created) and len(created) == len(WAITING)
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


def test_waiting_is_measured_from_when_the_customer_started_waiting(queue):
    """The dot's colour is decided in the screen from ``waiting_since`` (QueueTable.tsx); what the
    server owes it is that value. New: when the inquiry came in. Later stages: the customer's FIRST
    message after our last email — not our draft, and not their latest message: someone who wrote
    twice has waited since the first."""
    ctx = _messages_list_context(status="awaiting")
    waited = {
        row["email"]: (ctx["now"] - row["waiting_since"]).days for row in ctx["messages"]
    }
    assert waited["new-pending@example.com"] == 0     # → green
    assert waited["new-drafting@example.com"] == 2    # → orange
    assert waited["new-old-draft@example.com"] == 40
    assert waited["nego-replied@example.com"] == 9    # → red, from their reply — not our email 12 days ago
    assert waited["won-replied@example.com"] == 4     # wrote again 2 days ago; has waited since the first


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


def test_a_draft_alone_does_not_put_a_ticket_in_the_queue(db_session_factory, monkeypatch):
    """**New 를 지나면 기준은 고객의 말이다, 우리 초안이 아니다** (2026-10-08). 운영자가 협상 중인 티켓에 「메일 발송」으로
    재촉 메일을 쓰고 있어도 고객이 우리 마지막 메일 뒤에 아무 말이 없으면 답변 대기가 아니다 — 쓰다 만 초안은 그 티켓
    화면에서 이어 쓴다. 고객이 답하면 그때 서고, 그 행이 그 초안을 연다."""
    monkeypatch.setattr(messages_route, "SessionLocal", db_session_factory)
    with db_session_factory() as session:
        conv = _ticket(session, "nego@example.com", "negotiation", subject="협상 문의")
        _inquiry(session, conv, days=9)
        _we_wrote(session, conv, days=5)
        draft = _we_wrote(session, conv, days=1, status="pending_approval", variant="manual")
        session.commit()
        conv_id, draft_id = conv.id, draft.id

    assert _emails(status="awaiting") == set()

    with db_session_factory() as session:
        _customer_replied(session, session.get(Conversation, conv_id), days=0.5)
        session.commit()
    row = _rows(status="awaiting")["nego@example.com"]
    assert (row["id"], row["href"]) == (draft_id, f"/messages/{draft_id}")


def test_the_dashboard_number_and_the_list_below_it_agree(queue, monkeypatch):
    """**「답변 대기」 숫자와 그 아래 목록은 같은 것을 세야 합니다** (2026-08-05 의 6 대 1 이후 그대로다).

    한 화면에 나란히 서 있어서, 어긋나면 운영자가 없는 일감을 찾아 나섭니다. 둘이 갈린 길은 카운터와 목록이
    각자의 쿼리를 들고 있던 것 — 이제 숫자는 목록의 총계(`_messages_list_context(...)["total"]`) 그대로입니다.
    """
    from src.api.routes import customer_ops
    from src.api.routes import dashboard as dashboard_route

    for module in (dashboard_route, customer_ops):
        monkeypatch.setattr(module, "SessionLocal", queue)
    listed = _emails(status="awaiting")
    counted = dashboard_route._dashboard_context()["awaiting_total"]

    assert len(listed) == len(WAITING)
    assert counted == len(listed), "카운터와 목록이 같은 것을 세야 합니다"
