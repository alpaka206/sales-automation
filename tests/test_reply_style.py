"""Stable formatting and tone requirements in reply prompts."""

from __future__ import annotations

from unittest.mock import patch

from tests.conftest import legacy_policy_columns, legacy_template_columns
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
            "reply_language": "English",
        },
        include_rules=False,
    )

    assert "이전 대화 맥락" in prompt
    assert "이전 문의가 있습니다." in prompt
    # 이 초안에만 해당하는 것은 여기 남습니다. 언어는 문의마다 다르므로 값으로 들어옵니다.
    assert "**English로만** 작성합니다" in prompt


def test_the_layout_rules_live_in_exactly_one_place():
    """줄바꿈·불릿·톤 규칙이 프롬프트 파일과 정책 문서 양쪽에 있으면, 운영자가 콘솔에서 고친
    쪽과 배포해야 바뀌는 쪽이 조용히 어긋납니다. 콘솔이 이깁니다 — 고칠 수 있는 쪽이라서.

    이 테스트가 하는 일은 "같은 규칙이 두 군데 있지 않다" 하나뿐입니다.
    """
    import pathlib

    prompt = pathlib.Path("src/llm/prompts/inbound/draft_reply.md").read_text(encoding="utf-8")
    seed = pathlib.Path(
        "src/db/seeds/policy/rule_01_common_principles.md"
    ).read_text(encoding="utf-8")

    for rule in ("한 줄에는 한 문장", "불릿", "이모지", "CTA"):
        assert rule in seed, rule
        assert rule not in prompt, rule
    # 서명은 어느 쪽에도 없습니다 — 사람이 발송할 때 고릅니다.
    assert "서명" not in prompt


def test_company_rules_allow_polite_requests_and_one_cta() -> None:
    """Asserted against the SEEDED rule text, not against a live database.

    The rules are rows in ``policy_sources`` now (edited in Notion, synced), so
    get_company_rules() is empty without a database — but the text this repository ships
    as the starting point still has to say these things, or a fresh install starts with
    rules that contradict the reviewed ones.
    """
    import pathlib

    seed = pathlib.Path(
        "src/db/seeds/policy/rule_01_common_principles.md"
    ).read_text(encoding="utf-8")

    assert "말씀해 주세요" in seed
    assert "다음 행동(CTA)은 하나입니다" in seed
    assert "선호 채널" in seed
    # 서명은 사람이 고릅니다 — 규칙이 모델에게 본문에 쓰라고 시키면 안 됩니다.
    assert "{{__signature__}}" not in seed
    assert "본문에 서명을 쓰지 않습니다" in seed
    # 가격 숫자는 코드 상수와 문서가 같은 말을 해야 합니다. 예전에는 정반대였습니다.
    assert "가격 숫자를 회신에 쓰지 않습니다" in seed


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
    legacy_policy_columns(engine)
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


def test_the_router_reads_the_usage_note_when_one_is_written():
    """문서를 고르는 것은 모델이고, 모델이 보는 것은 본문이 아니라 인덱스의 summary 한
    줄입니다. 그래서 「언제 쓰는가」 칸이 그 자리에 들어가야 합니다 — 안 들어가면 화면에는
    용도가 보이는데 문서는 계속 안 골라지고, 그 이유는 아무 데도 안 보입니다.

    비워 두면 본문 앞부분을 자릅니다. 표로 시작하는 문서에는 그게 쓸모없는 요약이라
    (「| 케이스 | 문구 |」) 그런 문서는 이 칸을 채워야 골라집니다.
    """
    from src.db.models import PolicySource
    from src.llm.knowledge import summary_of

    written = PolicySource(
        label="견적 및 맞춤형 플랜 안내", doc_key="k-quote", mode="knowledge",
        body="| 케이스 | 문구 |\n|---|---|\n| 1 | ... |",
        usage_note="Quote, Price, pricing, cost, estimate 등 가격·견적을 직접 묻는 문의에 씁니다.")
    blank = PolicySource(
        label="지원 언어", doc_key="k-lang", mode="knowledge",
        body="지원 언어 목록입니다. 한국어, 영어, 일본어…")

    assert summary_of(written).startswith("Quote, Price")
    # 표로 시작하는 문서였습니다 — 칸이 없었으면 요약이 "| 케이스 | 문구 |" 였습니다.
    assert "케이스" not in summary_of(written)
    assert summary_of(blank).startswith("지원 언어 목록입니다")


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


def test_the_english_link_row_can_go_because_the_korean_one_answers_for_it():
    """링크는 **한 행**입니다 (2026-08-19 운영자 결정). 행에는 주소만 들어 있고 표기는
    정책 문서가 `[Calendly]({{MEETING_LINK}})` 로 달아 줍니다 — 그래서 영문 전용 행은 같은
    주소를 두 번 적어 두는 자리가 됐고, 둘 중 하나만 고치면 조용히 갈라졌습니다.

    지운 행은 ``get_email_template`` 이 ``status='active'`` 만 보므로 None 입니다. 그때 영문
    문의가 남은 행의 주소를 쓰지 못하면 링크가 통째로 빠진 메일이 검토 화면에 올라옵니다 —
    지워도 되는지는 이 한 줄이 사실인지가 전부였습니다.
    """
    from src.llm.prompts import apply_editable_tokens

    # `_en` 행이 없는 상태. 지운 행도, 비운 행도 결과는 같습니다.
    left = {"meeting_link": "https://calendar.example/abc123", "whatsapp_link": "https://wa.me/1"}
    with patch("src.db.email_templates.get_email_template", side_effect=lambda k, **kw: left.get(k)):
        out = apply_editable_tokens(
            "[Calendly]({{MEETING_LINK}}) · [WhatsApp]({{WHATSAPP}})", language="en"
        )
    assert out == "[Calendly](https://calendar.example/abc123) · [WhatsApp](https://wa.me/1)"


def test_contact_links_are_an_exact_two_line_footer_not_model_prose():
    from src.llm.prompts import canonicalize_contact_links

    values = {
        "meeting_link": "https://calendar.example/abc123",
        "whatsapp_link": "[WhatsApp](https://wa.me/1)",
    }
    body = (
        "Thank you for your inquiry.\n\n"
        "You can schedule a meeting at [Calendly](https://calendar.example/abc123) "
        "or contact us via [WhatsApp](https://wa.me/1)."
    )
    with patch("src.db.email_templates.get_email_template", side_effect=values.get):
        out = canonicalize_contact_links(body, "en")

    assert out == (
        "Thank you for your inquiry.\n\n"
        "[Calendly](https://calendar.example/abc123)\n"
        "[WhatsApp](https://wa.me/1)"
    )


def test_a_korean_reply_gets_the_meeting_link_only_and_in_korean():
    """국문 회신에는 WhatsApp 이 붙지 않고, 링크 글자도 「미팅 링크」입니다.

    0069 가 국문 서식에서 ``{{WHATSAPP}}`` 을 뺐는데도 이 푸터가 언어와 무관하게 두 줄을
    다시 붙였습니다 — ``language`` 를 어느 행에서 URL 을 읽을지 고르는 데만 썼기 때문입니다.
    운영자가 실제로 받은 국문 메일에 WhatsApp 링크가 들어 있었습니다(2026-08-26).
    """
    from src.llm.prompts import canonicalize_contact_links

    values = {
        "meeting_link": "https://calendar.example/abc123",
        "whatsapp_link": "https://wa.me/1",
    }
    body = (
        "안녕하세요.\n"
        "\n"
        "아래 링크로 편하신 시간을 알려주세요.\n"
        "\n"
        "{{MEETING_LINK}}"
    )
    with patch("src.db.email_templates.get_email_template", side_effect=values.get):
        out = canonicalize_contact_links(body, "ko")

    assert out.endswith("[미팅 링크](https://calendar.example/abc123)")
    assert "wa.me" not in out and "WhatsApp" not in out


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


# ----- 국문과 영문은 다른 서식·다른 링크 표기 -----


def test_the_reply_skeleton_is_per_language():
    """한 벌만 두었더니 국문 문의에 영문용 문장이 그대로 따라왔습니다 — 한국어 회신에
    WhatsApp 안내가 붙은 것이 그것입니다. auto_ack / auto_ack_en 과 같은 규칙입니다."""
    from unittest.mock import patch

    from src.llm.prompts import get_reply_format

    행 = {"reply_format": "국문 뼈대", "reply_format_en": "EN skeleton"}
    with patch("src.db.email_templates.get_email_template", side_effect=행.get):
        assert get_reply_format("ko") == "국문 뼈대"
        assert get_reply_format(None) == "국문 뼈대"
        assert get_reply_format("en") == "EN skeleton"
        assert get_reply_format("ja") == "EN skeleton"     # 국문이 아니면 영문 쪽

    # 영문 행이 없으면 국문으로 떨어집니다 — 서식이 아예 없는 것보다 낫습니다.
    with patch("src.db.email_templates.get_email_template", side_effect={"reply_format": "국문만"}.get):
        assert get_reply_format("en") == "국문만"


def test_the_meeting_and_whatsapp_links_are_per_language():
    """주소가 달라서가 아니라 **표기가 달라서**입니다. 국문은 「미팅 링크」 한 줄이고
    WhatsApp 이 없으며, 영문은 Calendly · WhatsApp 각각의 글자에 겁니다."""
    from unittest.mock import patch

    from src.llm.prompts import _PER_LANGUAGE_TOKENS, apply_editable_tokens

    assert {"{{MEETING_LINK}}", "{{WHATSAPP}}"} <= _PER_LANGUAGE_TOKENS

    행 = {
        "meeting_link": "[미팅 링크](https://cal.example/kr)",
        "meeting_link_en": "[Calendly](https://cal.example/en)",
        "whatsapp_link_en": "[WhatsApp](https://wa.me/1)",
    }
    with patch("src.db.email_templates.get_email_template", side_effect=행.get):
        assert apply_editable_tokens("{{MEETING_LINK}}", "ko") == 행["meeting_link"]
        assert apply_editable_tokens("{{MEETING_LINK}}", "en") == 행["meeting_link_en"]
        # 국문 행이 없는 토큰은 치환하지 않고 그대로 둡니다 — 빈칸으로 나가는 것보다
        # 검토 화면에 토큰이 보이는 편이 낫습니다.
        assert apply_editable_tokens("{{WHATSAPP}}", "ko") == "{{WHATSAPP}}"
        assert apply_editable_tokens("{{WHATSAPP}}", "en") == 행["whatsapp_link_en"]


def _legacy_revision_table(engine) -> None:
    """0069·0086 이 스냅샷을 쓰던 ``email_template_revisions``.

    그 표는 0096 이 ``document_revisions`` 로 옮기고 지웠으므로 모델이 없습니다. 옛
    마이그레이션은 raw SQL 로 그 이름에 쓰는데, **이미 적용된 DB 에서는 다시 돌지 않으므로**
    고칠 이유가 없습니다 — 여기서만 그때의 표를 세워 주면 그 시절 동작을 그대로 잽니다.
    """
    from sqlalchemy import text as sql_text

    with engine.begin() as conn:
        conn.execute(
            sql_text(
                "CREATE TABLE email_template_revisions ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, template_id INTEGER, key VARCHAR, "
                "name VARCHAR, language VARCHAR, channel VARCHAR, body TEXT, "
                "description TEXT, status VARCHAR, change_note TEXT, edited_by VARCHAR, "
                "created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
            )
        )


def test_the_language_rows_exist_because_nothing_else_can_make_them():
    """위 두 테스트가 읽는 ``*_en`` 행은 콘솔에서 만들 수 없습니다 — 「추가」는 ``signature_``
    접두사가 붙은 행만 만듭니다. 심는 곳이 마이그레이션 하나뿐이라, 그게 실제로 심는지를
    여기서 확인합니다. 안 그러면 코드는 영문 행을 찾는데 DB 에는 영영 없습니다."""
    import importlib

    from sqlalchemy import create_engine, text

    from src.db.models import EmailTemplate

    engine = create_engine("sqlite:///:memory:")
    EmailTemplate.__table__.create(engine)
    legacy_template_columns(engine)
    _legacy_revision_table(engine)
    importlib.import_module("src.db.migrations.0042_reply_format_template").up(engine)
    importlib.import_module("src.db.migrations.0069_links_are_words_not_urls").up(engine)

    with engine.begin() as conn:
        행 = dict(conn.execute(text("SELECT key, body FROM email_templates")).fetchall())

    # 링크는 주소가 아니라 글자로 나갑니다 — 주소는 그대로 남습니다.
    assert 행["meeting_link"].startswith("[미팅 링크](https://calendar.google.com/")
    assert 행["meeting_link_en"].startswith("[Calendly](https://calendar.google.com/")
    assert 행["whatsapp_link_en"] == "[WhatsApp](https://wa.me/821054802261)"
    # 국문 서식에서 WhatsApp 이 빠지고, 영문 서식은 그것을 그대로 들고 갑니다.
    assert "{{WHATSAPP}}" not in 행["reply_format"]
    assert "{{WHATSAPP}}" in 행["reply_format_en"]
    assert "미팅 예약: {{MEETING_LINK}}" not in 행["reply_format"]
    assert "{{MEETING_LINK}}" in 행["reply_format"]
    # 토큰을 그대로 출력하라는 주의 문장에도 {{WHATSAPP}} 가 있었습니다. 그 줄까지 지우면
    # 120자 예약 주소를 모델이 "정리" 하지 못하게 막던 지시가 같이 사라집니다.
    assert "절대 바꾸거나 풀어쓰지 말고" in 행["reply_format"]

    # 두 번 돌아도 같습니다 — 이미 표기가 붙은 행을 다시 감싸지 않습니다.
    importlib.import_module("src.db.migrations.0069_links_are_words_not_urls").up(engine)
    with engine.begin() as conn:
        다시 = dict(conn.execute(text("SELECT key, body FROM email_templates")).fetchall())
    assert 다시 == 행


def test_0086_normalizes_live_link_templates_without_changing_urls():
    import importlib

    from sqlalchemy import create_engine, text

    from src.db.models import EmailTemplate

    engine = create_engine("sqlite:///:memory:")
    EmailTemplate.__table__.create(engine)
    legacy_template_columns(engine)
    _legacy_revision_table(engine)
    importlib.import_module("src.db.migrations.0042_reply_format_template").up(engine)
    importlib.import_module("src.db.migrations.0069_links_are_words_not_urls").up(engine)
    importlib.import_module("src.db.migrations.0086_contact_link_templates_are_exact").up(engine)

    with engine.begin() as conn:
        rows = dict(
            conn.execute(
                text(
                    "SELECT key, body FROM email_templates WHERE key LIKE 'meeting_link%' "
                    "OR key LIKE 'whatsapp_link%'"
                )
            ).fetchall()
        )
        revisions = conn.execute(
            text("SELECT COUNT(*) FROM email_template_revisions WHERE edited_by='0086'")
        ).scalar_one()

    assert rows["meeting_link"].startswith("[Calendly](https://calendar.google.com/")
    assert rows["meeting_link_en"].startswith("[Calendly](https://calendar.google.com/")
    assert rows["whatsapp_link"] == "[WhatsApp](https://wa.me/821054802261)"
    assert rows["whatsapp_link_en"] == "[WhatsApp](https://wa.me/821054802261)"
    assert revisions >= 1


def test_the_link_replaces_the_line_it_found_and_does_not_move_to_the_end():
    """미팅 링크는 맺음말 **위**에 선다 — 붙이는 것이 아니라 그 자리를 대신한다.

    끝에 덧붙이던 시절에는 국문 회신이 「감사합니다.」 아래에 링크를 달고 나갔다. 맺음말이
    행동 요청보다 먼저 온 것이다(2026-08-26, msg 62). 서식은 이미 모델에게 맺음말 위에
    토큰을 두라고 말하고 있었고, 옮긴 것은 이 함수였다.
    """
    from src.llm.prompts import canonicalize_contact_links

    values = {"meeting_link": "https://calendar.example/abc123", "whatsapp_link": ""}
    body = (
        "안녕하세요.\n"
        "\n"
        "아래 링크로 편하신 시간을 알려주세요.\n"
        "\n"
        "{{MEETING_LINK}}\n"
        "\n"
        "감사합니다."
    )
    with patch("src.db.email_templates.get_email_template", side_effect=values.get):
        out = canonicalize_contact_links(body, "ko")

    assert out.index("[미팅 링크]") < out.index("감사합니다.")
    assert out.endswith("감사합니다.")
    assert "\n\n\n" not in out
