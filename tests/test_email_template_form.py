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

from tests.conftest import legacy_template_columns
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
        # 목록을 그리는 /api/ui/email-templates 는 세션을 직접 엽니다.
        patch("src.db.session.SessionLocal", factory),
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
                description="자동 접수확인 본문",
                version=1,
                body="안녕하세요")
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
            EmailTemplate(key=f"{SIGNATURE_KEY_PREFIX}b", name="B 담당자", language="all", version=1, body="<p>b</p>"),
            EmailTemplate(key=f"{SIGNATURE_KEY_PREFIX}a", name="A 담당자", language="all", version=1, body="<p>a</p>"),
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
    # 0048 이 살던 시절의 표에는 ``channel`` 이 있었습니다 — 0019 가 만들고 0100 이
    # 지운 칸입니다. 그 시절 동작을 재려면 그 시절 표여야 합니다.
    legacy_template_columns(engine)
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
            EmailTemplate(key="signature_ko", name="서명", language="ko", version=1, body="김규원"),
            EmailTemplate(key="auto_ack_en", name="접수확인 영어", language="en", version=1, body="Hi"),
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


def test_removed_auto_ack_rows_are_hidden_from_the_console(template_db):
    """묶지 않습니다 — 목록에 그린 줄 수와 카드의 숫자가 같아야 합니다.

    ``auto_ack_en`` 을 ``auto_ack`` 아래로 접어 두었더니 11개 행이 6줄로 그려지고 숫자만
    11로 떴습니다. 그리고 접힌 다섯 줄이 하필 **영문 문의가 읽는 유일한 행들**입니다:
    운영자는 「전체」라고 적힌 국문 행을 고치고, 영문 회신은 손대지 않은 ``_en`` 행을 계속
    읽었습니다. 화면에는 그럴 이유가 하나도 안 보였습니다.
    """
    with template_db() as session:
        session.add_all([
            EmailTemplate(key="auto_ack", name="접수확인", language="ko", version=1, body="안녕하세요"),
            EmailTemplate(key="auto_ack_en", name="접수확인", language="en", version=1, body="Hi"),
        ])
        session.commit()

    with TestClient(app) as client:
        payload = client.get("/api/ui/email-templates").json()

    keys = [item["key"] for item in payload["items"] if item["kind"] == "template"]
    assert keys == []
    count = next(k["count"] for k in payload["kinds"] if k["key"] == "template")
    assert count == len(keys)
    # 접는 장치가 돌아오면 이 필드가 먼저 돌아옵니다.
    assert "base_key" not in FORM.read_text(encoding="utf-8")


def test_saving_does_not_relabel_the_row_language(template_db):
    """콘솔에는 언어를 고르는 칸이 없습니다 (0063). 그러니 저장이 언어를 바꿀 수 없습니다.

    라우트가 ``language`` 의 기본값을 ``"all"`` 로 두던 동안, 0074 가 국문 행이라고 표시해 둔
    ``reply_format`` · ``meeting_link`` · ``whatsapp_link`` 는 콘솔에서 한 번 저장할 때마다
    조용히 '전체' 로 돌아갔습니다. 그 한 글자가 영문 회신이 ``_en`` 행을 읽는다는 것을
    화면에서 말해 주는 유일한 자리입니다.
    """
    with template_db() as session:
        session.add(
            EmailTemplate(key="reply_format", name="답변 메일 형식", language="ko", version=1, body="형식")
        )
        session.commit()
        tpl_id = session.query(EmailTemplate).one().id

    with TestClient(app) as client:
        # 화면이 보내는 그대로 — 언어 칸이 없으므로 언어도 없습니다.
        response = client.put(f"/email-templates/{tpl_id}",
                              data={"name": "답변 메일 형식", "body": "고친 형식"})
    assert response.status_code == 200
    with template_db() as session:
        row = session.query(EmailTemplate).one()
    assert row.body == "고친 형식"
    assert row.language == "ko"


def test_saving_always_leaves_a_live_row(template_db):
    """상태 칸이 여기 있었습니다 — 「초안/보관」 같은 값을 쓸 방법이 없어서 언제나
    ``active`` 였고, 0100 이 삭제를 하드 삭제로 바꾸면서 뜻을 완전히 잃었습니다(0101).
    이제 **표에 있는 행이 곧 살아 있는 행**입니다."""
    from src.db.email_templates import get_email_template
    from src.db.models import EmailTemplate as _T

    with TestClient(app) as client:
        assert client.post("/email-templates", data={
            "name": "새 서식", "key": "custom_note", "body": "본문", "language": "ko",
        }).status_code == 200

    assert get_email_template("custom_note") == "본문"
    with template_db() as session:
        assert not hasattr(session.query(_T).one(), "status")

def test_anything_deletes_now_and_the_screen_says_what_that_costs(template_db):
    """자유 삭제입니다 (2026-08-18, 운영자 결정) — 막는 대신 **말합니다.**

    ``signature_`` 로 시작하는 행만 지울 수 있었습니다. 나머지는 코드가 이름으로 찾는 행이라
    지우면 기능이 없어지는 것이 아니라 여전히 일어나는 **조회의 답**이 없어지기 때문입니다.
    그 대가를 알고 자유롭게 하기로 했으므로, 이제 유일한 방어선은 그 행이 어떤 행인지 화면이
    말해 주는 것입니다: 목록의 「발송 경로 사용」 표와 삭제 확인 창의 빨간 문장이 둘 다
    ``is_code_resolved`` 한 곳에서 나옵니다.
    """
    with template_db() as session:
        session.add_all([
            EmailTemplate(key="meeting_link", name="미팅 예약 링크", language="ko", version=1, body="https://cal"),
            EmailTemplate(key=f"{SIGNATURE_KEY_PREFIX}x", name="서명", language="all", version=1, body="<p/>"),
            EmailTemplate(key="my_own_note", name="내 메모", language="all", version=1, body="메모"),
        ])
        session.commit()
        ids = {row.key: row.id for row in session.query(EmailTemplate).all()}

    with TestClient(app) as client:
        for key in ids:
            assert client.delete(f"/email-templates/{ids[key]}").status_code == 200, key
        payload = client.get("/api/ui/email-templates").json()

    # **행이 사라집니다** (0100). 소프트 삭제로 두면 지운 행이 unique 한 `key` 를 영원히
    # 붙들고 있어서, 발송 경로가 찾는 이름을 다시는 못 만듭니다. 지운 내용은 판본 이력에
    # `change_note='deleted'` 로 남습니다.
    with template_db() as session:
        assert session.query(EmailTemplate).count() == 0

    # 그리고 화면은 어느 것이 발송 경로가 쓰던 행이었는지 말할 수 있어야 합니다.
    # 지운 행은 목록에 안 옵니다 — 행 자체가 없습니다.
    assert payload["items"] == []
    # 그리고 **같은 키로 다시 만들 수 있어야 합니다** — 그게 하드 삭제의 요점입니다.
    with template_db() as session:
        session.add(EmailTemplate(key="meeting_link", name="다시 만든 링크", language="ko", version=1, body="https://cal"))
        session.commit()


def test_is_code_resolved_rejects_removed_auto_ack_keys(template_db):
    """이름 목록만으로는 부족합니다. 접수확인은 언어마다 한 행이고(`auto_ack_ja` 를 만들면
    그 언어 문의가 실제로 읽습니다), 서명은 접두사로 훑습니다. 그리고 `auto_ack_footer` 는
    접수확인의 언어판이 아니라 로고 한 줄이라 두 글자로 못 박습니다."""
    from src.db.email_templates import is_code_resolved

    assert not is_code_resolved("auto_ack")
    assert not is_code_resolved("auto_ack_ja")
    assert not is_code_resolved("auto_ack_footer")
    assert is_code_resolved("signature_anything")
    assert not is_code_resolved("my_own_note")
    assert not is_code_resolved("auto_ack_japanese")


def test_auto_ack_keys_cannot_be_recreated(template_db):
    """키를 적으면 그대로, 비우면 서명. 발송 경로가 이름으로 꺼내 가므로, 콘솔이 키를 만들어
    주기만 하던 동안에는 `auto_ack_ja` 처럼 **읽히는데 만들 수는 없는** 행이 있었습니다."""
    with TestClient(app) as client:
        assert client.post("/email-templates",
                           data={"name": "일본어 접수확인", "key": "auto_ack_ja",
                                 "language": "ja", "body": "こんにちは"}).status_code == 400
        assert client.post("/email-templates",
                           data={"name": "커스텀 후속", "key": "custom_followup",
                                 "language": "ja", "body": "こんにちは"}).status_code == 200
        assert client.post("/email-templates",
                           data={"name": "새 서명", "body": "김규원"}).status_code == 200
        # 같은 키는 두 번 만들 수 없습니다 — 한 문의가 어느 행을 읽을지 정해지지 않습니다.
        assert client.post("/email-templates",
                           data={"name": "또", "key": "custom_followup"}).status_code == 400
        # 대문자·공백은 눈으로는 같은데 조회에는 안 걸리는 이름을 만듭니다.
        assert client.post("/email-templates",
                           data={"name": "또", "key": "Auto Ack"}).status_code == 400

    with template_db() as session:
        rows = {row.key: row.language for row in session.query(EmailTemplate).all()}
    assert rows["custom_followup"] == "ja"
    # 비우고 만든 것은 서명이고, 서명에는 언어가 없습니다 (0063).
    signature = next(k for k in rows if k.startswith(SIGNATURE_KEY_PREFIX))
    assert rows[signature] == "all"


def test_a_deleted_signature_is_gone_from_the_send_path_at_once(template_db):
    """DB 에 남는다는 것이 아직 쓰인다는 뜻이면 안 됩니다. 지운 서명이 검토 화면의
    고르개에 남아 있으면, 지웠다고 생각한 서명으로 메일이 나갑니다."""
    from src.db.email_templates import list_signature_templates

    with template_db() as session:
        session.add(EmailTemplate(key=f"{SIGNATURE_KEY_PREFIX}y", name="지울 서명",
                                  language="all",
                                  version=1, body="<p/>"))
        session.commit()
        tpl_id = session.query(EmailTemplate).filter_by(name="지울 서명").one().id

    assert "지울 서명" in {s["name"] for s in list_signature_templates()}
    with TestClient(app) as client:
        assert client.delete(f"/email-templates/{tpl_id}").status_code == 200
    assert "지울 서명" not in {s["name"] for s in list_signature_templates()}
    # 행은 사라지고, 그때 내용은 판본 이력에 남습니다 — 그것이 「DB 에서 볼 수 있게」입니다.
    from src.db.models import DocumentRevision

    with template_db() as session:
        assert session.get(EmailTemplate, tpl_id) is None
        gone = session.query(DocumentRevision).filter_by(change_note="deleted").one()
        assert gone.title == "지울 서명" and gone.body == "<p/>"


def test_a_deleted_row_frees_its_key_and_leaves_its_content_in_history(template_db):
    """**지우면 행이 사라지고, 내용은 판본 이력에 남습니다** (0100).

    한동안 ``status='deleted'`` 로만 바꿨습니다. 그런데 ``key`` 가 unique 이고 만들기
    라우트가 상태를 안 보고 중복을 막기 때문에, 청소를 없앤 뒤로는 **지운 행이 그 이름을
    영원히 붙들고** 있었습니다 — `reply_format` 을 한 번 지우면 다시는 그 이름으로 만들 수
    없고, 그 이름은 발송 경로가 찾는 이름입니다.
    """
    import src.db as db_pkg
    from src.db.models import DocumentRevision
    from src.db.email_templates import get_email_template

    with template_db() as session:
        session.add(EmailTemplate(key="reply_format", name="서식", language="ko", version=4, body="옛 서식"))
        session.commit()
        tpl_id = session.query(EmailTemplate).one().id

    with TestClient(app) as client:
        assert client.delete(f"/email-templates/{tpl_id}").status_code == 200
        # 같은 키로 다시 만들 수 있습니다 — 이것이 하드 삭제의 요점입니다.
        assert client.post("/email-templates", data={
            "name": "새 서식", "key": "reply_format", "body": "새 본문", "language": "ko",
        }).status_code == 200

    assert get_email_template("reply_format") == "새 본문"
    with template_db() as session:
        assert session.query(EmailTemplate).count() == 1
        # 지운 것의 내용은 이력에 그대로 있습니다.
        gone = session.query(DocumentRevision).filter_by(change_note="deleted").one()
        assert gone.body == "옛 서식" and gone.version == 4

    # 7일 휴지통은 없습니다 — 모듈째로.
    assert not hasattr(db_pkg, "soft_delete")
    import importlib

    try:
        importlib.import_module("src.db.soft_delete")
        raise AssertionError("soft_delete 모듈이 아직 있습니다")
    except ModuleNotFoundError:
        pass


def test_the_revision_history_is_out_of_gemini_reach():
    """판본 이력은 **초안이 절대 안 보는 표**입니다 (2026-08-27 운영자 지시:
    「gemini 가 답변 쓸 때 이 히스토리 쪽은 절대 참고하면 안 됨」).

    구조로 보장합니다 — 모델을 부르는 코드(``src/llm``)도, 그것을 부르는 에이전트
    (``src/agents``)도 그 표를 이름으로조차 알지 못합니다. 아는 것은 콘솔 라우트뿐입니다.
    """
    import pathlib

    for folder in ("src/llm", "src/agents"):
        for path in pathlib.Path(folder).rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert "DocumentRevision" not in source, path
            assert "document_revisions" not in source, path


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


# ---- 고치는 길은 전부 판본을 남긴다 ------------------------------------------------


def test_every_write_path_leaves_the_value_before_it(template_db):
    """**서명도 갑니다.** 서명은 별도 표가 아니라 ``signature_`` 로 시작하는 이메일 템플릿
    행이고, 화면도 라우트도 같습니다 — 그래서 같은 스냅샷을 지납니다.

    수정 · 삭제 · 되돌리기 셋 다 남기고, **덮어쓰지 않습니다**(append-only). 만들 때만
    안 남깁니다: 이 표가 들고 있는 것은 「바꾸기 **직전** 값」인데 갓 만든 행에는 직전이
    없습니다. 남기면 첫 수정 스냅샷과 같은 버전·같은 본문이 두 줄로 섭니다.
    """
    from src.db.models import DocumentRevision
    from src.db.revisions import EMAIL_TEMPLATE

    key = f"{SIGNATURE_KEY_PREFIX}untae"
    with template_db() as session:
        session.add(EmailTemplate(key=key, name="서명", language="all", version=1, body="처음 서명"))
        session.commit()
        tpl_id = session.query(EmailTemplate).one().id

    with TestClient(app) as client:
        # 만들기는 위에서 직접 했으므로, 여기서는 고치는 길 둘만 지납니다.
        client.put(f"/email-templates/{tpl_id}", data={"name": "서명", "body": "두 번째 서명"})
        client.put(f"/email-templates/{tpl_id}", data={"name": "서명", "body": "세 번째 서명"})
        client.delete(f"/email-templates/{tpl_id}")

    with template_db() as session:
        rows = session.query(DocumentRevision).order_by(DocumentRevision.id).all()
        assert [r.change_note for r in rows] == ["edited", "edited", "deleted"]
        assert {r.kind for r in rows} == {EMAIL_TEMPLATE}
        # 「바꾸기 **직전** 값」이 규칙이고 예외가 없습니다.
        assert [r.body for r in rows] == ["처음 서명", "두 번째 서명", "세 번째 서명"]
        # append-only: 이력이 서로를 덮어쓰지 않았습니다. 행 자체는 삭제로 사라졌고,
        # 마지막 스냅샷이 그때 본문을 들고 있습니다.
        assert session.get(EmailTemplate, tpl_id) is None


def test_the_language_is_editable_and_the_other_one_is_not_english(template_db):
    """**「영어」가 아니라 「외국어」입니다** (2026-08-27 운영자 지시, 이관 0099).

    ``_en`` 행을 고르는 조건이 「영어인가」가 아니라 「한국어가 **아닌가**」입니다
    (``prompts.get_reply_format``) — 일본어 문의도 베트남어 문의도 그 행을 읽습니다.
    화면에 「영어」라고 적혀 있으면 그 행을 영어 전용으로 읽게 되고, 무엇이 실제로 그
    행을 읽는지와 어긋납니다.

    그리고 **언제든 고칠 수 있습니다.** 만들 때만 묻고 그 뒤로 못 바꾸면, 잘못 고른
    행을 지우고 다시 만드는 수밖에 없는데 키가 코드 참조라 그럴 수도 없습니다.
    """
    with template_db() as session:
        session.add(EmailTemplate(key="reply_format_en", name="답변 메일 형식 (외국어)",
                                  language="ko",
                                  version=1, body="뼈대"))
        session.commit()
        tpl_id = session.query(EmailTemplate).one().id

    with TestClient(app) as client:
        response = client.put(f"/email-templates/{tpl_id}",
                              data={"name": "답변 메일 형식 (외국어)", "language": "foreign",
                                    "body": "뼈대"})
        assert response.status_code == 200, response.text

    with template_db() as session:
        assert session.get(EmailTemplate, tpl_id).language == "foreign"


def test_a_signature_still_cannot_keep_a_language(template_db):
    """어떤 코드도 언어로 서명을 고르지 않습니다 — 고르는 것은 사람입니다(0063)."""
    with template_db() as session:
        session.add(EmailTemplate(key=f"{SIGNATURE_KEY_PREFIX}z", name="서명", language="all", version=1, body="<p/>"))
        session.commit()
        tpl_id = session.query(EmailTemplate).one().id

    with TestClient(app) as client:
        client.put(f"/email-templates/{tpl_id}",
                   data={"name": "서명", "language": "foreign", "body": "<p/>"})

    with template_db() as session:
        assert session.get(EmailTemplate, tpl_id).language == "all"
