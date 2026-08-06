"""Stable formatting and tone requirements in reply prompts."""

from __future__ import annotations

from unittest.mock import patch

from src.llm.prompts import load_prompt


def test_draft_prompt_requires_scannable_plain_text_layout() -> None:
    prompt = load_prompt(
        "inbound/draft_reply",
        {
            "contact_name": "고객",
            "company": "Example",
            "country": "KR",
            "category": "support",
            "score": "70",
            "last_message": "기능 두 가지를 문의합니다.",
            "conversation_context": "이전 문의가 있습니다.",
            "enrichment_context": "",
            "knowledge_docs": "",
            "pricing_rule": "확인된 정책만 사용합니다.",
        },
        include_rules=False,
    )

    assert "한 줄에는 한 문장 또는 한 가지 요점만" in prompt
    assert "각 줄을 정확히 `- `로 시작" in prompt
    assert "다음 행동은 회신, 미팅, 결제, 자료 전달 중" in prompt
    assert "이전 대화 맥락" in prompt
    assert "이전 문의가 있습니다." in prompt


def test_company_rules_allow_polite_requests_and_one_cta() -> None:
    """Asserted against the SEEDED rule text, not against a live database.

    The rules are rows in ``policy_sources`` now (edited in Notion, synced), so
    get_company_rules() is empty without a database — but the text this repository ships
    as the starting point still has to say these things, or a fresh install starts with
    rules that contradict the reviewed ones.
    """
    import pathlib

    seed = pathlib.Path("src/db/seeds/policy/rule_01_tone.md").read_text(encoding="utf-8")

    assert "말씀해 주세요" in seed
    assert "메일 하나의 CTA는 하나만" in seed
    assert "선호 채널" in seed
    # 서명은 사람이 고릅니다 — 규칙이 모델에게 본문에 쓰라고 시키면 안 됩니다.
    assert "{{__signature__}}" not in seed
    assert "본문에 서명을 쓰지 않습니다" in seed


def test_0061_takes_the_signature_out_of_a_live_rule_document():
    """살아 있는 규칙 문서를 씨앗 파일과 같은 문장으로 만듭니다.

    안 걷어내면 프롬프트에 ``{{__signature__}}`` 이라는 글자와 "아래 서명을 그대로
    붙이세요" 가 그대로 들어가고, 모델은 붙일 것이 없으니 서명을 지어냅니다.
    """
    import importlib.util

    from sqlalchemy import create_engine, text

    from src.db.models import Base

    spec = importlib.util.spec_from_file_location(
        "m0061", "src/db/migrations/0061_the_signature_is_picked_not_written.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    before = (
        "# 톤 & 시그니처 (공통)\n\n"
        "## 본문 구조\n\n"
        "6. 감사 인사와 서명\n\n"
        "## 시그니처 (본문 마지막에 그대로 붙이기 — 변형 금지)\n\n"
        "아래 서명을 본문 마지막에 그대로 붙이세요. 이 서명은 **이메일 템플릿(signature_ko)**"
        " 에서 수정되며, 아래 값이 자동 주입됩니다.\n\n"
        "```\n{{__signature__}}\n```\n\n"
        "## 변수 치환 검증\n\n"
        "- 본문이나 시그니처에 `{{ }}`, `{var}` 같은 placeholder 가 남아 있으면 발송 X.\n"
        "- 시그니처는 위 블록을 **그대로 복사**. 회사명·역할·메일주소 절대 변형 금지.\n"
    )

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO policy_sources (label, doc_key, mode, order_index, status, "
                "body, created_at, updated_at) VALUES ('rule_01_tone', "
                "'file:rule_01_tone.md', 'rules', 10, 'active', :body, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"body": before},
        )

    module.up(engine)
    module.up(engine)  # 두 번 돌려도 같아야 합니다 — 첫 판이 찾을 것을 없애 두었으므로.

    with engine.begin() as conn:
        after = conn.execute(text("SELECT body FROM policy_sources")).scalar_one()

    assert "{{__signature__}}" not in after
    assert "그대로 복사" not in after
    assert "# 톤 (공통)" in after
    assert "6. 감사 인사 (서명은 쓰지 않습니다" in after
    assert "본문에 서명을 쓰지 않습니다" in after
    # 뒤에 오는 절은 그대로 있어야 합니다 — 지운 것은 시그니처 절 하나입니다.
    assert "## 변수 치환 검증" in after
    assert "- 본문에 `{{ }}`, `{var}` 같은 placeholder" in after


def test_translation_prompt_preserves_dash_bullets() -> None:
    prompt = load_prompt(
        "util/translate_to",
        {"target_language": "English", "text": "안내\n\n- 조건 1\n- 조건 2"},
        include_rules=False,
    )

    assert "Keep every `- ` bullet" in prompt
    assert "paragraph breaks" in prompt


# ---- The reply SHAPE is a DB row, not a prompt file ------------------------------


def test_reply_format_reaches_the_draft_prompt():
    """Editing 답변 메일 형식 in the console must change the next draft, not the next deploy."""
    from unittest.mock import patch

    from src.llm.prompts import get_reply_format, load_prompt

    assert "{{reply_format}}" in load_prompt("inbound/draft_reply", include_rules=False)

    with patch("src.db.email_templates.get_email_template", return_value="  뼈대  "):
        assert get_reply_format() == "뼈대"


def test_a_template_outage_does_not_block_drafting():
    from unittest.mock import patch

    from src.llm.prompts import get_reply_format

    with patch("src.db.email_templates.get_email_template", side_effect=RuntimeError("db down")):
        assert get_reply_format() == ""


def test_links_are_substituted_not_generated():
    """The booking URL is ~120 chars of opaque base64 — a model would tidy it."""
    from unittest.mock import patch

    from src.db.migrations import __name__ as _  # noqa: F401  (keeps import ordering honest)
    from src.llm.prompts import apply_editable_tokens

    values = {"meeting_link": "https://calendar.example/abc123", "whatsapp_link": "https://wa.me/1"}
    with patch("src.db.email_templates.get_email_template", side_effect=values.get):
        out = apply_editable_tokens("미팅 예약: {{MEETING_LINK}}\nWhatsApp: {{WHATSAPP}}")
    assert out == "미팅 예약: https://calendar.example/abc123\nWhatsApp: https://wa.me/1"


def test_an_unset_link_stays_visible_instead_of_vanishing():
    """A blank would ship a sentence promising a link that is not there."""
    from unittest.mock import patch

    from src.llm.prompts import apply_editable_tokens

    with patch("src.db.email_templates.get_email_template", return_value=""):
        assert apply_editable_tokens("예약: {{MEETING_LINK}}") == "예약: {{MEETING_LINK}}"


def test_every_token_in_the_seeded_format_is_one_the_code_substitutes():
    """A token in the skeleton that the code does not know ships to the customer raw.

    One direction only. The code may know tokens the skeleton never mentions —
    ``{{SENDER_NAME}}`` lives in the Korean policy document, not in the reply shape — and
    that is not a defect; the defect is a token nothing can replace.
    """
    import importlib
    import re

    from src.llm.prompts import _EDITABLE_TOKENS

    seed = importlib.import_module("src.db.migrations.0042_reply_format_template")
    known = set(_EDITABLE_TOKENS)
    # Upper-case tokens only: the skeleton also names {{reply_format}}-style prompt
    # variables, which load_prompt fills in long before a body exists.
    for token in re.findall(r"\{\{[A-Z_]+\}\}", seed.REPLY_FORMAT):
        assert token in known, token
    assert seed.WHATSAPP_LINK.endswith("821054802261")


def test_the_sender_name_is_one_row_not_a_name_typed_into_a_document():
    """한국어 템플릿은 본문 첫 줄에서 쓰는 사람을 이름으로 소개합니다. 그 이름을 정책 문서에
    박아 두면 담당자가 바뀔 때 고칠 곳이 서명과 문서 두 군데가 되고, 한쪽만 고치면 인사말과
    서명이 서로 다른 사람을 가리키는 메일이 나갑니다. 링크 두 개와 같은 방식으로 다룹니다."""
    from src.llm.prompts import _EDITABLE_TOKENS, apply_editable_tokens

    assert _EDITABLE_TOKENS["{{SENDER_NAME}}"] == "sender_name"
    with patch("src.db.email_templates.get_email_template", side_effect=lambda key, *a, **k:
               {"sender_name": "배운태"}.get(key)):
        assert apply_editable_tokens("이스트소프트 {{SENDER_NAME}}입니다") == "이스트소프트 배운태입니다"


def test_an_unset_sender_name_leaves_the_token_where_it_can_be_seen():
    """"이스트소프트 입니다" 는 읽는 순간 이미 나간 뒤입니다. 토큰은 발송 전에 눈에 띕니다."""
    from src.llm.prompts import apply_editable_tokens

    with patch("src.db.email_templates.get_email_template", return_value=""):
        assert apply_editable_tokens("이스트소프트 {{SENDER_NAME}}입니다") == "이스트소프트 {{SENDER_NAME}}입니다"


def test_the_sender_name_follows_the_customers_language_not_the_drafts():
    """"배운태" 와 "Untae Bae" 는 번역이 아니라 같은 사람의 두 표기입니다. 초안 본문은 검토용
    으로 늘 한국어인데, 그 초안이 영어 고객에게 갈 것이면 처음부터 영문 표기가 들어가야
    번역 단계가 그것을 건드리지 않습니다 — 아니면 모델이 매번 다르게 로마자로 바꿉니다."""
    from src.llm.prompts import apply_editable_tokens

    names = {"sender_name": "배운태", "sender_name_en": "Untae Bae"}
    with patch("src.db.email_templates.get_email_template", side_effect=lambda k, **kw: names.get(k)):
        assert apply_editable_tokens("{{SENDER_NAME}}", language="en") == "Untae Bae"
        assert apply_editable_tokens("{{SENDER_NAME}}", language="ko") == "배운태"
        # 언어를 모르면 한국어 표기 — 초안이 한국어라 그쪽이 덜 틀립니다.
        assert apply_editable_tokens("{{SENDER_NAME}}") == "배운태"


def test_an_empty_english_name_falls_back_rather_than_leaving_a_blank():
    """영문 칸만 비어 있으면 한국어 표기라도 넣습니다. 둘 다 비어야 토큰이 남습니다."""
    from src.llm.prompts import apply_editable_tokens

    names = {"sender_name": "배운태", "sender_name_en": ""}
    with patch("src.db.email_templates.get_email_template", side_effect=lambda k, **kw: names.get(k)):
        assert apply_editable_tokens("{{SENDER_NAME}}", language="en") == "배운태"
