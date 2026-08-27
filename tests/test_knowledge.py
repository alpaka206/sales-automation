"""Tests for the DB-backed knowledge_base loader."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.base import Base
from src.db.models import KnowledgeDocument
from src.llm import knowledge
from src.llm.knowledge import SelectDocsResult


class _FakeLLM:
    """Stub LLM whose router call returns a fixed set of slugs."""

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
def _db_backed_knowledge(monkeypatch):
    """Point the knowledge loader at an in-memory DB and clear its cache."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(knowledge, "SessionLocal", factory)
    knowledge.reset_cache()
    yield factory
    knowledge.reset_cache()


def _insert(factory, **kwargs) -> None:
    """Helper to insert a KnowledgeDocument."""
    defaults = {"scope": "both", "body": "body"}
    defaults.update(kwargs)
    session = factory()
    session.add(KnowledgeDocument(**defaults))
    session.commit()
    session.close()


def test_category_match(_db_backed_knowledge) -> None:
    _insert(
        _db_backed_knowledge,
        title="2026 Pricing",
        slug="pricing",
        categories=["pricing_question", "purchase_inquiry"],
        body="Body text here.",
    )
    out = knowledge.load_relevant_docs("pricing_question")
    assert "2026 Pricing" in out
    assert "Body text here." in out


def test_no_match_returns_empty(_db_backed_knowledge) -> None:
    _insert(
        _db_backed_knowledge,
        title="Pricing",
        slug="pricing",
        categories=["pricing_question"],
    )
    assert knowledge.load_relevant_docs("recruiting") == ""


def test_all_keyword_matches_every_category(_db_backed_knowledge) -> None:
    _insert(
        _db_backed_knowledge,
        title="About Us",
        slug="about",
        categories=["all"],
        body="We are a company.",
    )
    for cat in ("purchase_inquiry", "partnership", "support", "recruiting", "other"):
        knowledge.reset_cache()
        out = knowledge.load_relevant_docs(cat)
        assert "About Us" in out, f"missing for category={cat}"


def test_empty_categories_defaults_to_all(_db_backed_knowledge) -> None:
    _insert(
        _db_backed_knowledge,
        title="General Info",
        slug="general",
        categories=[],
        body="Applies to everything.",
    )
    assert "General Info" in knowledge.load_relevant_docs("partnership")
    knowledge.reset_cache()
    assert "General Info" in knowledge.load_relevant_docs("support")


def test_null_categories_defaults_to_all(_db_backed_knowledge) -> None:
    _insert(
        _db_backed_knowledge,
        title="General Info",
        slug="general",
        categories=None,
        body="Applies to everything.",
    )
    assert "General Info" in knowledge.load_relevant_docs("partnership")


def test_spam_still_gets_documents(_db_backed_knowledge) -> None:
    """It used to short-circuit to "". The operator's rule is that a 영업·홍보 목적의
    문의에도 회신은 나가고, 그 회신이 볼 것이 소개 문서입니다 — so taking the documents
    away left exactly that one reply written from nothing."""
    _insert(
        _db_backed_knowledge,
        title="General",
        slug="general",
        categories=["all"],
        body="Applies to everything.",
    )
    assert "General" in knowledge.load_relevant_docs("spam")


def test_empty_db_returns_empty(_db_backed_knowledge) -> None:
    assert knowledge.load_relevant_docs("purchase_inquiry") == ""


def test_multiple_matches_are_separated(_db_backed_knowledge) -> None:
    _insert(
        _db_backed_knowledge,
        title="Pricing A",
        slug="a-pricing",
        categories=["pricing_question"],
        body="A body.",
    )
    _insert(
        _db_backed_knowledge,
        title="Plans B",
        slug="b-plans",
        categories=["pricing_question"],
        body="B body.",
    )
    out = knowledge.load_relevant_docs("pricing_question")
    assert "Pricing A" in out
    assert "Plans B" in out
    assert "---" in out


def test_scope_inbound_includes_shared_docs(_db_backed_knowledge) -> None:
    _insert(
        _db_backed_knowledge,
        title="Inbound Only",
        slug="inbound-only",
        categories=["all"],
        scope="inbound",
    )
    _insert(_db_backed_knowledge, title="Shared", slug="shared", categories=["all"], scope="both")
    out = knowledge.load_relevant_docs("purchase_inquiry", scope="inbound")
    assert "Inbound Only" in out
    assert "Shared" in out


def test_scope_both_matches_all(_db_backed_knowledge) -> None:
    _insert(_db_backed_knowledge, title="Both", slug="both", categories=["all"], scope="both")
    _insert(_db_backed_knowledge, title="In", slug="in", categories=["all"], scope="inbound")
    out = knowledge.load_relevant_docs("purchase_inquiry", scope="both")
    assert "Both" in out
    assert "In" in out


def test_archived_docs_excluded_from_category_match(_db_backed_knowledge) -> None:
    _insert(
        _db_backed_knowledge,
        title="Archived Pricing",
        slug="archived",
        categories=["pricing_question"],
        body="old body",
        status="archived",
    )
    assert knowledge.load_relevant_docs("pricing_question") == ""


# ---- LLM document router ----


def test_router_selects_only_returned_slugs(_db_backed_knowledge) -> None:
    _insert(_db_backed_knowledge, title="Pricing", slug="pricing",
            categories=["pricing_question"], body="pricing body", summary="prices")
    _insert(_db_backed_knowledge, title="Refund", slug="refund",
            categories=["support"], body="refund body", summary="refunds")
    llm = _FakeLLM(slugs=["refund"])
    out = knowledge.select_relevant_docs("환불 되나요?", "support", llm=llm)
    assert "Refund" in out
    assert "Pricing" not in out
    # router runs on the cheap flash tier
    assert llm.calls and llm.calls[0]["tier"] == "flash"


def test_router_none_llm_falls_back_to_category(_db_backed_knowledge) -> None:
    _insert(_db_backed_knowledge, title="Pricing", slug="pricing",
            categories=["pricing_question"], body="pricing body")
    out = knowledge.select_relevant_docs("price?", "pricing_question", llm=None)
    assert "Pricing" in out


def test_router_empty_selection_falls_back_to_category(_db_backed_knowledge) -> None:
    _insert(_db_backed_knowledge, title="Pricing", slug="pricing",
            categories=["pricing_question"], body="pricing body")
    llm = _FakeLLM(slugs=[])  # selects nothing
    out = knowledge.select_relevant_docs("price?", "pricing_question", llm=llm)
    assert "Pricing" in out  # fell back to category match


def test_router_error_falls_back_to_category(_db_backed_knowledge) -> None:
    _insert(_db_backed_knowledge, title="Pricing", slug="pricing",
            categories=["pricing_question"], body="pricing body")
    out = knowledge.select_relevant_docs("price?", "pricing_question", llm=_BoomLLM())
    assert "Pricing" in out


def test_the_router_picks_documents_for_spam_too(_db_backed_knowledge) -> None:
    """The router decides; spam is no longer refused at the door. Selecting nothing is
    still its own call to make — the prompt says so — but it has to be a judgement about
    this inquiry, not a rule that fires before the model sees it."""
    _insert(_db_backed_knowledge, title="X", slug="x", categories=["all"], body="b")
    assert "X" in knowledge.select_relevant_docs("buy viagra", "spam", llm=_FakeLLM(["x"]))


def test_router_ignores_archived_candidates(_db_backed_knowledge) -> None:
    _insert(_db_backed_knowledge, title="Live", slug="live",
            categories=["pricing_question"], body="live body", status="active")
    _insert(_db_backed_knowledge, title="Old", slug="old",
            categories=["pricing_question"], body="old body", status="archived")
    # Even if the model names the archived slug, it isn't a candidate.
    llm = _FakeLLM(slugs=["old", "live"])
    out = knowledge.select_relevant_docs("price?", "pricing_question", llm=llm)
    assert "Live" in out
    assert "Old" not in out


def test_a_document_can_carry_the_mail_subject_for_replies_written_from_it(
    _db_backed_knowledge,
) -> None:
    """「기본 메일 템플릿 ENG」 같은 문서는 그 자체가 회신 한 통의 본보기입니다. 제목을 본문
    안에 "Subject: ..." 로 적으면 모델이 그 줄을 본문에 옮겨 적어, 첫 줄이 "Subject: ..." 인
    메일이 나갑니다. 그래서 문서의 칸으로 두고 **코드가** 꺼냅니다 — 제목은 모델이 지어내기
    딱 좋은 자리이고, 지어내면 RE: 가 겹치거나 언어가 뒤집힙니다."""
    _insert(
        _db_backed_knowledge, title="기본 메일 템플릿 ENG", slug="intro",
        categories=["purchase_inquiry"], body="Hey [Name],",
        tags=["notion", "subject:Next Steps on Your custom Perso Dubbing plan"],
    )
    body, subject = knowledge.select_relevant_docs(
        "tell me more", "purchase_inquiry", with_subject=True
    )
    assert "Hey [Name]," in body
    assert subject == "Next Steps on Your custom Perso Dubbing plan"


def test_a_document_with_no_subject_leaves_the_reply_on_re(_db_backed_knowledge) -> None:
    """그쪽이 고객 메일함에서 원래 스레드에 붙습니다. 제목을 정한 문서만 그걸 벗어납니다."""
    _insert(_db_backed_knowledge, title="크레딧", slug="credits",
            categories=["credits"], body="1 credit/초")
    _, subject = knowledge.select_relevant_docs("크레딧?", "credits", with_subject=True)
    assert subject is None


def test_two_documents_carrying_a_subject_is_logged_not_silently_resolved(
    _db_backed_knowledge, caplog
) -> None:
    """메일 제목은 메일 템플릿에만 채웁니다 — 지원 언어·크레딧 같은 근거 문서는 내용을
    제공할 뿐 그 메일의 제목을 정하지 않습니다.

    코드가 이름으로 "메일 템플릿" 을 알아보게 하지는 않았습니다: 문서 이름은 바뀌고, 이름을
    조건에 넣으면 이름을 바꾸는 순간 조용히 끊깁니다. 대신 둘 이상 채워져 있으면 경고를
    남깁니다 — 조용히 하나를 고르면 고객 메일함에 뜨는 제목이 문서 제목 알파벳순으로 정해지고
    왜 그런지 아무 데도 안 남습니다.
    """
    import logging

    _insert(_db_backed_knowledge, title="A 템플릿", slug="a", categories=["credits"],
            body="a", tags=["subject:From the template"])
    _insert(_db_backed_knowledge, title="B 근거", slug="b", categories=["credits"],
            body="b", tags=["subject:From the reference"])

    with caplog.at_level(logging.WARNING, logger="src.llm.knowledge"):
        _, subject = knowledge.select_relevant_docs("크레딧?", "credits", with_subject=True)

    assert subject == "From the template"
    assert "메일 제목은 메일 템플릿에만" in caplog.text


# ---- 화면에 없는 문서는 남지 않는다 (0097) -------------------------------------------


def test_0097_keeps_only_the_copies_the_console_can_show():
    """**남을 것을 정의합니다 — 지울 것을 나열하지 않습니다.**

    0077 은 지울 씨앗 문서 11개의 이름을 그대로 적었고, 그래서 목록에서 빠진
    ``perso_refund_policy`` 하나가 ``status='active'`` 로 살아남았습니다. 초안 라우터는
    그것을 고를 수 있었는데 콘솔에는 안 떴습니다 — 운영자가 못 보고 못 고치는 문서가
    회신에 인용되는 상태입니다. 규칙을 뒤집으면 빠진 것은 살아남지 않고 지워집니다.
    """
    import hashlib
    import importlib

    from sqlalchemy import create_engine, text

    from src.db.models import KnowledgeDocument, PolicySource

    engine = create_engine("sqlite:///:memory:")
    for model in (KnowledgeDocument, PolicySource):
        model.__table__.create(engine)

    doc_key = hashlib.sha256("살아 있는 정책".encode()).hexdigest()[:32]
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO policy_sources (label, doc_key, mode, order_index, status, "
                 "version, created_at, updated_at) VALUES ('살아 있는 정책', :k, 'knowledge', "
                 "100, 'active', 3, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
            {"k": doc_key},
        )
        for slug, status in (
            (f"notion-{doc_key}", "active"),   # 사본 — 남습니다
            ("perso_refund_policy", "active"),  # 0077 목록에서 빠졌던 그 행
            ("perso_pricing", "archived"),      # 재워 둔 씨앗
        ):
            conn.execute(
                text("INSERT INTO knowledge_documents (slug, title, body, scope, status, "
                     "version, created_at, updated_at) VALUES (:s, :s, 'b', 'both', :st, 1, "
                     "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
                {"s": slug, "st": status},
            )

    importlib.import_module("src.db.migrations.0097_only_the_documents_in_use_remain").up(engine)

    with engine.begin() as conn:
        left = [r[0] for r in conn.execute(text("SELECT slug FROM knowledge_documents"))]
        assert left == [f"notion-{doc_key}"]
        # 판 번호는 1부터 다시. 화면의 "v3" 이 「이 화면에서 세 번 저장했다」가 되도록.
        assert conn.execute(text("SELECT version FROM policy_sources")).scalar() == 1
