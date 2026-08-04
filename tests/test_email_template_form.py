"""이메일 템플릿 — what the screen asks for, and what it must not.

The form had four fields the operator could not act on: a key that is a code reference
(the send path resolves ``auto_ack`` / ``signature_ko`` / the reply-format row by exact
key, so moving one silently unhooks a template from the app), a description nothing ever
displayed, and a change memo that only reached a history line. What is left is the name,
the language, the status, and the body.
"""

from __future__ import annotations

import pathlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.db.base import Base
from src.db.email_templates import SIGNATURE_KEY_PREFIX
from src.db.models import EmailTemplate

FORM = pathlib.Path("frontend/src/screens/EmailTemplates.tsx")


@pytest.fixture()
def template_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with patch("src.api.routes.email_templates.SessionLocal", factory):
        yield factory


def test_the_form_asks_only_for_what_an_operator_decides():
    source = FORM.read_text(encoding="utf-8")
    assert 'id="et-name"' in source
    assert "템플릿 이름" in source
    for gone in ("키", "설명", "변경 메모", "버전", "상태"):
        # The screen's own label text — none of these is a decision the operator makes.
        assert f'field-label" htmlFor="et-{gone}' not in source, gone
    assert "et-name" in source


def test_creating_a_template_needs_no_key(template_db):
    with TestClient(app) as client:
        response = client.post(
            "/email-templates",
            data={"name": "Sales signature", "language": "en", "body": "<p>PERSO</p>"},
        )
    assert response.status_code == 200
    with template_db() as session:
        tpl = session.query(EmailTemplate).one()
        # The prefix is not decoration: the compose screen's signature picker is the only
        # thing that can reach a template created here, and it looks for exactly this.
        assert tpl.key == f"{SIGNATURE_KEY_PREFIX}sales_signature"
        assert tpl.name == "Sales signature"


def test_a_korean_name_still_produces_a_usable_key(template_db):
    """It romanizes to nothing, so the language stands in — the key is never empty."""
    with TestClient(app) as client:
        client.post("/email-templates", data={"name": "기본 서명", "language": "ko"})
    with template_db() as session:
        assert session.query(EmailTemplate).one().key == f"{SIGNATURE_KEY_PREFIX}ko"


def test_two_templates_with_the_same_name_do_not_collide(template_db):
    with TestClient(app) as client:
        client.post("/email-templates", data={"name": "기본 서명", "language": "ko"})
        client.post("/email-templates", data={"name": "기본 서명", "language": "ko"})
    with template_db() as session:
        keys = sorted(row.key for row in session.query(EmailTemplate).all())
    assert keys == [f"{SIGNATURE_KEY_PREFIX}ko", f"{SIGNATURE_KEY_PREFIX}ko_2"]


def test_a_name_with_no_body_is_refused(template_db):
    with TestClient(app) as client:
        response = client.post("/email-templates", data={"name": "  "})
    assert response.status_code == 400
    with template_db() as session:
        assert session.query(EmailTemplate).count() == 0


def test_editing_cannot_move_the_key_or_blank_the_description(template_db):
    """Both used to be posted. The key is what the send path resolves, and a description
    the form no longer shows would have been overwritten with the empty string it sent."""
    with template_db() as session:
        session.add(
            EmailTemplate(
                key="auto_ack",
                name="접수확인",
                language="ko",
                channel="email",
                description="자동 접수확인 본문",
                status="active",
                version=1,
                body="안녕하세요",
            )
        )
        session.commit()
        tpl_id = session.query(EmailTemplate).one().id

    with TestClient(app) as client:
        response = client.put(
            f"/email-templates/{tpl_id}",
            data={"name": "접수확인 메일", "language": "ko", "body": "새 본문"},
        )
    assert response.status_code == 200
    with template_db() as session:
        tpl = session.get(EmailTemplate, tpl_id)
        assert tpl.key == "auto_ack"
        assert tpl.description == "자동 접수확인 본문"
        assert tpl.name == "접수확인 메일"
        assert tpl.body == "새 본문"
        assert tpl.version == 2


def test_everything_saved_here_is_active(template_db):
    """Only active rows are ever read — the send path and the signature picker both
    filter on it — so a draft template was one that exists and does nothing. With no
    control left to set the value back, saving has to revive a dormant row rather than
    strand it where nothing can reach it."""
    with template_db() as session:
        session.add(
            EmailTemplate(
                key="signature_html_old",
                name="예전 서명",
                language="ko",
                channel="email",
                status="archived",
                version=1,
                body="<p>old</p>",
            )
        )
        session.commit()
        tpl_id = session.query(EmailTemplate).one().id

    with TestClient(app) as client:
        client.post("/email-templates", data={"name": "새 서명", "language": "ko"})
        client.put(f"/email-templates/{tpl_id}", data={"name": "예전 서명", "language": "ko"})
    with template_db() as session:
        assert {row.status for row in session.query(EmailTemplate).all()} == {"active"}
