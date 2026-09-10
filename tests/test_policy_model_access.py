"""「사람만 본다」 문서는 어떤 모델 호출에도 안 들어간다 (이관 0119).

**왜 이 파일이 있나.** 운영 문서 `Perso Dubbing Enterprise 판매 단가` 가 「항상 적용」이라
원가·이익률·최소 연 약정액·승인 하한이 **모든 Gemini 호출의 시스템 프롬프트**에 들어가고
있었다. 같은 세트의 `00_README` 기밀 등급표와 `02_금지어` §A·§D 가 그것을 전부 대외
금지로 못박는데도 그랬고, 금액 가드는 첫 회신에만 돌아서 막지도 못했다.

**거는 자리가 넷이고 전부 쿼리다.** 코드 흐름으로 지키면 다음 호출자가 그 앞을 안 지난다.
그래서 이 테스트도 자리마다 하나씩 있다.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.main import app
from src.db.base import Base
from src.db.models import PolicySource


@pytest.fixture()
def policy_db():
    """네 자리가 각자 자기 세션을 열므로 넷 다 같은 엔진으로 돌려 놓는다."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with (
        patch("src.api.routes.policy_docs.SessionLocal", factory),
        # `prompts._rules_from_db` 는 함수 안에서 늦게 import 합니다 — 원본을 갈아야
        # 그 자리가 따라옵니다.
        patch("src.db.session.SessionLocal", factory),
        patch("src.llm.knowledge.SessionLocal", factory),
    ):
        yield factory


def _doc(session, *, label, mode, body, access="customer_context", scope="all"):
    row = PolicySource(
        label=label,
        title=label,
        doc_key=label,
        mode=mode,
        scope=scope,
        body=body,
        model_access=access,
    )
    session.add(row)
    session.commit()
    return row.id


def test_a_people_only_document_never_reaches_the_company_rules(policy_db):
    """「모든 회신에 적용」이어도 사람용이면 프롬프트에 안 들어간다."""
    from src.llm.prompts import _rules_from_db

    with policy_db() as session:
        _doc(session, label="공개 규칙", mode="rules", body="이 문장은 나가도 된다.")
        _doc(
            session,
            label="내부 단가",
            mode="rules",
            # 실제 원가·마진은 여기 안 적는다 — 이 파일도 저장소에 남는다.
            body="내부 원가 $9.99/분 · 이익률 42%",
            access="human_only",
        )

    rules = _rules_from_db()
    assert "이 문장은 나가도 된다." in rules
    assert "원가" not in rules and "42%" not in rules


def test_a_people_only_document_never_reaches_the_router_index(policy_db):
    """라우터 후보에도 안 뜬다 — 인덱스는 제목과 요약뿐이지만 그것도 그 문서의 내용이다."""
    from src.llm.knowledge import FIRST, router_docs

    with policy_db() as session:
        _doc(session, label="공개 참고", mode="knowledge", body="본문")
        _doc(session, label="내부 참고", mode="knowledge", body="본문", access="human_only")

    titles = {doc.label for doc in router_docs(FIRST)}
    assert titles == {"공개 참고"}


def test_saving_a_people_only_document_does_not_send_the_body_to_a_model(policy_db, monkeypatch):
    """저장 경로가 마지막 구멍이었다.

    쿼리 필터와 system 미주입만으로는 **이 경로를 못 막는다** — 저장할 때 「언제 쓰는가」를
    만들려고 본문을 그대로 모델에 넘기기 때문이다. 그 호출은 `mode` 판정보다도 **먼저**
    일어나고 있었다.
    """
    calls: list[str] = []

    def _spy(title, body, llm=None):
        calls.append(body)
        return "쓰는 경우: …"

    monkeypatch.setattr("src.api.routes.policy_docs.usage_note_from_body", _spy)

    with policy_db() as session:
        human = _doc(
            session, label="내부 단가", mode="knowledge", body="옛 본문", access="human_only"
        )
        shared = _doc(session, label="공개 참고", mode="knowledge", body="옛 본문")

    with TestClient(app) as client:
        assert client.put(
            f"/policy-docs/{human}", data={"label": "내부 단가", "body": "내부 원가 $9.99"}
        ).status_code == 200
        assert calls == [], "사람용 문서의 본문이 모델로 갔습니다"

        assert client.put(
            f"/policy-docs/{shared}", data={"label": "공개 참고", "body": "새 본문"}
        ).status_code == 200
    assert calls == ["새 본문"]


def test_the_usage_note_is_only_made_for_documents_the_router_reads(policy_db, monkeypatch):
    """읽는 곳이 없으면 안 만든다.

    이 한 줄을 읽는 것은 라우터 인덱스뿐이고(`knowledge._build_index`) 라우터는
    `mode='knowledge'` 행만 본다. 「항상 적용」 문서에 대해 부르면 저장할 때마다 flash 를
    한 번 태우고 아무도 그 값을 안 읽는다.
    """
    calls: list[str] = []
    monkeypatch.setattr(
        "src.api.routes.policy_docs.usage_note_from_body",
        lambda title, body, llm=None: calls.append(body) or "한 줄",
    )

    with TestClient(app) as client:
        assert client.post(
            "/policy-docs", data={"label": "항상 규칙", "body": "본문", "mode": "rules"}
        ).status_code in (200, 303)
        assert calls == []

        assert client.post(
            "/policy-docs", data={"label": "문의별 참고", "body": "본문", "mode": "knowledge"}
        ).status_code in (200, 303)
    assert calls == ["본문"]


def test_the_scope_actually_decides_which_reply_gets_the_rules(policy_db):
    """**이 필터가 없던 동안 화면은 거짓말을 하고 있었습니다.**

    「첫 회신에만」과 「그 이후 회신에」를 고르게 해 놓고 `_rules_from_db` 는 `scope` 를
    안 봤습니다 — 고른 것이 아무 일도 안 하는데 화면에는 저장됐다고 보입니다. 화면을
    로더보다 먼저 내놓아서 생긴 일이고, 외부 검토가 재현해 알려 줬습니다.
    """
    from src.llm.prompts import _rules_from_db

    with policy_db() as session:
        _doc(session, label="공통", mode="rules", body="언제나 이 규칙.")
        _doc(session, label="첫 회신", mode="rules", body="처음에만 이 규칙.", scope="first")
        _doc(session, label="후속", mode="rules", body="그 뒤에만 이 규칙.", scope="followup")

    first = _rules_from_db("first")
    assert "언제나" in first and "처음에만" in first and "그 뒤에만" not in first

    later = _rules_from_db("followup")
    assert "언제나" in later and "그 뒤에만" in later and "처음에만" not in later

    # 유틸리티(분류·번역·요약)는 「모두」만 본다.
    util = _rules_from_db()
    assert "언제나" in util and "처음에만" not in util and "그 뒤에만" not in util


def test_an_unknown_stage_is_refused_rather_than_widened(policy_db):
    """넓히는 쪽으로 틀리면 사람용 문서가 새고, 그건 화면 어디에도 안 보인다.

    `router_docs` 가 모르는 stage 에서 **필터를 통째로 생략**하고 있었다 — `scope` 로
    「사람만 본다」를 막으면 안 되는 이유가 바로 그 자리였다.
    """
    import pytest as _pytest

    from src.llm.knowledge import router_docs, scopes_for_stage
    from src.llm.prompts import _rules_from_db

    assert scopes_for_stage(None) == ("all",)
    for call in (lambda: scopes_for_stage("second"),
                 lambda: router_docs("second"),
                 lambda: _rules_from_db("second")):
        with _pytest.raises(ValueError):
            call()


def test_editing_only_the_title_keeps_a_follow_up_only_document_out_of_the_first_reply(
    policy_db, monkeypatch
):
    """**제목 한 번 고친 것으로 후속 전용 문서가 첫 회신 후보가 됐습니다.**

    다섯 값 고르개가 `knowledge/followup` 을 표현할 수 없어서 `placement_of` 가 그것을
    `"knowledge"` 라고 답했고, 화면이 그 값을 담아 두었다가 제목만 고친 저장에 같이
    보냈고, 서버가 `knowledge/all` 로 적었습니다. 세 자리가 각자 맞는데 이어 붙이니
    틀린 자리입니다 — 외부 검토가 재현해 알려 줬습니다.
    """
    monkeypatch.setattr(
        "src.api.routes.policy_docs.usage_note_from_body",
        lambda title, body, llm=None: "",
    )
    from src.llm.knowledge import FIRST, FOLLOWUP, router_docs

    with policy_db() as session:
        doc = _doc(session, label="깊은 참고", mode="knowledge", body="본문", scope="followup")

    assert [d.label for d in router_docs(FOLLOWUP)] == ["깊은 참고"]
    assert router_docs(FIRST) == []

    # 화면이 제목만 고쳐 저장한다 — `placement` 는 안 보낸다(바뀐 것이 없으므로).
    with TestClient(app) as client:
        assert client.put(
            f"/policy-docs/{doc}", data={"label": "깊은 참고 (개정)", "body": "본문"}
        ).status_code == 200

    assert router_docs(FIRST) == [], "제목만 고쳤는데 첫 회신 후보가 됐습니다"
    assert [d.label for d in router_docs(FOLLOWUP)] == ["깊은 참고 (개정)"]


def test_a_placement_round_trip_never_moves_a_document():
    """**화면이 무엇을 보내든 서버가 안전해야 합니다.**

    옛 버그는 세 자리의 합이었습니다 — `placement_of` 가 `knowledge/followup` 에
    `"knowledge"` 라는 이름을 지어 줬고, 화면이 그것을 담아 두었다가 제목만 고친 저장에
    같이 보냈고, 서버가 `knowledge/all` 로 적었습니다. 화면을 고쳐 그 사슬을 끊었지만,
    **화면 하나에 기대는 안전은 다음 화면에서 깨집니다.**

    그래서 여기서 고정하는 것은 순수 불변식입니다: **읽은 값을 그대로 되돌려 적으면
    행이 안 움직인다.** 표현 못 하는 조합이면 빈 문자열이고, 빈 문자열은 아무것도
    안 바꿉니다.
    """
    import types

    from src.api.routes.policy_docs import PLACEMENTS, apply_placement, placement_of

    combos = [(access, mode, scope)
              for access in ("customer_context", "human_only")
              for mode in ("rules", "knowledge")
              for scope in ("all", "first", "followup")]
    for access, mode, scope in combos:
        row = types.SimpleNamespace(model_access=access, mode=mode, scope=scope)
        apply_placement(row, placement_of(row))
        assert (row.model_access, row.mode, row.scope) == (access, mode, scope), (
            f"{access}/{mode}/{scope} 가 되돌려 적는 것만으로 움직였습니다"
        )

    # 그리고 다섯 값은 전부 실제로 표현됩니다 — 하나라도 못 돌려주면 그 칸을 고를 수
    # 없거나, 고른 뒤 다시 열었을 때 다른 값이 보입니다.
    for key, _label, access, mode, scope in PLACEMENTS:
        row = types.SimpleNamespace(
            model_access=access, mode=mode or "knowledge", scope=scope or "all"
        )
        assert placement_of(row) == key


def test_an_unknown_placement_is_refused(policy_db):
    """조용히 기본값으로 떨어지면 사람용 문서가 고객용으로 저장된다."""
    with TestClient(app) as client:
        assert client.post(
            "/policy-docs", data={"label": "새 문서", "body": "본문", "placement": "무엇"}
        ).status_code == 400


def test_the_real_log_event_records_the_system_size(policy_db):
    """**검사 대상을 lambda 로 갈아 끼우고 그 lambda 를 부르던 테스트였습니다.**

    실제 `_log_event` 가 없어져도 통과했습니다 — 외부 검토가 지적했고 맞습니다.
    이제 진짜 함수를 부르고 저장된 payload 를 봅니다.
    """
    from src.db.models import Event
    from src.llm.client import LLMClient

    llm = LLMClient.__new__(LLMClient)
    llm.provider = "test"
    with patch("src.llm.client.SessionLocal", policy_db):
        LLMClient._log_event(llm, "1234", "ok", "rules" * 10)

    with policy_db() as session:
        payload = session.query(Event).filter(Event.kind == "llm_call").one().payload

    # 회사 규칙은 `prompt` 가 아니라 `system` 으로 갑니다. 이 칸이 없던 동안 로그의
    # `prompt_len` 은 실제로 보낸 입력의 절반도 안 됐습니다.
    assert payload["prompt_len"] == 4
    assert payload["system_len"] == 50
