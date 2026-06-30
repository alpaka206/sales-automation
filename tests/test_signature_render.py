"""Tests for branded signature rendering, text-signature stripping, and the
0022 migration that seeds branded signature templates + adds messages.signature_key.
"""

from __future__ import annotations

import importlib

import pytest
from sqlalchemy import create_engine, inspect, text

import src.db.email_templates as et
from src.integrations.email_html import (
    branded_signature_html,
    strip_known_signature,
    strips_text_signature,
    to_html_email,
)

_KO_SIG = "김규원\nPERSO AI | Intern (Developer Relations)\ndevrel.365@gmail.com"
_EN_SIG = "Kyuwon Kim\nPERSO AI | Intern (Developer Relations)\ndevrel.365@gmail.com"


@pytest.fixture()
def fake_templates(monkeypatch):
    """Stub get_email_template so signature lookups don't touch the real DB."""
    table = {
        "signature_ko": _KO_SIG,
        "signature_en": _EN_SIG,
        "signature_html_ko": "<table id='sig-ko'><tr><td>이혜람 카드</td></tr></table>",
        "signature_html_en": "<table id='sig-en'><tr><td>Hyeram card</td></tr></table>",
    }

    def _get(key, language=None):
        return table.get(key)

    monkeypatch.setattr(et, "get_email_template", _get)
    return table


# ---------------------------------------------------------------------------
# strips_text_signature / branded_signature_html semantics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,expected",
    [
        (None, False),
        ("", False),
        ("default", False),
        ("text", False),
        ("none", True),
        ("signature_html_ko", True),
        (object(), False),  # non-str (e.g. a mock) must never trigger a strip
    ],
)
def test_strips_text_signature(key, expected):
    assert strips_text_signature(key) is expected


def test_branded_signature_html_returns_card(fake_templates):
    assert "sig-ko" in branded_signature_html("signature_html_ko")


@pytest.mark.parametrize("key", [None, "", "default", "none", object()])
def test_branded_signature_html_none_cases(key, fake_templates):
    assert branded_signature_html(key) is None


# ---------------------------------------------------------------------------
# strip_known_signature
# ---------------------------------------------------------------------------


def test_strip_exact_korean_signature(fake_templates):
    body = f"안녕하세요.\n\n문의 주신 내용 확인했습니다.\n\n감사합니다.\n{_KO_SIG}"
    out = strip_known_signature(body)
    assert "김규원" not in out
    assert "devrel.365@gmail.com" not in out
    assert "감사합니다" not in out  # the trailing closing line is removed too
    assert "문의 주신 내용 확인했습니다." in out


def test_strip_via_email_anchor_when_translated(fake_templates):
    # A translated signature: the prose differs from the templates, but the email
    # address survives translation and anchors the cut.
    body = (
        "Hello.\n\nThanks for reaching out.\n\n"
        "ありがとうございます。\nKyuwon Kim\nPERSO AI\ndevrel.365@gmail.com"
    )
    out = strip_known_signature(body)
    assert "devrel.365@gmail.com" not in out
    assert "Thanks for reaching out." in out


def test_strip_noop_when_no_signature(fake_templates):
    body = "그냥 본문입니다.\n\n두 번째 문단."
    assert strip_known_signature(body) == body


def test_strip_handles_empty(fake_templates):
    assert strip_known_signature("") == ""


# ---------------------------------------------------------------------------
# to_html_email with a branded signature card
# ---------------------------------------------------------------------------


def test_signature_appended_after_body():
    card = "<table id='THECARD'><tr><td>SIG</td></tr></table>"
    html = to_html_email("본문 첫 줄.\n\n감사합니다.", signature_html=card)
    assert "THECARD" in html
    assert html.index("본문 첫 줄") < html.index("THECARD")


def test_signature_inserted_before_compliance_footer():
    card = "<table id='THECARD'><tr><td>SIG</td></tr></table>"
    body = "본문입니다.\n\n감사합니다.\n\n---\n수신 거부: http://x/unsub"
    html = to_html_email(body, signature_html=card)
    # card sits between the body and the trailing footer paragraph
    assert html.index("본문입니다") < html.index("THECARD") < html.index("수신 거부")


def test_no_signature_is_backward_compatible():
    plain = to_html_email("본문\n\n둘째 문단")
    assert "THECARD" not in plain
    assert plain.count("<p ") == 2


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
