"""번역하기, and what 처리 경과 is for.

The reply workflow is: the operator reviews a KOREAN draft, presses 번역하기, reads the
result in the customer's language, then sends. The button tested for the opposite of what
it meant, so a Korean draft for an English inquiry reported "번역할 내용이 없습니다" and
the operator never saw the mail they were about to send.
"""

from __future__ import annotations

import pytest

from src.llm.translate import is_mostly_korean, needs_korean

KOREAN = "안녕하세요. 문의 주셔서 감사합니다. 요청하신 내용 확인했습니다."
ENGLISH = "Hello, thank you for your inquiry. We have reviewed your request."
MIXED = "Thank you for reaching out. 담당자 드림"   # English body, Korean signature


def _the_button_translates(body: str, target: str) -> bool:
    """The condition src/api/routes/messages.py:message_translate decides on."""
    return bool(target) and target != "ko" and is_mostly_korean(body)


@pytest.mark.parametrize(
    ("body", "target", "expected"),
    [
        (KOREAN, "en", True),      # the whole point of the button
        (KOREAN, "ja", True),
        (KOREAN, "ko", False),     # a Korean inquiry gets the Korean draft as written
        (ENGLISH, "en", False),    # already in the send language, nothing to do
        (MIXED, "en", False),      # mostly English already; the signature is not the body
        ("", "en", False),
    ],
)
def test_the_button_translates_exactly_when_there_is_korean_to_translate(body, target, expected):
    assert _the_button_translates(body, target) is expected


def test_the_two_language_checks_are_not_each_others_negation():
    """Writing one as `not` the other is how the inversion got in. Both are False for
    text with no letters, because a URL is not Korean AND not English."""
    assert needs_korean(ENGLISH) and not is_mostly_korean(ENGLISH)
    assert is_mostly_korean(KOREAN) and not needs_korean(KOREAN)
    for nothing in ("", "   ", "12345", "https://perso.ai/pricing"):
        assert not is_mostly_korean(nothing), nothing


def test_a_korean_body_is_never_stamped_with_the_send_language():
    """The other half of the same inversion: the no-op branch marked a Korean draft as
    being in the target language. The send guard then had to catch it — and if it had
    trusted the flag, the customer would have received Korean labelled English."""
    washed, target = KOREAN, "en"
    stamps_target = bool(target) and target != "ko" and not is_mostly_korean(washed)
    assert stamps_target is False


def test_the_send_guard_still_catches_a_korean_body_marked_as_sent_language():
    """The delivery chokepoint blocks stale metadata instead of translating unseen text."""
    from types import SimpleNamespace

    from src.integrations.senders import SendLanguageMismatch, enforce_send_language

    message = SimpleNamespace(
        id=1,
        body=KOREAN,
        language="en",
        target_language="en",
        prompt_variant=None,
    )
    with pytest.raises(SendLanguageMismatch):
        enforce_send_language(message)


# ----- 미리 해 두는 번역: 한국어가 아닌 **고객 문의** 하나뿐, 그리고 화면 밖에서 -----


def _seed(session_factory, *, direction: str, body: str, subject: str | None = None) -> int:
    from src.db.models import Contact, Conversation, Message

    with session_factory() as session:
        contact = Contact(normalized_email="buyer@corp.com", full_name="Buyer")
        session.add(contact)
        session.flush()
        conv = Conversation(contact_id=contact.id, stage="new")
        session.add(conv)
        session.flush()
        msg = Message(
            conversation_id=conv.id, direction=direction, channel="email",
            body=body, subject=subject, status="received",
        )
        session.add(msg)
        session.commit()
        return msg.id


@pytest.fixture()
def inbound_db(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from src.agents import inbound
    from src.db.base import Base

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(inbound, "SessionLocal", factory)
    return factory


def test_a_foreign_inquiry_is_translated_once_and_stored(inbound_db, monkeypatch):
    from src.agents.inbound import cache_korean_inquiries
    from src.db.models import Message

    calls = []
    monkeypatch.setattr(
        "src.llm.translate.to_korean",
        lambda text, **kw: calls.append(text) or "번역된 문의입니다.",
    )
    mid = _seed(inbound_db, direction="inbound", body=ENGLISH, subject="Quote please")

    assert cache_korean_inquiries() == 1
    with inbound_db() as session:
        assert session.get(Message, mid).body_ko == "번역된 문의입니다."
    assert len(calls) == 2   # 본문과 제목

    # 두 번째 순회는 이 행을 다시 집지 않습니다 — 안 그러면 폴러가 10분마다 모델을 부릅니다.
    calls.clear()
    assert cache_korean_inquiries() == 0
    assert calls == []


def test_a_korean_inquiry_is_stored_without_calling_the_model(inbound_db, monkeypatch):
    """이미 한국어인 문의는 옮길 것이 없습니다. 그래도 채워 둡니다 — 비워 두면 폴러가
    같은 행을 영원히 다시 집습니다."""
    from src.agents.inbound import cache_korean_inquiries
    from src.db.models import Message

    monkeypatch.setattr(
        "src.llm.translate.to_korean",
        lambda *a, **kw: pytest.fail("한국어 문의를 번역하려 했습니다"),
    )
    mid = _seed(inbound_db, direction="inbound", body=KOREAN)

    assert cache_korean_inquiries() == 1
    with inbound_db() as session:
        assert session.get(Message, mid).body_ko == KOREAN


def test_our_replies_are_never_pre_translated(inbound_db, monkeypatch):
    """초안은 한국어로 쓰이고, 보낼 언어로 바꾸는 것은 운영자가 `번역하기` 를 누를 때뿐입니다.
    미리 할 이유도 없고 — 운영자가 고친 본문을 번역해야 하므로 — 할 수도 없습니다."""
    from src.agents.inbound import cache_korean_inquiries
    from src.db.models import Message

    monkeypatch.setattr(
        "src.llm.translate.to_korean",
        lambda *a, **kw: pytest.fail("회신 초안을 미리 번역하려 했습니다"),
    )
    mid = _seed(inbound_db, direction="outgoing", body=ENGLISH)

    assert cache_korean_inquiries() == 0
    with inbound_db() as session:
        assert session.get(Message, mid).body_ko is None


def test_opening_a_ticket_never_reaches_the_model():
    """**화면을 여는 길에는 번역이 없습니다.**

    티켓을 열 때 번역하던 코드가 있었고, 그래서 그 티켓을 처음 여는 사람이 말풍선마다
    모델을 기다렸다가 화면을 봤습니다(영어 세 줄이면 여섯 번). 답을 쓰려고 여는 창입니다.
    """
    import pathlib

    # `to_korean` = 한국어로 옮기기 = 미리 해 두는 그 번역. 접수 경로에만 있어야 합니다.
    # `translate_to` 는 다릅니다 — 운영자가 `번역하기` 를 누를 때 그 자리에서 도는 것이라
    # messages.py 에 그대로 있는 게 맞습니다.
    for path in ("src/api/routes/messages.py", "src/api/routes/ui_api.py"):
        source = pathlib.Path(path).read_text(encoding="utf-8")
        assert "to_korean" not in source, path


def test_a_model_failure_leaves_it_retryable(inbound_db, monkeypatch):
    """모델이 안 되면 **비워 둡니다.**

    `to_korean` 은 실패해도 예외를 던지지 않고 빈 문자열을 돌려줍니다. 그 자리에 원문을
    넣으면 영어가 「한국어 번역」이라는 이름을 달고 행에 굳고, 폴러는 그 행을 다시 집지
    않습니다 — 되돌릴 방법이 없습니다. 검토 화면의 `번역하기` 는 회신 초안을 보낼 언어로
    바꾸는 버튼이라 고객 문의에는 닿지 않습니다.
    """
    from src.agents.inbound import cache_korean_inquiries
    from src.db.models import Message

    monkeypatch.setattr("src.llm.translate.to_korean", lambda *a, **kw: "")   # 모델 장애
    mid = _seed(inbound_db, direction="inbound", body=ENGLISH)

    cache_korean_inquiries()
    with inbound_db() as session:
        assert session.get(Message, mid).body_ko is None      # 원문이 굳지 않았다

    # 모델이 돌아오면 다음 순회가 같은 행을 집어 채웁니다.
    monkeypatch.setattr("src.llm.translate.to_korean", lambda *a, **kw: "이제 번역됩니다.")
    assert cache_korean_inquiries() == 1
    with inbound_db() as session:
        assert session.get(Message, mid).body_ko == "이제 번역됩니다."


def test_choosing_no_signature_and_sending_straight_away_removes_it(inbound_db, monkeypatch):
    """「서명 없음」을 고르고 곧장 `발송` 을 누르면 서명이 붙어 나갔습니다.

    서명은 None 이 곧 「서명 없음」인데 `approve()` 의 기본값도 None 이라, "안 넘겼다" 와
    "없음으로 정했다" 가 같은 값이었습니다. 그래서 초안이 만들어질 때 달린 기본 서명이
    그대로 남았습니다. 같은 폼의 `저장`·`번역하기` 는 직접 대입이라 지워졌고 — 한 번
    경유하면 지워지고 곧장 발송하면 안 지워지는, 눈에 안 보이는 차이였습니다.
    """
    from src.agents import approval
    from src.db.models import Message

    monkeypatch.setattr(approval, "SessionLocal", inbound_db)
    mid = _seed(inbound_db, direction="outgoing", body="본문")
    with inbound_db() as session:
        msg = session.get(Message, mid)
        msg.status = "pending_approval"
        msg.signature_key = "signature_hyeram"      # 초안이 달고 태어난 기본 서명
        session.commit()

    approval.approve(mid, approver="test", signature_key=None)   # 「서명 없음」
    with inbound_db() as session:
        assert session.get(Message, mid).signature_key is None

    # 안 넘기면 그대로 둡니다 — 서명을 다루지 않는 호출자가 지우면 안 됩니다.
    with inbound_db() as session:
        msg = session.get(Message, mid)
        msg.status = "pending_approval"
        msg.signature_key = "signature_hyeram"
        session.commit()
    approval.approve(mid, approver="test")
    with inbound_db() as session:
        assert session.get(Message, mid).signature_key == "signature_hyeram"


# ---------------------------------------------------------------------------
# 주소는 모델에게 넘기지 않습니다.
# ---------------------------------------------------------------------------

BOOKING = (
    "https://calendar.google.com/calendar/u/0/appointments/schedules/"
    "AcZssZ3woViQ906eyzcO97gG4oZPCyESiCL7x_WyBERhh3-LZqZSpl-ZPAhONZtZWyQgIN7FzEtqrzwi"
)


class _FakeLLM:
    """모델이 받은 것을 기록하고, 시키는 대로 돌려줍니다."""

    def __init__(self, reply):
        self.reply = reply
        self.seen = ""

    def complete(self, _name, variables, **_kw):
        self.seen = variables["text"]
        return self.reply(self.seen) if callable(self.reply) else self.reply


def test_the_booking_url_never_reaches_the_translator():
    """초안 단계에서 모델에게서 지킨 그 주소가, 발송 직전 번역에서 다시 노출됐습니다.

    `apply_editable_tokens` 가 `{{MEETING_LINK}}` 를 진짜 값으로 바꾸는 것이 번역보다
    **앞**입니다. 그래서 영문 고객에게 나갈 때 `enforce_send_language` 가 120자짜리 base64
    주소를 통째로 모델에 넣고 있었습니다 — 토큰을 만든 이유가 바로 그것을 막는 것인데.
    """
    from src.llm.translate import translate_to

    body = f"미팅은 여기서 잡으실 수 있습니다: [Calendly]({BOOKING})"
    llm = _FakeLLM(lambda seen: seen.replace("미팅은 여기서 잡으실 수 있습니다", "Book a meeting here"))

    out = translate_to(body, "en", llm=llm)

    assert BOOKING not in llm.seen, "주소가 모델에게 갔습니다"
    assert "%%0%%" in llm.seen
    # 그리고 돌아온 본문에는 원래 주소가 글자 하나 안 틀리고 들어 있습니다.
    assert out == f"Book a meeting here: [Calendly]({BOOKING})"


def test_the_closing_paren_of_a_markdown_link_is_not_part_of_the_url():
    """`[Calendly](주소))` 로 나갔습니다 — 괄호가 하나 더 붙어서.

    지키는 정규식이 `)` 까지 주소로 집었습니다. 그러면 모델이 보는 것은 짝이 안 맞는
    `[Calendly](%%0%%` 이고, 모델은 그 괄호를 닫아 줍니다. 되돌릴 때 원래 `)` 가 다시
    붙으니 `))` 입니다. 발송 직전 `canonicalize_contact_links` 가 그 줄을 다시 만들어
    주는 덕에 고객에게는 안 갔지만, 검토 화면에는 그대로 보였습니다.
    """
    from src.llm.translate import translate_to

    body = f"미팅은 [Calendly]({BOOKING}) 에서 잡으실 수 있습니다"
    llm = _FakeLLM(lambda seen: seen.replace("미팅은 ", "Book at ").replace(" 에서 잡으실 수 있습니다", ""))

    out = translate_to(body, "en", llm=llm)

    # 모델이 **짝이 맞는** 괄호를 봐야 스스로 하나 더 닫지 않습니다.
    assert "[Calendly](%%0%%)" in llm.seen
    assert out == f"Book at [Calendly]({BOOKING})"


def test_a_translation_that_loses_the_link_is_a_failed_translation():
    """자리표시자가 안 돌아오면 링크가 없어진 것입니다. 그대로 내보내면 고객은 예약 링크가
    빠진 메일을 받고, 사람이 발송을 누른 **뒤**에 도는 단계라 검토에도 안 걸립니다.
    언어가 틀린 메일은 눈에 띄어 고쳐지지만, 링크가 빠진 메일은 안 띕니다."""
    from src.llm.translate import translate_to

    body = f"예약: [Calendly]({BOOKING})"
    swallowed = _FakeLLM("Booking: Calendly")          # 자리표시자를 삼킨 모델

    assert translate_to(body, "en", llm=swallowed) == ""


def test_the_tokens_are_protected_too():
    """`{{SENDER_NAME}}` 이 아직 안 바뀐 채로 번역을 지나는 경로도 있습니다 —
    초안의 `ensure_language` 는 치환보다 앞입니다."""
    from src.llm.translate import _protect, _restore

    masked, held = _protect("담당 {{SENDER_NAME}} 드림 — {{MEETING_LINK}}")
    assert "{{" not in masked
    assert held == ["{{SENDER_NAME}}", "{{MEETING_LINK}}"]
    # 모델이 자리표시자 옆에 공백을 넣는 정도는 흔해서 느슨하게 되돌립니다.
    assert _restore(masked.replace("%%1%%", "%% 1 %%"), held).endswith("{{MEETING_LINK}}")


# ---- 번역해도 한국어 초안은 남는다 -------------------------------------------------


@pytest.fixture()
def draft_db():
    """검토 화면이 읽고 쓰는 그 DB. 라우트가 여는 커넥션과 테스트가 여는 커넥션이 같아야
    하므로 StaticPool 입니다 — 기본 풀은 `:memory:` 를 커넥션마다 새로 만듭니다."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from src.db.base import Base

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _one_draft(factory, body: str):
    from src.db.models import Contact, Conversation, Message

    with factory() as session:
        contact = Contact(
            full_name="Ivan",
            email="ivan@example.com",
            normalized_email="ivan@example.com",
        )
        session.add(contact)
        session.flush()
        conv = Conversation(contact_id=contact.id, stage="new")
        session.add(conv)
        session.flush()
        msg = Message(
            conversation_id=conv.id,
            direction="outgoing",
            channel="email",
            subject="RE: Pricing",
            body=body,
            status="pending_approval",
            language="ko",
            target_language="en",
        )
        session.add(msg)
        session.commit()
        return msg.id


def test_translating_keeps_the_korean_the_operator_reviewed(draft_db):
    """번역은 한 번 누르면 되돌릴 수 없고, 그때 한국어 초안은 화면에서 사라졌습니다.

    운영자가 승인한 것은 그 한국어입니다. 번역이 뜻을 바꿨는지 확인하려면 두 벌이
    같이 있어야 하는데, 남는 것은 번역본 하나뿐이었습니다.
    """
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.db.models import Message

    message_id = _one_draft(draft_db, KOREAN)
    with (
        patch("src.api.routes.messages.SessionLocal", draft_db),
        patch("src.api.routes.messages.translate_to", return_value=ENGLISH),
        TestClient(app) as client,
    ):
        response = client.post(f"/messages/{message_id}/translate", data={"body": KOREAN})

    assert response.status_code == 200
    assert response.json()["body_ko"] == KOREAN
    with draft_db() as session:
        row = session.get(Message, message_id)
        assert row.body == ENGLISH
        assert row.body_ko == KOREAN      # 승인한 한국어는 행에 남습니다
        assert row.language == "en"


def test_re_translating_an_edited_draft_replaces_the_korean_copy(draft_db):
    """운영자가 본문을 도로 한국어로 고쳐 놓고 다시 번역하면, 옆 칸도 그 새 한국어입니다.
    지난 판본이 남아 있으면 대조할 것이 「승인한 글」이 아니게 됩니다."""
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.db.models import Message

    message_id = _one_draft(draft_db, ENGLISH)
    with draft_db() as session:
        row = session.get(Message, message_id)
        row.body_ko, row.language = "예전 한국어 초안입니다.", "en"
        session.commit()

    edited = KOREAN + " 조건을 하나 더 확인했습니다."
    with (
        patch("src.api.routes.messages.SessionLocal", draft_db),
        patch("src.api.routes.messages.translate_to", return_value=ENGLISH),
        TestClient(app) as client,
    ):
        response = client.post(f"/messages/{message_id}/translate", data={"body": edited})

    assert response.json()["body_ko"] == edited
    with draft_db() as session:
        assert session.get(Message, message_id).body_ko == edited
