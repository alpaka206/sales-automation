"""Unit tests for the deterministic rule guards (no LLM)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from src.common.pricing_guard import contains_price, strip_price_sentences
from src.common.subjects import (
    generic_inquiry_subject,
    reply_subject,
    strip_reply_prefixes,
)
from src.common.textwash import text_wash

# ---------- reply_subject (RE: with no duplicates) ----------


def test_reply_subject_adds_single_re():
    assert reply_subject("Pricing question") == "RE: Pricing question"


def test_reply_subject_does_not_stack():
    assert reply_subject("Re: hi") == "RE: hi"
    assert reply_subject("RE: RE: hi") == "RE: hi"
    assert reply_subject("re: Re: 답장: hi") == "RE: hi"


def test_reply_subject_korean_and_cjk_prefixes():
    assert reply_subject("회신: 문의드립니다") == "RE: 문의드립니다"
    assert reply_subject("回复: 你好") == "RE: 你好"


def test_reply_subject_counter_form():
    assert reply_subject("Re[2]: thread") == "RE: thread"


def test_reply_subject_empty_uses_localized_generic():
    assert reply_subject("", target_code="ja") == "RE: お問い合わせの件"
    assert reply_subject(None, target_code="en") == "RE: Your inquiry"
    # Unknown language falls back to English generic.
    assert reply_subject("", target_code="zz") == "RE: Your inquiry"


def test_strip_reply_prefixes():
    assert strip_reply_prefixes("Fwd: Re: hello") == "hello"
    assert strip_reply_prefixes("no prefix") == "no prefix"


def test_generic_inquiry_subject():
    assert generic_inquiry_subject("ko") == "문의 주신 건"
    assert generic_inquiry_subject("nope") == "Your inquiry"


# ---------- text_wash ----------


def test_text_wash_collapses_blank_lines_and_trims():
    assert text_wash("a.\n\n\n\nb.  ") == "a.\n\nb."


def test_text_wash_normalizes_bullets():
    assert text_wash("• item one\n· item two") == "- item one\n- item two"


def test_text_wash_separates_bullet_blocks_from_prose():
    raw = "안내드립니다.\n• 첫 번째 조건\n• 두 번째 조건\n회신해 주세요."
    assert text_wash(raw) == (
        "안내드립니다.\n\n- 첫 번째 조건\n- 두 번째 조건\n\n회신해 주세요."
    )


def test_text_wash_collapses_inner_spaces():
    assert text_wash("hello    world") == "hello world"


def test_text_wash_empty():
    assert text_wash("") == ""
    assert text_wash(None) == ""


# ---------- pricing guard ----------


def test_contains_price_positive():
    assert contains_price("It's $29/mo")
    assert contains_price("월 99,000원입니다")
    assert contains_price("Starter is 99k KRW")
    assert contains_price("USD 49 per month")


def test_contains_price_negative():
    assert not contains_price("We support 90-minute videos")
    assert not contains_price("About 200 mins of audio")
    assert not contains_price("Business Tier 2 Plan")
    assert not contains_price("Launched in 2026")


def test_strip_price_sentences_removes_price_lines():
    body = "플랜을 안내드립니다.\n- Creator 플랜 $29/월\n미팅에서 안내드릴게요."
    cleaned, removed = strip_price_sentences(body)
    assert "$29" not in cleaned
    assert "미팅" in cleaned
    assert len(removed) == 1


def test_strip_price_sentences_noop_when_no_price():
    body = "플랜을 안내드립니다.\n미팅에서 안내드릴게요."
    cleaned, removed = strip_price_sentences(body)
    assert cleaned == body
    assert removed == []


# ---------- send-time language guard ----------


def _msg(**kw):
    base = dict(
        id=1,
        channel="email",
        body="안녕하세요, 회신드립니다.",
        language="ko",
        target_language="en",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_send_guard_translates_when_language_differs():
    from src.integrations.senders import enforce_send_language

    msg = _msg(language="ko", target_language="en")
    with patch("src.llm.translate.translate_to", return_value="Hello, here is our reply.") as tx:
        enforce_send_language(msg)
    tx.assert_called_once()
    assert msg.body == "Hello, here is our reply."
    assert msg.language == "en"


def test_send_guard_noop_when_already_target():
    from src.integrations.senders import enforce_send_language

    msg = _msg(language="en", target_language="en", body="Hello.")
    with patch("src.llm.translate.translate_to") as tx:
        enforce_send_language(msg)
    tx.assert_not_called()
    assert msg.body == "Hello."


def test_send_guard_noop_without_target():
    from src.integrations.senders import enforce_send_language

    msg = _msg(language="ko", target_language=None, body="안녕하세요.  ")
    with patch("src.llm.translate.translate_to") as tx:
        enforce_send_language(msg)
    tx.assert_not_called()
    # Still washed (trailing spaces trimmed).
    assert msg.body == "안녕하세요."


def test_contains_price_english_words():
    # English spelled-out currency must also be caught (survives a mostly-Korean draft).
    assert contains_price("The plan is 29 dollars a month")
    assert contains_price("about 50 euros")
    assert not contains_price("about 50 people")


# ---------- send-path first-reply no-price guard (rule 8, enforced at send) ----------


def _seed_reply(db_session, body, *, prior_sent=False):
    from src.db.models import Contact, Conversation, Message

    c = Contact(normalized_email="x@acme.com", full_name="X", email="x@acme.com", domain="acme.com")
    db_session.add(c)
    db_session.flush()
    conv = Conversation(contact_id=c.id, inquiry_subject="pricing")
    db_session.add(conv)
    db_session.flush()
    if prior_sent:
        db_session.add(
            Message(
                conversation_id=conv.id,
                direction="outgoing",
                channel="email",
                body="이전 회신",
                language="en",
                status="sent",
            )
        )
    msg = Message(
        conversation_id=conv.id,
        direction="outgoing",
        channel="email",
        body=body,
        language="ko",
        target_language="en",
        status="approved",
    )
    db_session.add(msg)
    db_session.commit()
    return msg


def test_first_reply_price_stripped_at_send(db_session, monkeypatch):
    from src.integrations import senders

    msg = _seed_reply(db_session, "플랜 안내드립니다.\n- Creator $29/월\n미팅에서 안내드릴게요.")
    monkeypatch.setattr("src.db.session.SessionLocal", lambda: db_session)
    senders.enforce_first_reply_no_price(msg)
    assert "$29" not in msg.body
    assert "미팅" in msg.body


def test_later_reply_keeps_price_at_send(db_session, monkeypatch):
    from src.integrations import senders

    msg = _seed_reply(db_session, "Creator 플랜은 $29/월 입니다.", prior_sent=True)
    monkeypatch.setattr("src.db.session.SessionLocal", lambda: db_session)
    senders.enforce_first_reply_no_price(msg)
    # A later reply may quote prices — must be untouched.
    assert "$29" in msg.body

