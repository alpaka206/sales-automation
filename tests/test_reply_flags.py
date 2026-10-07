"""채우지 않은 자리(`agents.reply_flags`) — 회신 본문에 남은 검사는 이것 하나다 (2026-10-06).

운영자: 「답변 작성에 대한 경고문 이런건 필요없어」. 초안의 품질을 짚는 경고(첫 회신 금액 · 대외 비공개
용어 · 처리 완료 표현 같은 것)는 두지 않는다. 남은 것은 관문이다 — ``[[…]]`` · 치환 안 된 ``{{TOKEN}}`` ·
``[Name]`` 같은 빈칸은 승인이 거절하고, 코드가 승인하는 후속 리마인더는 발송이 막는다.

본문은 전부 지어낸 것이다.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.agents.reply_flags import quote_lines, unfilled_slots
from src.db.base import Base
from src.db.models import Contact, Conversation, Event, Message

# ---- 찾기 -----------------------------------------------------------------------


@pytest.mark.parametrize("line", [
    "Payment link: [[Stripe 결제 링크]]",
    "Book here: {{MEETING_LINK}}",
    "이스트소프트 {{SENDER_NAME}}입니다",
    "Hi [Name],",
    "[이름]",
    "안녕하세요 {고객 이름}님,",
    "you can try it here: [signup link]",
    "[pricing / signup link]",
    "Pilot fee: [PO 산출] flat",
    "That comes to $[P] for [N] minutes.",
    "[확인 필요: Stripe 결제 링크 — 생성 후 붙여넣기]",
    "[§2-1 기본 4문]",
])
def test_an_unfilled_slot_is_found_with_its_line(line):
    """콘솔 서식·예시(Template A~G)가 빈칸에 쓰는 모양들. 실제로 [LINK]·[확인 필요: …] 가 고객에게 나갔습니다."""
    assert unfilled_slots(f"Thanks.\n\n{line}\n\nBest,", language="en") == [line]


def test_a_slot_wrapped_across_lines_is_still_found():
    assert unfilled_slots("Pay here: [[Stripe\nlink]] today.") == ["Pay here: [[Stripe", "link]] today."]


@pytest.mark.parametrize("body,language", [
    ("Book here: [Calendly](https://calendar.example/abc123)", "en"),
    ("[미팅 링크](https://calendar.example/abc123)", "ko"),
    ("[안내] 결제 방법은 아래와 같습니다.", "ko"),
    ('Send {"lang": "en"} with the request.', "en"),
    # API 안내의 경로·코드 속 중괄호는 빈칸이 아닙니다.
    ("Call GET /v1/voices/{voice_id} or use `{project_id}` in ${name}.", "en"),
    ("- [ ] item and [Perso AI] and [Usage Limit Guide]", "en"),
    # 버튼 이름은 멀쩡한 글자입니다.
    ("Click [Share Link] in the editor.", "en"),
    ("Use the [Download] button.", "en"),
    # 언어를 모르면 대괄호 속 한국어를 빈칸으로 보지 않습니다.
    ("[안내] 결제 방법", None),
    ("", "en"),
])
def test_ordinary_brackets_are_not_slots(body, language):
    assert unfilled_slots(body, language=language) == []


def test_korean_in_brackets_is_a_writers_note_in_a_foreign_reply():
    """「[가격을 먼저 안내했다면]」 — 예시 문서가 쓰는 사람에게 남긴 지시가 영어 메일에 남은 것."""
    body = "[가격을 먼저 안내했다면] A quick note before we start."
    assert unfilled_slots(body, language="en") == [body]
    assert unfilled_slots(body, language="ko") == []


def test_the_quote_names_the_lines_and_counts_the_rest():
    assert quote_lines(["Hi [Name],"]) == "「Hi [Name],」"
    assert quote_lines(["a", "b", "c", "d"], limit=2) == "「a」, 「b」 외 2줄"
    assert quote_lines(["x" * 90]) == f"「{'x' * 80}…」"


# ---- 승인 -----------------------------------------------------------------------


@pytest.fixture()
def shared_db():
    """라우트·승인·발송 관문이 같은 DB 를 봅니다(StaticPool — 라우트는 다른 스레드에서 돕니다)."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with (
        patch("src.api.routes.messages.SessionLocal", factory),
        patch("src.agents.approval.SessionLocal", factory),
        patch("src.agents.send_worker.SessionLocal", factory),
        patch("src.db.session.SessionLocal", factory),
    ):
        yield factory


def _draft(factory, body, *, status="pending_approval", language="en", **fields) -> int:
    with factory() as session:
        contact = Contact(normalized_email="dana@example.com", email="dana@example.com", full_name="Dana")
        session.add(contact)
        session.flush()
        conv = Conversation(contact_id=contact.id, stage="new", hubspot_ticket_id="ticket-1")
        session.add(conv)
        session.flush()
        msg = Message(conversation_id=conv.id, direction="outgoing", to_address="dana@example.com",
                      subject="RE: Pricing", body=body, language=language, target_language=language,
                      status=status, **fields)
        session.add(msg)
        session.commit()
        return msg.id


def _approval_events(factory, message_id):
    with factory() as session:
        return [e.payload for e in session.query(Event).filter_by(kind="reply_approval").all()
                if e.payload.get("message_id") == message_id]


def test_approval_refuses_an_unfilled_slot_and_names_the_line(shared_db):
    from src.agents.approval import ApprovalError, approve

    message_id = _draft(shared_db, "Hi Dana,\n\nYour payment link: [[Stripe link]]\n\nBest,")
    with pytest.raises(ApprovalError, match=r"채우지 않은 자리가 있습니다 — 「Your payment link: \[\[Stripe link\]\]」"):
        approve(message_id, "op")
    with shared_db() as session:
        assert session.get(Message, message_id).status == "pending_approval"
    assert _approval_events(shared_db, message_id) == []


def test_an_edited_slot_is_refused_too(shared_db):
    """검토 화면의 발송은 칸의 글(`edited_body`)을 승인합니다 — 저장된 글이 아니라 그 글을 봅니다."""
    from src.agents.approval import ApprovalError, approve

    message_id = _draft(shared_db, "Hi Dana,\n\nThanks.")
    with pytest.raises(ApprovalError, match=r"「Hi \[Name\],」"):
        approve(message_id, "op", edited_body="Hi [Name],\n\nThanks.")


def test_the_approved_text_is_the_prepared_text(shared_db):
    """승인하는 글은 미리보기와 같은 다듬기를 거친 글이고, 그 글이 저장·바인딩됩니다."""
    from src.agents.approval import approve
    from src.agents.reply_safety import approval_binding

    links = {"meeting_link": "[Calendly](https://calendar.example/abc123)"}
    message_id = _draft(shared_db, "Hello.")
    with patch("src.db.email_templates.get_email_template", side_effect=links.get):
        approved = approve(message_id, "op", edited_body="Hello.   \n\n\n\nBook: {{MEETING_LINK}}  ")
    assert approved.body == "Hello.\n\nBook: [Calendly](https://calendar.example/abc123)"
    (payload,) = _approval_events(shared_db, message_id)
    assert payload == {"message_id": message_id, "binding": approval_binding(approved)}


def test_an_unset_link_token_cannot_be_approved(shared_db):
    """주소가 설정 안 된 토큰은 다듬기를 지나도 그대로 남고 — 그래서 승인이 거절합니다."""
    from src.agents.approval import ApprovalError, approve

    message_id = _draft(shared_db, "Hello.\n\nBook: {{MEETING_LINK}}")
    with patch("src.db.email_templates.get_email_template", return_value=None):
        with pytest.raises(ApprovalError, match=r"\{\{MEETING_LINK\}\}"):
            approve(message_id, "op")


# ---- 발송 — 코드가 승인한 리마인더 ------------------------------------------------


async def test_a_code_approved_reminder_with_a_slot_is_not_sent(shared_db, monkeypatch):
    """리마인더는 `approval.approve` 를 안 지납니다 — 시퀀스가 `approved` 로 세웁니다. 템플릿에 빈칸이
    남았으면 발송이 막고 사유를 행에 남깁니다. 허브스팟에는 아무것도 안 갑니다."""
    from src.agents import send_worker
    from src.agents.followup_sequence import REMINDER_VARIANTS

    body = "Hi [Name],\n\nFollowing up on my last note.\n\nBest,"
    message_id = _draft(shared_db, body, status="approved", prompt_variant=REMINDER_VARIANTS[0])
    hubspot = MagicMock()
    monkeypatch.setattr("src.integrations.hubspot.HubSpotClient", hubspot)
    assert send_worker._claim_id(message_id)

    assert await send_worker._send_one(message_id) is False

    hubspot.assert_not_called()
    with shared_db() as session:
        row = session.get(Message, message_id)
        assert row.status == "send_failed"
        assert row.body == body
        assert "채우지 않은 자리" in row.send_error and "「Hi [Name],」" in row.send_error


# ---- 검토 화면 --------------------------------------------------------------------


def test_the_review_screen_carries_no_draft_warnings(shared_db):
    """검토 화면에는 초안 품질 경고가 없습니다 — 지금 본문의 표시도, 초안 때 근거 검사의 판정도."""
    from src.api.routes.messages import _message_detail_context

    message_id = _draft(shared_db, "Hi Dana,\n\nThe Starter plan is $19 a month.\n\nI have refunded it.")
    with shared_db() as session:
        session.add(Event(kind="reply_context", payload={
            "message_id": message_id,
            "limited_evidence_checks": {"status": "FAIL", "issues": ["unsupported_duration"]},
        }))
        session.commit()
    context = _message_detail_context(message_id)
    assert "flags" not in context["msg"]
    assert "evidence" not in context["ticket"]


def test_the_preview_draws_what_will_be_stored_and_only_html(shared_db):
    """미리보기는 그 초안의 언어로 다듬은 글을 HTML 로만 그립니다 — 경고를 싣는 JSON 답은 없습니다.
    토큰이 섞이면 글자로는 언어를 못 가립니다(아래 본문은 영문자가 더 많습니다)."""
    links = {"meeting_link": "[Calendly](https://calendar.example/abc123)"}
    message_id = _draft(shared_db, "안녕하세요.", language="ko")
    raw = "안녕하세요.  \n\n\n\n편하신 시간: {{MEETING_LINK}}"
    with patch("src.db.email_templates.get_email_template", side_effect=links.get):
        response = TestClient(_app()).post("/messages/preview", headers={"Accept": "application/json"},
                                           data={"body": raw, "message_id": str(message_id)})
    assert response.headers["content-type"].startswith("text/html")
    assert "미팅 링크</a>" in response.text and "{{MEETING_LINK}}" not in response.text


def _app():
    from src.api.main import app

    return app
