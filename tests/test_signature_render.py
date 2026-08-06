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
    from src.db.email_templates import AUTO_ACK_FOOTER_KEY
    from src.db.models import Base

    module = importlib.import_module("src.db.migrations.0062_auto_ack_footer_logo")

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    module.up(engine)
    module.up(engine)  # idempotent — 두 번째가 행을 하나 더 만들면 안 됩니다.

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT key, body FROM email_templates WHERE key = :k"),
            {"k": AUTO_ACK_FOOTER_KEY},
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
