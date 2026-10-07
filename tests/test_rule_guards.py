"""Unit tests for the deterministic rule guards (no LLM)."""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.common.pricing_guard import contains_price
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


def test_an_indented_line_after_a_bullet_continues_it():
    """줄을 맞춰 쓴 항목은 한 항목입니다 — 그 사이에 빈 줄을 넣으면 문단 셋으로 쪼개져 나갔습니다(MSG#110)."""
    wrapped = (
        "Direct answers first:\n"
        "\n"
        "- A covers dubbing for\n"
        "  up to 60 minutes\n"
        "  per month.\n"
        "- B is metered.\n"
        "Thanks."
    )
    assert text_wash(wrapped) == (
        "Direct answers first:\n"
        "\n"
        "- A covers dubbing for\n"
        "  up to 60 minutes\n"
        "  per month.\n"
        "- B is metered.\n"
        "\n"
        "Thanks."
    )


def test_a_blank_line_ends_the_item_and_indented_prose_keeps_its_indent():
    raw = "- item\n\n\n\n   an indented note\nprose"
    assert text_wash(raw) == "- item\n\n   an indented note\nprose"


def test_a_dash_sign_off_is_not_a_bullet():
    """「— Untae」가 목록 한 칸(`<ul><li>`)이 되어 나갔습니다. 대시는 맺음·덧붙임을 여는 글자이기도 합니다."""
    assert text_wash("Thanks,\n— Untae") == "Thanks,\n— Untae"
    assert text_wash("– a note") == "– a note"


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


@pytest.mark.parametrize("line", [
    # 범위를 묻는 질문 — 「per month」 하나로 금액이 됐고, 그 줄이 첫 회신에서 지워졌습니다(MSG#110).
    "How many minutes per month do you expect to dub?",
    "- The top tier and its hours per month. A monthly total comes later",
    "1. Footage. About how many hours of video do you publish per month,",
    "We usually bill per seat.",
    "Upload limit is 60/mo",
    "Credits refresh 1,000/month.",
    "100/월 크레딧",
    # 「동」(VND) 이 한국 주소에 걸렸습니다.
    "제2동 사무실",
    "3동 건물",
    "cut the per-minute cost by as much as 35% compared with",
])
def test_a_quantity_is_not_a_price(line):
    """금액은 숫자 옆의 통화(기호·코드·말)입니다. 기간이나 수량만으로는 아닙니다."""
    assert not contains_price(line)


@pytest.mark.parametrize("line", [
    "Starter is $19 a month and includes 30",
    "minutes of dubbing. A top-up pack adds 60 minutes for $25.",
    "Together that covers 90 minutes in one language for $44, with",
    "29€ per seat",
    "3만 원",
    "10,000 VND",
])
def test_a_currency_next_to_a_number_is_a_price(line):
    assert contains_price(line)


# ---------- send-time language guard ----------


def _msg(**kw):
    base = dict(
        id=1,
        body="안녕하세요, 회신드립니다.",
        language="ko",
        target_language="en",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_send_guard_refuses_unreviewed_translation():
    from src.integrations.senders import SendLanguageMismatch, enforce_send_language

    msg = _msg(language="ko", target_language="en")
    with patch("src.llm.translate.translate_to") as tx:
        with pytest.raises(SendLanguageMismatch, match="requires reviewed translation"):
            enforce_send_language(msg)
    tx.assert_not_called()


def test_send_guard_noop_when_already_target():
    from src.integrations.senders import enforce_send_language

    msg = _msg(language="en", target_language="en", body="Hello.")
    with patch("src.llm.translate.translate_to") as tx:
        enforce_send_language(msg)
    tx.assert_not_called()
    assert msg.body == "Hello."


def test_send_guard_refuses_korean_body_with_stale_target_metadata():
    from src.integrations.senders import SendLanguageMismatch, enforce_send_language

    msg = _msg(language="en", target_language="en", body="안녕하세요. 문의 감사합니다.")
    with pytest.raises(SendLanguageMismatch):
        enforce_send_language(msg)


@pytest.mark.parametrize("target", [None, "en"])
def test_the_send_guard_never_rewrites_the_body(target):
    """발송은 막기만 합니다 — 승인한 글자를 공백 하나도 다듬지 않고, 링크 줄도 다시 쓰지 않습니다.

    예전에는 여기서 공백을 고르고 연락 링크 줄을 통째로 다시 썼습니다. 승인 **뒤**의 일이라 고객이
    받은 글은 아무도 승인한 적 없는 글이었습니다.
    """
    from src.integrations.senders import enforce_send_language

    body = "Hello.  \n\nIf it's easier, grab a slot here: https://calendar.example/abc123 or reply."
    msg = _msg(language="en", target_language=target, body=body)
    links = {"meeting_link": "[Calendly](https://calendar.example/abc123)",
             "whatsapp_link": "[WhatsApp](https://wa.me/1)"}
    with (patch("src.llm.translate.translate_to") as tx,
          patch("src.db.email_templates.get_email_template", side_effect=links.get)):
        enforce_send_language(msg)
    tx.assert_not_called()
    assert msg.body == body


def test_contains_price_english_words():
    # English spelled-out currency must also be caught (survives a mostly-Korean draft).
    assert contains_price("The plan is 29 dollars a month")
    assert contains_price("about 50 euros")
    assert not contains_price("about 50 people")


# ---------- send path: refuse an unfilled slot, never rewrite (2026-10-06) ----------
#
# 첫 회신의 금액은 예전에 발송이 **줄째 지웠습니다**(MSG#110 · #118). 이제 발송은 본문을 건드리지
# 않고, 막는 것은 채우지 않은 자리 하나입니다 — 금액도 대화 기록도 안 봅니다(운영자: 「답변 작성에
# 대한 경고문 이런건 필요없어」).


def test_a_first_reply_price_goes_out_as_written_without_reading_the_history(monkeypatch):
    from src.integrations import senders

    def unavailable():
        raise RuntimeError("unavailable")

    monkeypatch.setattr("src.db.session.SessionLocal", unavailable)
    body = "Thanks for asking.\n- Starter is $19 a month.\nHappy to walk you through it."
    msg = _msg(language="en", target_language="en", body=body)
    senders.enforce_no_unfilled_slots(msg)
    assert msg.body == body


@pytest.mark.parametrize("language,target,body,refused", [
    ("en", "en", "Hi [Name],\n\nFollowing up on my last note.", "Hi [Name],"),
    ("en", "en", "Book a slot: {{MEETING_LINK}}", "Book a slot: {{MEETING_LINK}}"),
    # 나갈 언어가 한국어가 아니면 대괄호 속 한국어는 쓰는 사람에게 남긴 지시입니다.
    ("en", "en", "[가격을 먼저 안내했다면] One note first.", "[가격을 먼저 안내했다면] One note first."),
    ("ko", None, "[안내] 결제 방법은 아래와 같습니다.", None),
])
def test_an_unfilled_slot_stops_delivery_and_the_body_stays(language, target, body, refused):
    from src.integrations import senders
    from src.integrations.delivery import DeliveryPermanentError

    msg = _msg(language=language, target_language=target, body=body)
    if refused is None:
        senders.enforce_no_unfilled_slots(msg)
    else:
        with pytest.raises(DeliveryPermanentError, match=re.escape(f"「{refused}」")):
            senders.enforce_no_unfilled_slots(msg)
    assert msg.body == body
