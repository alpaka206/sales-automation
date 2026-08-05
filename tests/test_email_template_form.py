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
    with (
        patch("src.api.routes.email_templates.SessionLocal", factory),
        # set_default_signature opens its own session — the read side of the same table.
        patch("src.db.email_templates.SessionLocal", factory),
    ):
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


def test_the_default_signature_moves_from_the_console(template_db):
    """0046 turned "who signs the company's mail" into a row instead of a literal in
    inbound.py — and then nothing ever called set_default_signature, so the flag stayed
    on whoever the migration carried over and could only be moved with SQL."""
    from src.db.email_templates import default_signature_key

    with template_db() as session:
        session.add_all(
            [
                EmailTemplate(key=f"{SIGNATURE_KEY_PREFIX}old", name="이전 담당자",
                              language="all", channel="email", status="active",
                              version=1, body="<p>old</p>", is_default=True),
                EmailTemplate(key=f"{SIGNATURE_KEY_PREFIX}new", name="새 담당자",
                              language="all", channel="email", status="active",
                              version=1, body="<p>new</p>", is_default=False),
            ]
        )
        session.commit()
        new_id = session.query(EmailTemplate).filter_by(name="새 담당자").one().id

    with TestClient(app) as client:
        assert client.post(f"/email-templates/{new_id}/default").status_code == 200

    assert default_signature_key() == f"{SIGNATURE_KEY_PREFIX}new"
    with template_db() as session:
        # Exactly one, or "the default" stops meaning anything.
        assert [row.name for row in session.query(EmailTemplate).filter_by(is_default=True)] == [
            "새 담당자"
        ]


def test_only_a_signature_can_be_made_the_default(template_db):
    """The other rows are code references. Stamping a draft with the reply-format row
    would put the model's own instructions in front of a customer."""
    with template_db() as session:
        session.add(
            EmailTemplate(key="reply_format", name="답변 메일 형식", language="all",
                          channel="email", status="active", version=1, body="1) 인사")
        )
        session.commit()
        tpl_id = session.query(EmailTemplate).one().id

    with TestClient(app) as client:
        assert client.post(f"/email-templates/{tpl_id}/default").status_code == 400
    with template_db() as session:
        assert session.query(EmailTemplate).one().is_default is False


def test_a_signature_typed_before_the_prefix_existed_is_re_keyed(tmp_path):
    """The key used to be typed by hand, so a signature written then has no prefix — and
    BOTH lookups that find signatures filter on that prefix. The row was invisible to the
    compose screen's picker and to the default, i.e. a signature nothing could use, while
    the console filed it under 이메일 템플릿 because the group is derived from the key too.

    Rows the code resolves by exact name are left alone; everything else could only have
    come from 새로 만들기, which creates signatures.
    """
    import importlib.util

    from sqlalchemy import create_engine, text

    spec = importlib.util.spec_from_file_location(
        "m0048", "src/db/migrations/0048_console_signatures_get_the_prefix.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for key, name in (
            ("baeuntae", "배운태"),          # typed by hand before 08-04
            ("auto_ack", "자동 접수확인"),     # resolved by exact key
            ("reply_format", "답변 메일 형식"),
            (f"{SIGNATURE_KEY_PREFIX}hyeram", "이혜람"),
        ):
            conn.execute(
                text(
                    "INSERT INTO email_templates (key, name, language, channel, status, "
                    "version, body, is_default, created_at, updated_at) "
                    "VALUES (:k, :n, 'all', 'email', 'active', 1, '', 0, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"k": key, "n": name},
            )

    module.up(engine)

    with engine.begin() as conn:
        keys = {
            row[1]: row[0]
            for row in conn.execute(text("SELECT key, name FROM email_templates")).fetchall()
        }
    assert keys["배운태"] == f"{SIGNATURE_KEY_PREFIX}baeuntae"
    assert keys["자동 접수확인"] == "auto_ack"
    assert keys["답변 메일 형식"] == "reply_format"
    assert keys["이혜람"] == f"{SIGNATURE_KEY_PREFIX}hyeram"


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


def test_the_last_row_for_a_key_the_code_resolves_cannot_be_deleted(template_db):
    """지우면 기능이 없어지는 게 아니라, 여전히 일어나는 lookup 의 답이 없어집니다 —
    하드코딩된 문장으로 조용히 떨어지는 접수확인, 또는 {{MEETING_LINK}} 로 끝나는 회신."""
    with template_db() as session:
        session.add(
            EmailTemplate(key="meeting_link", name="미팅 예약 링크", language="all",
                          channel="email", status="active", version=1, body="https://cal")
        )
        session.commit()
        tpl_id = session.query(EmailTemplate).one().id

    with TestClient(app) as client:
        response = client.delete(f"/email-templates/{tpl_id}")
    assert response.status_code == 400
    with template_db() as session:
        assert session.query(EmailTemplate).count() == 1


def test_only_a_signature_deletes(template_db):
    """Every other row is a key the code resolves by name — auto_ack_en and sender_name_en
    included. Deleting one removes the answer to a lookup that still happens, and nothing
    could put it back: the console creates signatures, not code references. 안 쓰려면
    본문을 비웁니다 — 그건 보이고 되돌릴 수 있습니다."""
    with template_db() as session:
        session.add_all([
            EmailTemplate(key="auto_ack_en", name="접수확인 영어", language="en",
                          channel="email", status="active", version=1, body="Hi"),
            EmailTemplate(key=f"{SIGNATURE_KEY_PREFIX}x", name="서명", language="all",
                          channel="email", status="active", version=1, body="<p/>"),
        ])
        session.commit()
        ids = {row.name: row.id for row in session.query(EmailTemplate).all()}

    with TestClient(app) as client:
        assert client.delete(f"/email-templates/{ids['접수확인 영어']}").status_code == 400
        assert client.delete(f"/email-templates/{ids['서명']}").status_code == 200
    with template_db() as session:
        assert [row.name for row in session.query(EmailTemplate).all()] == ["접수확인 영어"]
