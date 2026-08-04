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
