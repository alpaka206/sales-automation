"""승인한 글자가 그대로 나갑니다 — 사고 두 건의 모양을 지어낸 글로 고정합니다 (2026-10-06).

- **MSG#110** — 운영자가 줄을 맞춰 쓴 항목(들여 쓴 이어지는 줄)을 승인했습니다. 발송이 그 사이에 빈
  줄을 넣어 항목 하나가 문단 셋으로 갈라졌고, 「per month」를 금액으로 읽어 두 줄을 지웠습니다.
- **MSG#118** — 첫 회신에 콘솔 문서가 허용한 공개 가격을 적었습니다. 발송이 그 줄들을 지워 끊긴
  문장이 나갔습니다.

둘 다 사람이 승인한 **뒤**에 일어났습니다. 그래서 여기서는 미리보기 · 저장 · 승인 · 실제 ``send()`` 가
같은 글자를 들고 있는지를 바이트 단위로 봅니다. 그리고 첫 회신의 금액도, 콘솔 문서가 비공개로 적은
숫자도 **확인 단계 없이** 승인되고 나갑니다 — 운영자: 「답변 작성에 대한 경고문 이런건 필요없어」.

본문은 전부 지어낸 것입니다(사고 메일의 모양만 따랐습니다).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.textwash import text_wash
from src.db.base import Base
from src.db.models import Approval, Contact, Conversation, Event, Message, PolicySource
from src.integrations.email_html import to_html_email

# 줄을 맞춰 쓴 항목 — 들여 쓴 이어지는 줄, 범위를 묻는 「per month」.
WRAPPED = """Hi Dana,

Thanks for the details. A couple of quick answers:

- Flat pricing. We do not sell a flat bundle, so rather than
  guess a package we match the credits to what you plan to run.

- The total. I can share it once I know the scope below, since
  it comes from your numbers and not from a fixed table.

What sets the quote:

1. Footage. About how many hours of video do you plan to dub per month,
   counting every series?

2. Voices. Do you need one voice per show, or one per speaker?

Best,
Alex"""

# 첫 회신의 금액 — 그리고 콘솔 문서가 「❌ 비공개」로 적은 숫자($0.12, 아래 `_CONSOLE_DOC`).
PRICED = """Hi Sam,

Thanks for reaching out. Here is the route that fits a one-off project.

The Starter plan is $19 a month and includes 30 minutes. A top-up
pack adds 60 minutes for $25, so 90 minutes comes to $44 in total.
Large volumes can go below $0.12 per minute on an annual plan.

Does any one episode run past 45 minutes?

Best,
Alex"""

TEXTS = {"wrapped": WRAPPED, "priced": PRICED}
_CONSOLE_DOC = "## 내부 단가\n- 원가 ❌ 비공개: $0.12 per minute\n"


@pytest.mark.parametrize("name", TEXTS)
def test_the_wash_leaves_the_operators_layout_alone(name):
    assert text_wash(TEXTS[name]) == TEXTS[name]


def test_a_hard_wrapped_bullet_renders_as_one_list_item():
    html = to_html_email(WRAPPED)
    assert html.count("<li") == 2
    # 이어지는 줄은 그 항목 안에 있습니다 — <ul> 둘 사이의 <p> 가 아니라.
    assert "so rather than<br>guess a package" in html
    assert "<p style=\"margin:0 0 14px;\">  guess" not in html


@pytest.fixture()
def shared_db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    from src.common.config import settings

    # 발송 버튼은 승인만 하고, 실제 발송은 아래에서 `send()` 를 직접 부릅니다(워커와 같은 함수).
    monkeypatch.setattr(settings, "SEND_WORKER_ENABLED", True)
    with (
        patch("src.api.routes.messages.SessionLocal", factory),
        patch("src.agents.approval.SessionLocal", factory),
        patch("src.db.session.SessionLocal", factory),
    ):
        yield factory


def _pending_first_reply(factory) -> int:
    with factory() as session:
        session.add(PolicySource(label="단가", title="단가", doc_key="rates", mode="rules", body=_CONSOLE_DOC))
        contact = Contact(normalized_email="buyer@example.com", email="buyer@example.com", full_name="Buyer")
        session.add(contact)
        session.flush()
        conv = Conversation(contact_id=contact.id, stage="new", hubspot_ticket_id="ticket-1")
        session.add(conv)
        session.flush()
        session.add(Message(conversation_id=conv.id, direction="inbound", body="How much for 90 minutes?",
                            status="received"))
        msg = Message(conversation_id=conv.id, direction="outgoing", to_address="buyer@example.com",
                      subject="RE: video dubbing", body="(draft)", language="en", target_language="en",
                      status="pending_approval")
        session.add(msg)
        session.commit()
        return msg.id


@pytest.mark.parametrize("name", TEXTS)
async def test_preview_stored_approved_and_sent_are_the_same_bytes(shared_db, monkeypatch, name):
    from src.api.main import app
    from src.integrations.hubspot import ConversationReplyContext
    from src.integrations.senders import send
    from src.llm import organizer

    text = TEXTS[name]
    message_id = _pending_first_reply(shared_db)
    client = TestClient(app)
    # 콘솔 문서가 그 숫자를 정말 비공개로 적었습니다 — 그래도 승인·발송은 그것을 묻지 않습니다.
    assert "$0.12" in organizer.deterministic_terms(_CONSOLE_DOC)

    # ① 미리보기 — 저장될 글을 그립니다.
    preview = client.post("/messages/preview", data={"body": text})
    assert preview.status_code == 200 and preview.text == to_html_email(text)

    # ② 저장 — 다듬기를 지나도 같은 글자입니다.
    assert client.post(f"/messages/{message_id}/edit", data={"body": text, "subject": ""}).status_code == 200
    with shared_db() as session:
        assert session.get(Message, message_id).body == text

    # ③ 승인(검토 완료 · 발송) — 한 번 누르면 끝입니다. 확인할 경고도, 남길 「확인함」도 없습니다.
    approved = client.post(f"/messages/{message_id}/send", data={"body": text, "subject": "RE: video dubbing"})
    assert approved.status_code == 200, approved.text
    with shared_db() as session:
        row = session.get(Message, message_id)
        assert row.status == "approved" and row.body == text
        assert session.query(Approval).filter_by(message_id=message_id).one().diff == text
        (event,) = session.query(Event).filter_by(kind="reply_approval").all()
        assert set(event.payload) == {"message_id", "binding"}
        message = row
        _ = message.conversation  # 떼어 내기 전에 읽어 둡니다(워커가 든 행처럼).
        session.expunge_all()

    # ④ 실제 발송 — 허브스팟에 가는 글자가 승인한 글자입니다.
    hubspot = MagicMock()
    hubspot.find_default_reply_context = AsyncMock(
        return_value=ConversationReplyContext("thread-1", "1002", "account-1"))
    hubspot.send_conversation_message = AsyncMock(return_value="hs-1")
    hubspot.close = AsyncMock()
    monkeypatch.setattr("src.integrations.hubspot.HubSpotClient", lambda: hubspot)
    await send(message)

    sent = hubspot.send_conversation_message.await_args.kwargs
    assert sent["text"] == text
    assert message.body == text
    # 줄마다 HTML 에 있습니다 — 빠진 줄이 없다(불릿 기호는 <li> 가 됩니다).
    for line in text.splitlines():
        if line.strip():
            assert line.strip().removeprefix("- ") in sent["rich_text"], line
    if name == "priced":
        assert "The Starter plan is $19 a month and includes 30 minutes. A top-up<br>pack" in sent["rich_text"]
