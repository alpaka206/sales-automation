"""The UI shows actual records once, scoped to this customer, without drafts."""
from datetime import datetime, timedelta

from src.db.history_view import ticket_records
from src.db.models import Contact, Conversation, CustomerInteraction, Message


def test_history_is_scoped_deduplicated_and_includes_real_records(db_session):
    session = db_session
    a = Contact(normalized_email="a@example.test", full_name="A")
    b = Contact(normalized_email="b@example.test", full_name="B")
    session.add_all([a, b])
    session.flush()
    one, two = Conversation(contact_id=a.id), Conversation(contact_id=b.id)
    session.add_all([one, two])
    session.flush()
    at = datetime(2026, 9, 21)
    session.add_all([
        Message(conversation_id=one.id, direction="inbound", body="문의 원문", status="received",
                created_at=at),
        Message(conversation_id=one.id, direction="outgoing", body="실제 회신", status="sent",
                hubspot_message_id="sent-1", sent_at=at + timedelta(hours=1)),
        Message(conversation_id=one.id, direction="outgoing", body="미발송 초안", status="pending_approval"),
        # 안전 모드에서 돌린 회신도 이력입니다 — `DELIVERED_STATUSES` 의 docstring 이 그 이유.
        Message(conversation_id=one.id, direction="outgoing", body="테스트 발송", status="test_sent",
                sent_at=at + timedelta(minutes=30)),
        # 갔는지 모르는 회신은 더더욱 보여야 합니다 — 고객에게 닿았을 수 있습니다.
        Message(conversation_id=one.id, direction="outgoing", body="확인 안 된 회신",
                status="delivery_unknown", sent_at=at + timedelta(minutes=40)),
        # 사람이 되돌린 초안은 고객이 못 본 글입니다.
        Message(conversation_id=one.id, direction="outgoing", body="거절된 초안", status="rejected"),
        CustomerInteraction(contact_id=a.id, conversation_id=one.id, channel="email",
                            direction="outgoing", summary="중복된 회신",
                            external_id="hubspot:conv:sent-1", happened_at=at),
        CustomerInteraction(contact_id=a.id, conversation_id=one.id, channel="phone",
                            summary="실제 전화 기록", happened_at=at + timedelta(hours=2)),
        CustomerInteraction(contact_id=b.id, conversation_id=one.id, channel="email",
                            summary="잘못 연결된 다른 고객 정보", happened_at=at),
        Message(conversation_id=two.id, direction="inbound", body="B 고객 원문", status="received"),
    ])
    session.commit()
    records = ticket_records(session, a.id, [one.id, two.id])
    assert set(records) == {one.id}
    assert [r["summary"] for r in records[one.id]] == [
        "실제 전화 기록", "실제 회신", "확인 안 된 회신", "테스트 발송", "문의 원문",
    ]
    assert records[one.id][0]["editable"] is True
    assert records[one.id][1]["editable"] is False


def test_the_reminder_completion_line_stays_next_to_the_reminder_mail(db_session):
    """「1차 리마인더 완료」는 운영자 지시로 소통 히스토리에 남기는 줄입니다 (2026-09-21).

    리마인더 메일 행이 보인다고 그 줄을 「중복」으로 거르면, 티켓 화면에는 있는 줄이 고객
    상세에서만 사라집니다 — 같은 행을 두고 두 화면이 다른 말을 합니다.
    """
    from src.agents.followup_sequence import REMINDER_NOTE_PREFIX, done_label

    session = db_session
    contact = Contact(normalized_email="r@example.test", full_name="R")
    session.add(contact)
    session.flush()
    conv = Conversation(contact_id=contact.id)
    session.add(conv)
    session.flush()
    at = datetime(2026, 9, 21, 9)
    mail = Message(conversation_id=conv.id, direction="outgoing", body="리마인더 본문",
                   status="sent", prompt_variant="followup_reminder_1", sent_at=at)
    session.add(mail)
    session.flush()
    session.add(CustomerInteraction(
        contact_id=contact.id, conversation_id=conv.id, channel="email", direction="outgoing",
        summary=done_label("followup_reminder_1"),
        external_id=f"{REMINDER_NOTE_PREFIX}{mail.id}", happened_at=at,
    ))
    session.commit()
    summaries = [r["summary"] for r in ticket_records(session, contact.id, [conv.id])[conv.id]]
    assert "리마인더 본문" in summaries
    assert done_label("followup_reminder_1") in summaries
