"""문서 라우터 — 원본은 ``policy_sources`` 한 곳입니다.

한동안 사본 표(``knowledge_documents``)를 읽었습니다. 그 표의 칸은 하나도 자기 것이
아니었고(slug 은 ``doc_key`` 에서, 요약은 ``usage_note`` 에서, 메일 제목은 ``subject`` 를
태그에 실어서, ``scope``·``categories``·``author`` 는 행마다 같은 상수), 그래서 어긋날 수
있었습니다. 2026-08-27 에 표를 없애고 원본을 직접 읽습니다(이관 0098).

여기서 고정하는 것: **누가 후보가 되는가**(활성인 「문의별 참고」 행), **모델이 고른 것만
싣는가**, **못 골랐을 때 무엇으로 떨어지는가**, 그리고 **메일 제목은 누가 정하는가**.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.base import Base
from src.db.models import PolicySource
from src.llm import knowledge
from src.llm.knowledge import SelectDocsResult


class _FakeLLM:
    """Stub LLM whose router call returns a fixed set of keys."""

    def __init__(self, slugs: list[str]) -> None:
        self.slugs = slugs
        self.calls: list[dict] = []

    def complete(self, prompt_name, variables=None, schema=None, tier="flash", **kwargs):
        self.calls.append({"prompt": prompt_name, "variables": variables, "tier": tier})
        return SelectDocsResult(slugs=self.slugs, reasoning="stub")


class _BoomLLM:
    """Stub LLM whose router call raises, to exercise the fallback path."""

    def complete(self, *args, **kwargs):
        raise RuntimeError("router down")


@pytest.fixture(autouse=True)
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(knowledge, "SessionLocal", factory)
    return factory


def _doc(db, doc_key: str, title: str, **kwargs) -> None:
    """「문의별 참고」 문서 하나. ``doc_key`` 가 라우터에게 보이는 이름입니다."""
    fields = {"mode": "knowledge", "body": f"{title} 본문"}
    fields.update(kwargs)
    with db() as session:
        session.add(PolicySource(label=title, title=title, doc_key=doc_key, **fields))
        session.commit()


# ---- 누가 후보가 되는가 -------------------------------------------------------------


def test_only_knowledge_rows_are_candidates(db) -> None:
    """「항상 적용」은 고르는 대상이 아닙니다 — 모든 프롬프트에 통째로 들어갑니다.

    **상태는 안 봅니다** (0101): 지우면 행이 사라지므로(0100) 표에 있는 행이 곧 살아
    있는 행입니다.
    """
    _doc(db, "k1", "지원 언어 정책")
    _doc(db, "r1", "공통 원칙", mode="rules")

    assert [d.doc_key for d in knowledge.router_docs()] == ["k1"]


def test_no_documents_means_no_block(db) -> None:  # noqa: ARG001
    assert knowledge.select_relevant_docs("문의", "pricing_question") == ""


def test_selected_bodies_are_rendered_with_their_titles(db) -> None:
    _doc(db, "k1", "크레딧 차감 정책", body="초 단위로 차감합니다")
    _doc(db, "k2", "지원 언어 정책", body="99개 언어")

    out = knowledge.select_relevant_docs("문의", "support", llm=_FakeLLM(["k1", "k2"]))

    assert "### 크레딧 차감 정책" in out and "초 단위로 차감합니다" in out
    assert "### 지원 언어 정책" in out and "99개 언어" in out
    assert "---" in out, "문서 사이 구분이 없으면 모델이 한 문서로 읽습니다"


# ---- 라우터 ------------------------------------------------------------------------


def test_the_router_loads_only_what_it_picked(db) -> None:
    _doc(db, "k1", "고른 문서")
    _doc(db, "k2", "안 고른 문서")

    llm = _FakeLLM(["k1"])
    out = knowledge.select_relevant_docs("가격이 궁금합니다", "pricing_question", llm=llm)

    assert "고른 문서" in out and "안 고른 문서" not in out
    # 인덱스에는 둘 다 보여야 고를 수 있습니다.
    index = llm.calls[0]["variables"]["doc_index"]
    assert "k1" in index and "k2" in index


def test_the_index_carries_the_key_title_and_summary_only(db) -> None:
    """인덱스는 문의마다 통째로 프롬프트에 들어갑니다. 행마다 똑같은 값을 실으면 그만큼
    토큰이고 모델에게는 아무 정보도 아닙니다 — 사본 시절의 ``categories: all`` 과
    ``tags: notion`` 이 그랬습니다."""
    _doc(db, "k1", "크레딧 차감 정책", usage_note="크레딧이 얼마나 차감되는지 묻는 문의")

    llm = _FakeLLM([])
    knowledge.select_relevant_docs("문의", "support", llm=llm)
    index = llm.calls[0]["variables"]["doc_index"]

    assert index == (
        "- slug: k1\n  title: 크레딧 차감 정책\n"
        "  summary: 크레딧이 얼마나 차감되는지 묻는 문의"
    )


def test_the_summary_falls_back_to_the_top_of_the_body(db) -> None:
    """「언제 쓰는가」가 라우터가 읽는 유일한 설명입니다. 안 적었으면 본문 앞을 자릅니다 —
    첫 문단이 용도를 설명하는 문서라면 그것도 맞는 답입니다."""
    _doc(db, "k1", "환불 정책", body="## 표\n\n환불은 영업일 5~10일 안에 처리됩니다.")

    llm = _FakeLLM([])
    knowledge.select_relevant_docs("문의", "support", llm=llm)

    assert "환불은 영업일 5~10일 안에 처리됩니다." in llm.calls[0]["variables"]["doc_index"]


@pytest.mark.parametrize("llm", [None, _BoomLLM(), _FakeLLM([]), _FakeLLM(["없는키"])])
def test_every_failure_falls_back_to_every_active_document(db, llm) -> None:
    """**문서 없이 답을 쓰는 것보다는 낫습니다.** 라우터를 못 부르든, 터지든, 아무것도 못
    고르든, 있지도 않은 키를 돌려주든 — 답은 같습니다."""
    _doc(db, "k1", "문서 하나")
    _doc(db, "k2", "문서 둘")

    out = knowledge.select_relevant_docs("문의", "pricing_question", llm=llm)

    assert "문서 하나" in out and "문서 둘" in out


def test_spam_still_gets_documents(db) -> None:
    """영업·홍보 목적의 문의에도 회신은 나가고, 그 회신이 볼 것이 소개 문서입니다."""
    _doc(db, "k1", "Business 플랜 홍보")

    assert "Business 플랜 홍보" in knowledge.select_relevant_docs("광고입니다", "spam")


# ---- 메일 제목은 문서가 정하지 않는다 (2026-09-10) --------------------------------


def test_a_document_cannot_name_the_mail_any_more() -> None:
    """**문서가 회신 제목을 정하던 길은 없앴습니다** (운영자 지시, 이관 0118).

    그 길은 두 번 사고를 냈습니다. ① 제목을 든 문서가 둘이면 **가나다순으로 앞선 쪽**이
    이겼는데 그것이 「메일 템플릿」이라는 보장이 없었습니다 — 2026-08-26 에 「B2B 플랜
    비교표」(참고 문서)가 「견적 및 맞춤형 플랜 안내」(실제 회신 서식)를 제쳤고, 코드는
    이긴 쪽을 지목할 수 없어 경고만 남겼습니다. ② 문서 제목은 운영자가 쓴 **고정 문장**
    이라 문의 언어와 무관하게 그 문서의 언어로 나갔습니다(한국어 문의에 영어 제목, msg 62).

    이제 제목은 `common.subjects.reply_subject` 하나가 정합니다. **CODE GUARD 3 은
    그대로입니다** — 없어진 것은 문서가 제목을 덮어쓰는 길이고, 모델이 제목을 쓰는 길이
    열린 것이 아닙니다.
    """
    import pathlib

    from src.db.models import PolicySource

    assert not hasattr(PolicySource, "subject"), "문서는 더 이상 제목을 들지 않습니다"
    assert not hasattr(knowledge, "subject_from_docs")
    # 초안 경로에도 그 갈래가 남아 있으면 안 됩니다.
    inbound = pathlib.Path("src/agents/inbound.py").read_text(encoding="utf-8")
    assert "doc_subject" not in inbound
    assert "_subject_in_inquiry_language(" not in inbound
    assert "draft.subject = reply_subject(" in inbound


# ---- 사본 표는 없다 ----------------------------------------------------------------


def test_the_copy_table_is_gone() -> None:
    """``knowledge_documents`` 는 파생물이었습니다 — 칸이 하나도 자기 것이 아니었고,
    그래서 원본과 어긋날 수 있었습니다(0097 의 ``perso_refund_policy`` 가 그 결과입니다).
    표도, 그 표를 채우던 코드도 없어야 합니다(0098)."""
    import pathlib

    from src.db import models

    assert not hasattr(models, "KnowledgeDocument")
    assert "knowledge_documents" not in Base.metadata.tables
    # 그 표를 채우던 파일도 없습니다 (2026-09-10). 마지막까지 남아 있던 `knowledge_slug` 가
    # `notion-<해시>` 를 돌려주는데 그것이 정책 문서 목록에 찍혀 있었고, 함수 docstring 이
    # 직접 「아무것도 가리키지 않습니다」라고 적고 있었습니다(운영자 지적).
    assert not pathlib.Path("src/agents/policy_sync.py").exists()


# ---- 어느 회신에 붙는 문서인가 (0108) ------------------------------------------------


def test_a_follow_up_only_document_never_reaches_the_first_reply(db) -> None:
    """**첫 회신에는 간단히, 더 물어오면 자세히** (2026-09-07 운영자 지시).

    깊은 문서는 후속 회신에만 붙습니다. 모델에게 「첫 회신이니 고르지 마라」라고 부탁하는
    대신 인덱스에 **아예 안 싣습니다** — 부탁은 지켜질 때도 있고 안 지켜질 때도 있습니다.
    """
    _doc(db, "always", "지원 언어 정책")
    _doc(db, "deep", "엔터프라이즈 계약 조건 상세", scope="followup")
    _doc(db, "intro", "첫 인사 서식", scope="first")

    # 순서는 제목 가나다순 그대로입니다 — `scope` 는 거를 뿐 줄을 세우지 않습니다.
    assert [d.doc_key for d in knowledge.router_docs(knowledge.FIRST)] == ["always", "intro"]
    assert [d.doc_key for d in knowledge.router_docs(knowledge.FOLLOWUP)] == ["deep", "always"]


def test_the_default_scope_is_every_reply(db) -> None:
    """기존 문서는 전부 이 자리입니다 — 이 칸이 생겨도 **오늘 동작이 안 바뀝니다.**

    `mode` 를 늘리는 대신 칸을 나눈 이유가 이것입니다: 기본이 「모두」라, 이 칸을 안 보는
    코드는 많이 보여 줄 뿐 덜 보여 주지 않습니다.
    """
    _doc(db, "k1", "지원 언어 정책")

    assert [d.doc_key for d in knowledge.router_docs(knowledge.FIRST)] == ["k1"]
    assert [d.doc_key for d in knowledge.router_docs(knowledge.FOLLOWUP)] == ["k1"]
