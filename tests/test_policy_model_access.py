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


def test_the_system_prompt_size_is_logged_separately(monkeypatch):
    """회사 규칙은 `prompt` 가 아니라 `system` 으로 간다.

    이 칸이 없던 동안 로그의 `prompt_len` 은 실제로 보낸 입력의 절반도 안 됐고,
    그래서 「프롬프트를 줄였다」를 로그만으로는 증명할 수 없었다.
    """
    from src.llm.client import LLMClient

    seen: dict = {}
    llm = LLMClient.__new__(LLMClient)
    llm.provider = "test"
    monkeypatch.setattr(
        LLMClient, "_log_event",
        lambda self, prompt, result, system=None: seen.update(
            prompt_len=len(prompt), system_len=len(system or "")
        ),
    )
    LLMClient._log_event(llm, "1234", "ok", "rules" * 10)
    assert seen == {"prompt_len": 4, "system_len": 50}
