"""문서 라우터 — 이번 문의에 어떤 정책 문서를 붙일지 **모델이** 고릅니다.

원본은 ``policy_sources`` **한 곳**입니다. 콘솔의 「정책 문서」가 쓰고, 여기가 읽습니다.

**한동안 사본이 하나 더 있었습니다.** ``knowledge_documents`` 라는 표에 ``policy_sync``
가 같은 문서를 밀어 넣었고, 라우터는 그 사본을 읽었습니다. 그 표의 칸은 **하나도 자기
것이 아니었습니다** — slug 은 ``doc_key`` 에서, 제목·본문은 그대로, 요약은 ``usage_note``
에서, 메일 제목은 ``subject`` 를 ``tags`` 에 ``"subject:…"`` 로 실어서, ``scope`` ·
``categories`` · ``author`` 는 행마다 똑같은 상수였습니다. 원본이 아니라 파생물이었고,
그래서 어긋날 수 있었습니다: 상태를 따로 재워야 했고(``_set_knowledge_status``), 저장
직후 사본을 따로 밀어야 했고(``refresh_knowledge_copy``), 재우다 만 행 하나가 콘솔에
안 보이는 채로 초안에 인용될 뻔했습니다(0097). 2026-08-27 에 표를 없애고 원본을 직접
읽습니다.

고르는 방법은 둘입니다:

1. ``select_relevant_docs(inquiry, category, llm)`` — **LLM 라우터.** 문서마다 한 줄짜리
   인덱스(``doc_key`` · 제목 · 요약)를 만들어 모델에게 주고 고르게 합니다. 본문은 고른
   것만 싣습니다.
2. ``active_docs()`` — 낙하산. 라우터가 실패하거나 아무것도 못 고르면 **활성 문서 전부**
   입니다. 문서 없이 답을 쓰는 것보다는 낫습니다.

어떤 문의에 어떤 문서를 붙일지는 **코드에 없습니다.** 정책은 바뀌고 문서 이름도 바뀌므로,
매핑을 코드에 굳히면 그때마다 아무 흔적 없이 끊깁니다.
"""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel

from ..db.models import PolicySource
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)

# 라우터가 후보마다 읽는 한 줄. 한 프롬프트에 문서 수만큼 들어가므로 일부러 짧습니다.
_SUMMARY_CHARS = 400

KNOWLEDGE = "knowledge"
ACTIVE = "active"


class SelectDocsResult(BaseModel):
    """Router output: which document keys are relevant to the inquiry."""

    slugs: list[str] = []
    reasoning: str = ""


def _summarize(markdown: str) -> str:
    text = re.sub(r"[#>|*`-]", " ", markdown or "")
    return re.sub(r"\s+", " ", text).strip()[:_SUMMARY_CHARS]


def summary_of(source: PolicySource) -> str:
    """라우터가 읽을 한 줄 — 사람이 적은 「언제 쓰는가」가 있으면 그것.

    없으면 본문 앞부분을 자릅니다. 본문 첫 문단이 용도를 설명하는 문서라면 그것도 맞는
    답이고, 그렇지 않은 문서(바로 표로 시작하는 것들)는 그 칸을 채워야 골라집니다.
    """
    note = (source.usage_note or "").strip()
    return note[:_SUMMARY_CHARS] if note else _summarize(source.body or "")


def title_of(source: PolicySource) -> str:
    return source.title or source.label or ""


def subject_from_docs(docs: list[PolicySource]) -> str | None:
    """근거로 쓴 문서가 들고 온 메일 제목.

    코드가 읽습니다 — 모델에게 묻지 않습니다(CODE GUARD 3 과 같은 이유). 제목은 모델이
    기꺼이 지어내는 종류의 짧은 줄이고, 그러면 RE: 가 쌓이거나 언어가 뒤집힙니다.

    **메일 제목은 메일 템플릿 문서에만 채웁니다.** 지원 언어·크레딧 같은 근거 문서는
    내용을 제공할 뿐 그 메일의 제목을 정하지 않습니다 — 그 문서들의 제목 칸은 비워 둡니다.
    코드가 이름으로 「메일 템플릿」을 알아보게 하지는 않았습니다: 문서 이름은 바뀌고,
    이름을 조건에 넣으면 이름을 바꾸는 순간 조용히 끊깁니다.
    """
    carrying = [(doc, (doc.subject or "").strip()) for doc in docs]
    carrying = [(doc, subject) for doc, subject in carrying if subject]
    if not carrying:
        return None
    if len(carrying) > 1:
        # **어느 쪽이 옳은지 여기서는 모릅니다.** 순서는 제목 가나다순이라, 이긴 문서가
        # 「메일 템플릿」이라는 보장이 없습니다 — 2026-08-26 에 「B2B 플랜 비교표」(참고
        # 문서)가 「견적 및 맞춤형 플랜 안내」(실제 회신 서식)를 제치고 제목을 정했습니다.
        # 그때 이 경고는 이긴 쪽을 옳다고 가정하고 진 쪽을 비우라고 적었는데, 비워야 할
        # 것은 반대였습니다. 그래서 지목하지 않고 전부 나열합니다.
        logger.warning(
            "%d documents carry a mail subject (%s); 가나다순으로 앞선 「%s」의 제목을 "
            "씁니다. 메일 제목은 메일 템플릿에만 채우고, 내용만 제공하는 근거 문서는 "
            "제목 칸을 비워 두세요.",
            len(carrying),
            ", ".join(title_of(doc) for doc, _ in carrying),
            title_of(carrying[0][0]),
        )
    return carrying[0][1]


def _format_docs(docs: list[PolicySource]) -> str:
    """Render selected documents as a prompt-ready block."""
    parts = [f"### {title_of(doc)}\n{doc.body or ''}" for doc in docs]
    if not parts:
        return ""
    return "## Relevant knowledge base documents\n\n" + "\n\n---\n\n".join(parts)


def active_docs() -> list[PolicySource]:
    """초안이 고를 수 있는 문서 전부 — 활성인 「문의별 참고」 행.

    캐시하지 않습니다. 행이 몇 개뿐이고, 여기서 캐시가 굳으면 어제 정책과 오늘 정책의
    차이가 됩니다. ``mode='rules'`` 는 여기 안 옵니다 — 그쪽은 고르는 대상이 아니라 모든
    프롬프트에 통째로 들어갑니다(``llm.prompts._rules_from_db``).
    """
    session = SessionLocal()
    try:
        return (
            session.query(PolicySource)
            .filter(PolicySource.mode == KNOWLEDGE, PolicySource.status == ACTIVE)
            .order_by(PolicySource.title, PolicySource.label, PolicySource.id)
            .all()
        )
    finally:
        session.close()


def reset_cache() -> None:
    """회사 규칙 프롬프트 캐시를 비웁니다.

    문서 쪽은 캐시가 없습니다(``active_docs`` 가 매번 읽습니다). 이름이 남아 있는 것은
    콘솔이 정책 문서를 저장한 뒤 이것을 부르기 때문입니다.
    """
    from .prompts import get_company_rules

    get_company_rules.cache_clear()


def _build_index(docs: list[PolicySource]) -> str:
    """Compact, token-cheap index the router reads to pick documents."""
    return "\n".join(
        f"- slug: {doc.doc_key}\n  title: {title_of(doc)}\n  summary: "
        f"{summary_of(doc) or '(no summary)'}"
        for doc in docs
    )


def select_relevant_docs(
    inquiry: str,
    category: str,
    llm: object | None = None,
    language: str | None = None,
    with_subject: bool = False,
):
    """어떤 문서를 보고 답할지 **모델이** 고릅니다.

    ``category`` 는 힌트로 넘어가고, ``language`` 도 마찬가지입니다: 같은 문서가 KR/ENG
    두 벌로 있으면 문의 언어에 맞는 쪽만 고르라고 프롬프트가 말합니다(둘 다 넣으면 따라야
    할 형식이 두 개가 됩니다). 규칙이 프롬프트에 있다는 것이 요점입니다 — 정책이 바뀌면
    문서와 프롬프트가 바뀌지, 라우팅 표를 고치러 코드로 오지 않습니다.

    라우터가 실패하거나 아무것도 못 고르면 **활성 문서 전부**로 떨어집니다.

    ``with_subject=True`` 면 (본문, 그 문서들이 들고 온 메일 제목) 을 돌려줍니다.
    """

    def done(docs: list[PolicySource]):
        text = _format_docs(docs)
        return (text, subject_from_docs(docs)) if with_subject else text

    candidates = active_docs()
    if not candidates:
        return done([])
    if llm is None:
        return done(candidates)

    try:
        result = llm.complete(
            "inbound/select_docs",
            {
                "inquiry": (inquiry or "").strip() or "(no message body)",
                "category": category or "unknown",
                "inquiry_language": (language or "unknown"),
                "doc_index": _build_index(candidates),
            },
            schema=SelectDocsResult,
            tier="flash",
        )
        wanted = {s.strip().lower() for s in (result.slugs or []) if s.strip()}
    except Exception:
        logger.warning("Doc router failed, falling back to every active document.", exc_info=True)
        return done(candidates)

    selected = [doc for doc in candidates if (doc.doc_key or "").lower() in wanted]
    if not selected:
        logger.info("Doc router selected nothing; falling back to every active document.")
        return done(candidates)

    logger.info(
        "Doc router selected %d/%d docs for category=%s: %s",
        len(selected),
        len(candidates),
        category,
        ", ".join(title_of(doc) for doc in selected),
    )
    return done(selected)
