"""이메일 템플릿 — what the screen asks for, and what it must not.

The form had four fields the operator could not act on: a key that is a code reference
(the send path resolves ``auto_ack`` / the reply-format row by exact key, so moving one
silently unhooks a template from the app), a description nothing ever displayed, and a
change memo that only reached a history line. 언어 joined them for signatures in 0061 —
nothing matches a signature to a language, the operator picks one on the draft. What is
left is the name and the body.
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
        # default_signature_key opens its own session — the read side of the same table.
        patch("src.db.email_templates.SessionLocal", factory),
        # 휴지통 비우기도 자기 세션을 엽니다.
        patch("src.db.soft_delete.SessionLocal", factory),
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
            data={"name": "Sales signature", "body": "<p>PERSO</p>"},
        )
    assert response.status_code == 200
    with template_db() as session:
        tpl = session.query(EmailTemplate).one()
        # The prefix is not decoration: the review screen's signature picker is the only
        # thing that can reach a template created here, and it looks for exactly this.
        assert tpl.key == f"{SIGNATURE_KEY_PREFIX}sales_signature"
        assert tpl.name == "Sales signature"


def test_a_korean_name_still_produces_a_usable_key(template_db):
    """It romanizes to nothing, so a stand-in does — the key is never empty. It used to be
    the language; signatures do not have one any more, and the key is never shown."""
    with TestClient(app) as client:
        client.post("/email-templates", data={"name": "기본 서명"})
    with template_db() as session:
        row = session.query(EmailTemplate).one()
        assert row.key == f"{SIGNATURE_KEY_PREFIX}custom"
        # 언어를 묻지 않으므로 서명은 언제나 '전체' 입니다.
        assert row.language == "all"


def test_two_templates_with_the_same_name_do_not_collide(template_db):
    with TestClient(app) as client:
        client.post("/email-templates", data={"name": "기본 서명"})
        client.post("/email-templates", data={"name": "기본 서명"})
    with template_db() as session:
        keys = sorted(row.key for row in session.query(EmailTemplate).all())
    assert keys == [f"{SIGNATURE_KEY_PREFIX}custom", f"{SIGNATURE_KEY_PREFIX}custom_2"]


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


def test_a_new_draft_starts_on_the_first_signature(template_db):
    """"기본 서명" 이라는 저장된 값은 없앴습니다(0060).

    있던 것은 플래그 하나와, 그것이 하나뿐임을 보장하는 인덱스와, 옮기는 버튼과 라우트
    였습니다 — 초안마다 이미 고를 수 있는 값을 위해서요. 규칙은 이제 "목록의 첫 번째" 이고,
    실제로 어느 서명으로 나가는지는 그 건의 검토 화면에서 정합니다.
    """
    from src.db.email_templates import default_signature_key

    with template_db() as session:
        session.add_all([
            EmailTemplate(key=f"{SIGNATURE_KEY_PREFIX}b", name="B 담당자", language="all",
                          channel="email", status="active", version=1, body="<p>b</p>"),
            EmailTemplate(key=f"{SIGNATURE_KEY_PREFIX}a", name="A 담당자", language="all",
                          channel="email", status="active", version=1, body="<p>a</p>"),
        ])
        session.commit()

    assert default_signature_key() == f"{SIGNATURE_KEY_PREFIX}a"


def test_with_no_signatures_a_draft_starts_unsigned(template_db):
    """None 은 이제 정말 "서명 없음" 입니다. 예전에는 회사 규칙이 본문에 넣어 둔 텍스트
    서명을 뜻했고, 그래서 아무것도 안 고른 메일에도 이름이 붙어 나갔습니다 (0061)."""
    from src.db.email_templates import default_signature_key

    assert default_signature_key() is None


def test_a_signature_typed_before_the_prefix_existed_is_re_keyed(tmp_path):
    """The key used to be typed by hand, so a signature written then has no prefix — and
    BOTH lookups that find signatures filter on that prefix. The row was invisible to the
    compose screen's picker and to the default, i.e. a signature nothing could use, while
    the console filed it under 이메일 템플릿 because the group is derived from the key too.

    Rows the code resolves by exact name are left alone; everything else could only have
    come from 새로 만들기, which creates signatures.

    ``signature_html_`` is spelled out rather than taken from the constant: 0048 wrote that
    prefix, and it stays written even though 새로 만들기 has since dropped the ``html_``
    (0061). A migration's assertion has to describe the past, not today's constant.
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
            ("signature_html_hyeram", "이혜람"),
        ):
            conn.execute(
                text(
                    "INSERT INTO email_templates (key, name, language, channel, status, "
                    "version, body, created_at, updated_at) "
                    "VALUES (:k, :n, 'all', 'email', 'active', 1, '', "
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
    assert keys["배운태"] == "signature_html_baeuntae"
    assert keys["자동 접수확인"] == "auto_ack"
    assert keys["답변 메일 형식"] == "reply_format"
    assert keys["이혜람"] == "signature_html_hyeram"


def test_a_signature_cannot_keep_a_language(template_db):
    """화면이 안 묻는데 폼이 옛 값을 실어 보내면, 아무도 못 보고 못 바꾸는 값이 되살아납니다.
    ``auto_ack`` 은 다릅니다 — 그건 정말 한 메일의 두 언어입니다."""
    with template_db() as session:
        session.add_all([
            EmailTemplate(key="signature_ko", name="서명", language="ko", channel="email",
                          status="active", version=1, body="김규원"),
            EmailTemplate(key="auto_ack_en", name="접수확인 영어", language="en",
                          channel="email", status="active", version=1, body="Hi"),
        ])
        session.commit()
        ids = {row.key: row.id for row in session.query(EmailTemplate).all()}

    with TestClient(app) as client:
        client.put(f"/email-templates/{ids['signature_ko']}",
                   data={"name": "서명", "language": "ko", "body": "김규원"})
        client.put(f"/email-templates/{ids['auto_ack_en']}",
                   data={"name": "접수확인 영어", "language": "en", "body": "Hi"})
    with template_db() as session:
        langs = {row.key: row.language for row in session.query(EmailTemplate).all()}
    assert langs == {"signature_ko": "all", "auto_ack_en": "en"}


def test_a_signature_never_groups_under_another_row(template_db):
    """언어별 묶음은 ``auto_ack`` / ``auto_ack_en`` 을 한 줄로 보이게 하는 장치입니다.
    서명 둘은 그냥 서명 둘이고, 묶으면 '전체' 라고 적힌 언어 칩이 두 개 뜹니다."""
    from src.api.routes.ui_api import _base_key

    keys = {"signature_x", "signature_x_en", "auto_ack", "auto_ack_en"}
    assert _base_key("signature_x_en", keys) == "signature_x_en"
    assert _base_key("auto_ack_en", keys) == "auto_ack"


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


def test_every_signature_deletes_and_nothing_else_does(template_db):
    """Every other row is a key the code resolves by name — auto_ack_en and sender_name_en
    included. Deleting one removes the answer to a lookup that still happens, and nothing
    could put it back: the console creates signatures, not code references. 안 쓰려면
    본문을 비웁니다 — 그건 보이고 되돌릴 수 있습니다.

    ``signature_ko`` is in here on purpose. It WAS such a key — the prompt injected it into
    every draft — so the screen showed it under 서명 and then refused to delete it, which
    is the worst of both. Nothing reads it now, so it goes (0061)."""
    with template_db() as session:
        session.add_all([
            EmailTemplate(key="auto_ack_en", name="접수확인 영어", language="en",
                          channel="email", status="active", version=1, body="Hi"),
            EmailTemplate(key="signature_ko", name="서명 (한국어)", language="ko",
                          channel="email", status="active", version=1, body="김규원"),
            EmailTemplate(key=f"{SIGNATURE_KEY_PREFIX}x", name="서명", language="all",
                          channel="email", status="active", version=1, body="<p/>"),
        ])
        session.commit()
        ids = {row.name: row.id for row in session.query(EmailTemplate).all()}

    with TestClient(app) as client:
        assert client.delete(f"/email-templates/{ids['접수확인 영어']}").status_code == 400
        assert client.delete(f"/email-templates/{ids['서명 (한국어)']}").status_code == 200
        assert client.delete(f"/email-templates/{ids['서명']}").status_code == 200
    with template_db() as session:
        # 행은 남습니다 — 지운 것은 일주일 동안 되돌릴 수 있습니다(0070). 발송 경로가
        # 보는 것은 status 이고, 읽는 쪽은 전부 이미 'active' 만 봅니다.
        alive = [row.name for row in session.query(EmailTemplate)
                 if row.status == "active"]
        assert alive == ["접수확인 영어"]
        gone = session.query(EmailTemplate).filter_by(status="deleted").all()
        assert {row.name for row in gone} == {"서명 (한국어)", "서명"}
        assert all(row.deleted_at is not None for row in gone)


def test_a_deleted_signature_is_gone_from_the_send_path_at_once(template_db):
    """되돌릴 수 있다는 것이 아직 쓰인다는 뜻이면 안 됩니다. 지운 서명이 검토 화면의
    고르개에 남아 있으면, 지웠다고 생각한 서명으로 메일이 나갑니다."""
    from src.db.email_templates import list_signature_templates

    with template_db() as session:
        session.add(EmailTemplate(key=f"{SIGNATURE_KEY_PREFIX}y", name="지울 서명",
                                  language="all", channel="email", status="active",
                                  version=1, body="<p/>"))
        session.commit()
        tpl_id = session.query(EmailTemplate).filter_by(name="지울 서명").one().id

    assert "지울 서명" in {s["name"] for s in list_signature_templates()}
    with TestClient(app) as client:
        assert client.delete(f"/email-templates/{tpl_id}").status_code == 200
        assert "지울 서명" not in {s["name"] for s in list_signature_templates()}
        # 되돌리면 그대로 돌아옵니다.
        assert client.post(f"/email-templates/{tpl_id}/restore").status_code == 200
    assert "지울 서명" in {s["name"] for s in list_signature_templates()}


def test_the_bin_empties_after_a_week(template_db):
    """일주일이 지나면 진짜로 사라집니다 — 목록을 읽을 때 청소합니다."""
    from datetime import timedelta

    from src.db.soft_delete import RETENTION_DAYS, days_left, purge_expired, utcnow

    with template_db() as session:
        session.add_all([
            EmailTemplate(key=f"{SIGNATURE_KEY_PREFIX}fresh", name="어제 지움", language="all",
                          channel="email", status="deleted", version=1, body="a",
                          deleted_at=utcnow() - timedelta(days=1)),
            EmailTemplate(key=f"{SIGNATURE_KEY_PREFIX}stale", name="열흘 전 지움", language="all",
                          channel="email", status="deleted", version=1, body="b",
                          deleted_at=utcnow() - timedelta(days=RETENTION_DAYS + 3)),
        ])
        session.commit()

    assert purge_expired() == 1
    with template_db() as session:
        assert [row.name for row in session.query(EmailTemplate)] == ["어제 지움"]
        # 「N일 후 완전 삭제」는 올림입니다. 반나절 남은 것을 0일이라 쓰면 이미 지난
        # 것처럼 읽히는데, 그 사이에도 되돌릴 수 있습니다.
        assert days_left(session.query(EmailTemplate).one().deleted_at) == RETENTION_DAYS - 1


def test_deleting_needs_the_sentence_typed_out_not_a_click():
    """확인이 클릭 한 번이면 두 번째부터는 읽지 않습니다 — 손이 기억하는 동작이 됩니다.
    문장을 옮겨 적는 동안에는 무엇을 지우는지 읽게 됩니다.

    화면 소스로 확인합니다. 이 규칙이 사는 곳은 서버가 아니라 이 창 하나이고(서버는 되돌릴
    수 있게 만드는 쪽을 맡습니다), 그래서 창이 사라지면 규칙도 같이 사라집니다.
    """
    dialog = pathlib.Path("frontend/src/ui/DeleteDialog.tsx").read_text(encoding="utf-8")
    assert 'DELETE_PHRASE = "이 문서를 삭제하겠습니다."' in dialog
    assert "typed.trim() === DELETE_PHRASE" in dialog
    assert "disabled={!ok}" in dialog, "문장이 맞기 전에는 삭제 버튼이 눌리면 안 됩니다"

    # 그리고 지우는 화면 둘 다 그 창을 지납니다 — 한쪽만 지나면 나머지가 옛날 그대로입니다.
    for screen in ("frontend/src/screens/EmailTemplates.tsx",
                   "frontend/src/screens/PolicyDocs.tsx"):
        source = pathlib.Path(screen).read_text(encoding="utf-8")
        assert "DeleteDialog" in source, screen
        # 휴지통은 오른쪽 끝. 저장 옆에 나란히 두면 둘이 같은 무게로 보입니다.
        assert 'className="action-bar row-between"' in source, screen
        assert 'name="trash"' in source, screen
