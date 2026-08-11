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
    """Belt and braces, and it is what kept the bug from reaching a customer. The guard
    translates when the metadata says target-language but the script says Korean."""
    def returns_without_translating(body: str, target: str) -> bool:
        # src/integrations/senders: `if not (target != "ko" and is_mostly_korean(body))`
        return not (target != "ko" and is_mostly_korean(body))

    assert returns_without_translating(KOREAN, "en") is False   # stale flag -> translate
    assert returns_without_translating(ENGLISH, "en") is True   # genuinely English -> send
    assert returns_without_translating(KOREAN, "ko") is True    # Korean target -> send


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
