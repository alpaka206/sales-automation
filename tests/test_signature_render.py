"""Tests for signature rendering and the 0022 migration that seeds signature
templates + adds messages.signature_key.

Nothing strips a signature out of a body any more. It used to: the prompt wrote one INTO
the body (``{{__signature__}}``), so picking a different one on the review screen meant
finding that text again and cutting it back off. The operator picks, the send path
attaches — one direction, no undo machinery (0061).
"""

from __future__ import annotations

import importlib

import pytest
from sqlalchemy import create_engine, inspect, text

import src.db.email_templates as et
from src.integrations.email_html import (
    branded_signature_html,
    sanitize_email_html,
    to_html_email,
)

_KO_SIG = "김규원\nPERSO AI | Intern (Developer Relations)\ndevrel.365@gmail.com"


@pytest.fixture()
def fake_templates(monkeypatch):
    """Stub get_email_template so signature lookups don't touch the real DB."""
    table = {
        "signature_ko": _KO_SIG,
        "signature_html_ko": "<table id='sig-ko'><tr><td>브랜드 서명 카드</td></tr></table>",
    }

    def _get(key, language=None):
        return table.get(key)

    monkeypatch.setattr(et, "get_email_template", _get)
    return table


# ---------------------------------------------------------------------------
# branded_signature_html semantics
# ---------------------------------------------------------------------------


def test_branded_signature_html_returns_card(fake_templates):
    assert "sig-ko" in branded_signature_html("signature_html_ko")


@pytest.mark.parametrize("key", [None, "", "none", "default", object()])
def test_branded_signature_html_none_cases(key, fake_templates):
    """"none" and "default" were the two extra choices the picker used to carry. Old rows
    still hold them, and no template answers to either — so they mean 서명 없음."""
    assert branded_signature_html(key) is None


def test_a_plain_text_signature_keeps_its_line_breaks(fake_templates):
    """서명을 HTML 로 쓸 이유는 없습니다 — 세 줄로 치면 세 줄이어야 합니다."""
    html = to_html_email("본문.", signature_html=_KO_SIG)
    assert "김규원<br>" in html
    assert "devrel.365@gmail.com" in html


# ---------------------------------------------------------------------------
# 0062: the auto-ack's footer is a logo, and it has to survive the sanitizer
# ---------------------------------------------------------------------------


def test_the_auto_ack_footer_is_seeded_under_the_key_the_ack_asks_for():
    """접수확인이 찾는 키와 마이그레이션이 넣는 키가 다르면 로고는 조용히 안 붙습니다 —
    없는 템플릿은 None 이 되고, 메일은 그냥 나갑니다."""
    from src.db.models import Base

    module = importlib.import_module("src.db.migrations.0062_auto_ack_footer_logo")

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    module.up(engine)
    module.up(engine)  # idempotent — 두 번째가 행을 하나 더 만들면 안 됩니다.

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT key, body FROM email_templates WHERE key = :k"),
            {"k": "auto_ack_footer"},
        ).all()
    assert len(rows) == 1
    assert "perso.ai/dubbing" in rows[0][1]


def test_the_logo_survives_the_email_sanitizer():
    """허용 목록이 <img> 의 src 나 <a> 의 href 를 떨어뜨리면 접수확인 아래가 빈 칸이 됩니다."""
    module = importlib.import_module("src.db.migrations.0062_auto_ack_footer_logo")

    html = to_html_email("접수했습니다.", signature_html=module._BODY)
    assert 'href="https://perso.ai/dubbing"' in html
    assert "framerusercontent.com" in html
    assert 'alt="Perso Dubbing"' in html
    assert 'height="28"' in html
    # &amp; 로 다시 이스케이프되어야 합니다 — 파서가 속성값의 문자 참조를 풀어 놓습니다.
    assert "width=1752&amp;height=279" in html
    assert html.index("접수했습니다") < html.index("framerusercontent")


# ---------------------------------------------------------------------------
# to_html_email with a branded signature card
# ---------------------------------------------------------------------------


def test_signature_appended_after_body():
    card = "<table id='THECARD'><tr><td>SIG</td></tr></table>"
    html = to_html_email("본문 첫 줄.\n\n감사합니다.", signature_html=card)
    assert "THECARD" in html
    assert html.index("본문 첫 줄") < html.index("THECARD")


def test_signature_inserted_before_trailing_separator():
    card = "<table id='THECARD'><tr><td>SIG</td></tr></table>"
    body = "본문입니다.\n\n감사합니다.\n\n---\n수신 거부: http://x/unsub"
    html = to_html_email(body, signature_html=card)
    # card sits between the body and the trailing footer paragraph
    assert html.index("본문입니다") < html.index("THECARD") < html.index("수신 거부")


def test_no_signature_is_backward_compatible():
    plain = to_html_email("본문\n\n둘째 문단")
    assert "THECARD" not in plain
    assert plain.count("<p ") == 2


def test_email_html_sanitizes_active_content_and_unsafe_urls():
    fragment = (
        '<p onclick="steal()">Hello<script>alert(1)</script>'
        '<a href="javascript:alert(2)">bad</a><a href="https://example.com">good</a></p>'
    )
    clean = sanitize_email_html(fragment)
    assert "onclick" not in clean
    assert "script" not in clean
    assert "alert(1)" not in clean
    assert "javascript:" not in clean
    assert 'href="https://example.com"' in clean


def test_signature_keeps_safe_table_formatting_but_drops_script():
    html = to_html_email(
        "Hello",
        signature_html='<table id="THECARD"><tr><td style="color:#123">Sig</td></tr></table><script>x</script>',
    )
    assert 'id="THECARD"' in html
    assert 'style="color:#123"' in html
    assert "<script" not in html


# ---------------------------------------------------------------------------
# 0022 migration: column + branded seed
# ---------------------------------------------------------------------------


def _run_0022(engine):
    mod = importlib.import_module(
        "src.db.migrations.0022_message_signature_and_branded_seed"
    )
    mod.up(engine)


def test_migration_0022_seeds_and_is_idempotent():
    from src.db.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    _run_0022(engine)
    _run_0022(engine)  # idempotent — a second run must not duplicate or error

    # signature_key column present on messages
    cols = {c["name"] for c in inspect(engine).get_columns("messages")}
    assert "signature_key" in cols

    with engine.connect() as conn:
        rows = dict(
            conn.execute(
                text(
                    "SELECT key, body FROM email_templates "
                    "WHERE key LIKE 'signature_html_%'"
                )
            ).all()
        )
    assert set(rows) == {"signature_html_ko", "signature_html_en"}
    assert "Perso" in rows["signature_html_ko"]
    assert rows["signature_html_en"].strip()  # non-empty seeded body


# ----- 본문 표기: 링크 라벨과 굵게·기울임·밑줄 -----


def test_a_labelled_link_keeps_its_label_not_the_url():
    """예약 주소는 120자짜리 base64 입니다. 맨 URL 을 그대로 앵커로 만들면 그 덩어리가
    메일 본문 한복판에 그대로 실립니다."""
    from src.integrations.email_html import to_html_email

    html = to_html_email("[미팅 링크](https://cal.example/AcZssZ3woViQ906eyzcO97gG4oZPCyESiCL7x)")
    assert 'href="https://cal.example/AcZssZ3woViQ906eyzcO97gG4oZPCyESiCL7x"' in html
    assert ">미팅 링크</a>" in html
    assert ">https://cal.example" not in html      # 주소가 글자로 보이면 안 됩니다


def test_bold_italic_underline_render():
    from src.integrations.email_html import to_html_email

    html = to_html_email("**굵게** *기울임* __밑줄__")
    assert "<strong>굵게</strong>" in html
    assert "<em>기울임</em>" in html
    assert "<u>밑줄</u>" in html


def test_a_bare_url_still_becomes_a_link():
    """표기를 안 쓴 본문도 예전 그대로 동작해야 합니다."""
    from src.integrations.email_html import to_html_email

    assert '<a href="https://perso.ai"' in to_html_email("https://perso.ai 를 보세요")


def test_the_markup_cannot_smuggle_a_dangerous_link():
    """표기 하나 늘렸다고 javascript: 가 들어올 구멍을 만들 수는 없습니다."""
    from src.integrations.email_html import to_html_email

    for 위험 in ("[누르기](javascript:alert(1))", "[누르기](data:text/html,x)"):
        html = to_html_email(위험)
        assert 'href="javascript:' not in html and 'href="data:' not in html
    # 라벨 안의 태그는 글자로 남습니다.
    assert "<b>" not in to_html_email("[<b>라벨</b>](https://perso.ai)")


def test_overlapping_marks_do_not_cross_tags():
    """`***x***` 는 굵게+기울임입니다. 겹친 것을 먼저 안 잡으면 굵게 규칙이 별 셋 중 둘만
    먹고 남은 하나를 기울임이 가져가면서 `<strong><em>x</strong></em>` 가 나옵니다."""
    from src.integrations.email_html import to_html_email

    html = to_html_email("***겹침***")
    assert "<strong><em>겹침</em></strong>" in html
    assert "</strong></em>" not in html          # 어긋난 닫힘


def test_a_stray_star_is_left_alone():
    """곱셈 기호와 문장 부호를 서식으로 읽으면 안 됩니다."""
    from src.integrations.email_html import to_html_email

    for 그대로 in ("2 * 3 = 6", "별 하나 * 둘 ** 은 그대로"):
        html = to_html_email(그대로)
        assert "<em>" not in html and "<strong>" not in html, 그대로
