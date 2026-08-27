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


# ---- 메일 제목 ---------------------------------------------------------------------


def test_the_document_can_carry_the_mail_subject(db) -> None:
    """제목은 코드가 읽습니다 — 모델에게 묻지 않습니다. 짧은 줄일수록 모델이 지어냅니다."""
    _doc(db, "k1", "견적 안내", subject="[Perso Dubbing] Next steps")

    body, subject = knowledge.select_relevant_docs(
        "견적", "pricing_question", llm=_FakeLLM(["k1"]), with_subject=True
    )

    assert "견적 안내" in body
    assert subject == "[Perso Dubbing] Next steps"


def test_a_document_with_no_subject_leaves_the_reply_on_re(db) -> None:
    _doc(db, "k1", "지원 언어 정책")

    subject = knowledge.select_relevant_docs(
        "언어", "support", llm=_FakeLLM(["k1"]), with_subject=True
    )[1]

    assert subject is None


def test_two_documents_carrying_a_subject_is_logged_not_silently_resolved(db, caplog) -> None:
    """순서는 제목 가나다순이라 **이긴 문서가 메일 템플릿이라는 보장이 없습니다** —
    2026-08-26 에 「B2B 플랜 비교표」(참고 문서)가 「견적 및 맞춤형 플랜 안내」(실제 회신
    서식)를 제치고 제목을 정했습니다. 그래서 경고는 어느 쪽이 옳다고 지목하지 않고 제목을
    든 문서를 전부 나열합니다 — 어느 것이 메일 템플릿인지는 운영자가 압니다."""
    _doc(db, "k1", "B2B 플랜 비교표", subject="비교표 제목")
    _doc(db, "k2", "견적 및 맞춤형 플랜 안내", subject="견적 제목")

    with caplog.at_level("WARNING"):
        subject = knowledge.select_relevant_docs(
            "견적", "pricing_question", llm=_FakeLLM(["k1", "k2"]), with_subject=True
        )[1]

    assert subject == "비교표 제목"
    logged = caplog.text
    assert "B2B 플랜 비교표" in logged and "견적 및 맞춤형 플랜 안내" in logged


# ---- 사본 표는 없다 ----------------------------------------------------------------


def test_the_copy_table_is_gone() -> None:
    """``knowledge_documents`` 는 파생물이었습니다 — 칸이 하나도 자기 것이 아니었고,
    그래서 원본과 어긋날 수 있었습니다(0097 의 ``perso_refund_policy`` 가 그 결과입니다).
    표도, 그 표를 채우던 코드도 없어야 합니다(0098)."""
    import src.agents.policy_sync as policy_sync
    from src.db import models

    assert not hasattr(models, "KnowledgeDocument")
    assert "knowledge_documents" not in Base.metadata.tables
    for gone in ("refresh_knowledge_copy", "_upsert_knowledge", "_tags_for"):
        assert not hasattr(policy_sync, gone), gone
