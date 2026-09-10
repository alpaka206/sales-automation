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
2. ``router_docs()`` — 낙하산. 라우터가 실패하거나 아무것도 못 고르면 **문서 전부**
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


def usage_note_from_body(title: str, body: str, llm: object | None = None) -> str:
    """본문을 읽고 **라우터가 읽을 한 줄**을 만듭니다 (2026-09-10 운영자 지시).

    「언제 쓰는가」는 사람이 적던 칸이었습니다. 비워 두면 `summary_of` 가 본문 앞 400자를
    자르는데, **바로 표로 시작하는 문서에서는 그 400자가 아무것도 안 말합니다** — 그러면
    라우터가 그 문서를 못 고르고, 그것이 실측 「Doc router selected nothing」 5/5 의
    재료였습니다. 그렇다고 사람이 매번 적게 하면 안 적힌 행이 반드시 생깁니다.

    형식을 프롬프트가 고정합니다(`policy/usage_note.md`) — 「쓰는 경우: … / 담긴 것: …」.
    라우터가 보는 것은 본문이 아니라 이 한 줄이므로, **어떤 문의가 이 문서를 끌어와야
    하는지**를 고객이 쓸 말로 적어야 합니다.

    **실패하면 빈 문자열입니다.** 그때 그 칸은 NULL 로 남고 `summary_of` 가 예전처럼 본문
    앞부분으로 떨어집니다 — 문서를 저장하는 일이 모델 사정으로 막히면 안 됩니다.
    """
    text = (body or "").strip()
    if not text:
        return ""
    try:
        from .client import LLMClient

        line = (llm or LLMClient()).complete(
            "policy/usage_note",
            {"title": title or "", "body": text[:8000]},
            tier="flash",
            max_tokens=200,
        )
    except Exception:
        logger.warning("「언제 쓰는가」를 만들지 못했습니다: %s", title, exc_info=True)
        return ""
    line = " ".join(str(line or "").split()).strip()
    if not line or line == "(없음)":
        return ""
    return line[:_SUMMARY_CHARS]


# **메일 제목 칸은 없앴습니다** (2026-09-10 운영자 지시: 「메일 제목은 아예 db 자체에도
# 없어도 될 것 같고」). `subject_from_docs` 가 여기 있었습니다 — 근거로 쓴 문서가 들고 온
# 제목으로 회신 제목을 정하던 함수입니다.
#
# **그 길은 두 번 사고를 냈습니다.** ① 제목을 든 문서가 둘이면 가나다순으로 앞선 쪽이
# 이겼는데, 그것이 「메일 템플릿」이라는 보장이 없었다 — 2026-08-26 에 참고 문서가 실제
# 회신 서식을 제쳤다. ② 문서 제목은 운영자가 쓴 고정 문장이라 문서의 언어로 나갔다 —
# 한국어 문의에 영어 제목이 나간 것이 그것이다(msg 62). 두 번째는 `_subject_in_inquiry_
# language` 로 덧대었고, 그 함수도 같이 나갔습니다.
#
# 이제 제목은 `common.subjects.reply_subject` **하나**가 정합니다 — 「RE: <고객이 쓴
# 제목>」이고 RE: 가 쌓이지 않으며 문의의 언어입니다. CODE GUARD 3(제목을 모델에게 묻지
# 않는다)은 그대로입니다: 없어진 것은 **문서가 제목을 덮어쓰는 길**이고, 모델이 제목을
# 쓰는 길이 열린 것이 아닙니다.


def _format_docs(docs: list[PolicySource]) -> str:
    """Render selected documents as a prompt-ready block."""
    parts = [f"### {title_of(doc)}\n{doc.body or ''}" for doc in docs]
    if not parts:
        return ""
    return "## Relevant knowledge base documents\n\n" + "\n\n---\n\n".join(parts)


# 이 회신이 첫 회신인가 후속인가. `PolicySource.scope` 와 짝입니다(0108).
FIRST = "first"
FOLLOWUP = "followup"
_SCOPES_FOR = {FIRST: ("all", FIRST), FOLLOWUP: ("all", FOLLOWUP)}


def scopes_for_stage(stage: str | None) -> tuple[str, ...]:
    """이 단계에서 볼 수 있는 ``scope`` 들. **규칙 문서와 참고 문서가 같이 씁니다.**

    표가 둘이면 「첫 회신에만」인 규칙 문서와 참고 문서가 서로 다른 단계에 붙습니다.

    **모르는 값은 거절합니다.** 예전에는 ``router_docs`` 가 필터를 통째로 생략했는데,
    그러면 「후속 회신에만」 문서가 첫 회신 후보에 들어가고 화면에는 안 보입니다.
    넓히는 쪽으로 틀리는 것이 좁히는 쪽으로 틀리는 것보다 나쁩니다 — 사람용 문서가
    새는 길도 같은 자리입니다.
    """
    if stage is None:
        return ("all",)
    allowed = _SCOPES_FOR.get(stage)
    if allowed is None:
        raise ValueError(f"모르는 회신 단계입니다: {stage!r}")
    return allowed


def router_docs(stage: str = FIRST) -> list[PolicySource]:
    """그 회신에서 고를 수 있는 문서 — 「문의별 참고」 행 중 이 단계에 해당하는 것.

    ``stage`` 는 ``first``(첫 회신) 또는 ``followup``(그 뒤의 회신)입니다. 문서의 ``scope``
    가 ``all`` 이면 둘 다에, ``first``/``followup`` 이면 그 한쪽에만 붙습니다 — 첫 회신에는
    간단히 답하고 고객이 더 물어오면 깊은 문서를 붙여 자세히 쓰기 위한 칸입니다
    (2026-09-07 운영자 지시, ``docs/후속-회신-자동생성-설계.md``).

    **모르는 ``stage`` 는 안 거릅니다.** 이 칸을 덜 보여 주는 쪽으로 틀리면 초안이 근거
    없이 답하는데, 그건 화면 어디에도 안 보입니다.

    **상태를 안 봅니다** (0101). 지우면 행이 사라지므로(0100) 표에 있는 행이 곧 살아 있는
    행입니다 — 「항상 쓰는 것이니 항상 가져옵니다」. 캐시도 없습니다: 행이 몇 개뿐이고,
    여기서 캐시가 굳으면 어제 정책과 오늘 정책의 차이가 됩니다.

    ``mode='rules'`` 는 여기 안 옵니다 — 그쪽은 고르는 대상이 아니라 모든 프롬프트에
    통째로 들어갑니다(``llm.prompts._rules_from_db``).
    """
    allowed = scopes_for_stage(stage)
    session = SessionLocal()
    try:
        query = (
            session.query(PolicySource)
            # 0119 — 「사람만 본다」는 라우터 인덱스에도 안 실립니다. 인덱스는 제목과
            # 「언제 쓰는가」만 담지만, 그 둘도 그 문서의 내용입니다.
            .filter(PolicySource.model_access == "customer_context")
            .filter(PolicySource.mode == KNOWLEDGE)
            .filter(PolicySource.scope.in_(allowed))
        )
        return query.order_by(
            PolicySource.title, PolicySource.label, PolicySource.id
        ).all()
    finally:
        session.close()


def reset_cache() -> None:
    """회사 규칙 프롬프트 캐시를 비웁니다.

    문서 쪽은 캐시가 없습니다(``router_docs`` 가 매번 읽습니다). 이름이 남아 있는 것은
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
    stage: str = FIRST,
):
    """어떤 문서를 보고 답할지 **모델이** 고릅니다.

    ``category`` 는 힌트로 넘어가고, ``language`` 도 마찬가지입니다: 같은 문서가 KR/ENG
    두 벌로 있으면 문의 언어에 맞는 쪽만 고르라고 프롬프트가 말합니다(둘 다 넣으면 따라야
    할 형식이 두 개가 됩니다). 규칙이 프롬프트에 있다는 것이 요점입니다 — 정책이 바뀌면
    문서와 프롬프트가 바뀌지, 라우팅 표를 고치러 코드로 오지 않습니다.

    라우터가 실패하거나 아무것도 못 고르면 **문서 전부**로 떨어집니다.

    ``stage`` 는 후보를 먼저 좁힙니다 — 「후속 회신에만」 문서는 첫 회신의 인덱스에 아예
    안 실립니다. 모델에게 「이건 첫 회신이니 고르지 마라」라고 부탁하는 대신 보여 주지
    않습니다: 부탁은 지켜질 때도 있고 안 지켜질 때도 있습니다.
    """

    def done(docs: list[PolicySource]) -> str:
        return _format_docs(docs)

    candidates = router_docs(stage)
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
        logger.warning("Doc router failed, falling back to every document.", exc_info=True)
        return done(candidates)

    selected = [doc for doc in candidates if (doc.doc_key or "").lower() in wanted]
    if not selected:
        # **한 줄이 두 가지 실패를 같이 덮고 있었습니다** (2026-09-09 실측: 문의 5건 중
        # 5건이 이 줄이었는데, 어느 쪽인지는 로그 어디에도 없었습니다).
        #
        #   ① 모델이 정말 아무것도 안 골랐다 — 프롬프트를 고칠 일입니다.
        #   ② 모델이 고른 slug 가 `doc_key` 와 안 맞는다 — 제목을 돌려줬거나 철자가
        #      다른 것이고, 그건 **설정/데이터** 문제라 프롬프트를 아무리 고쳐도 안 낫습니다.
        #
        # 둘은 정반대 방향의 수리인데 화면에도 로그에도 구별이 없었습니다. 허브스팟 400 의
        # `errors[]` 를 남기는 것과 같은 이유로 여기서 갈라 적습니다 — **이 한 줄이 없으면
        # 원인을 알아내는 데 실제 문의를 한 건 태워야 합니다.**
        if wanted:
            logger.warning(
                "Doc router picked %d slug(s) that match no document (%s); "
                "known keys: %s. Falling back to every document.",
                len(wanted), ", ".join(sorted(wanted)),
                ", ".join(sorted((d.doc_key or "") for d in candidates)),
            )
        else:
            logger.info(
                "Doc router chose no document out of %d; falling back to every one. "
                "Model said: %s",
                len(candidates), (getattr(result, "reasoning", "") or "(no reason given)")[:200],
            )
        return done(candidates)

    logger.info(
        "Doc router selected %d/%d docs for category=%s: %s",
        len(selected),
        len(candidates),
        category,
        ", ".join(title_of(doc) for doc in selected),
    )
    return done(selected)
