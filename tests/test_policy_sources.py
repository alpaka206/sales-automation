"""``policy_sources`` — 초안이 실제로 무엇을 읽는가.

이 파일은 원래 "노션에서 읽어 오다 실패해도 정책을 잃지 않는다" 를 지키는 테스트들이었습니다.
읽어 오는 경로가 전부 사라졌으므로(토큰 발급 불가 · 쿠키 403 · Export zip 이 부모 한 장,
docs/정책문서-동기화-설계.md) 그 절반은 없어졌고, 남은 것은 **한 행이 어느 프롬프트에
들어가는가** 입니다:

    mode='rules'      모든 회신에 붙는 시스템 지시문
    mode='knowledge'  그 문의에 해당할 때만, 문서 라우터를 통해

이 구분이 무너지면 둘 중 하나가 됩니다 — 가격 정책이 모든 회신에 붙어 컨텍스트를 채우거나,
가드레일이 아무 회신에도 안 붙거나.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.base import Base
from src.db.models import KnowledgeDocument, PolicySource


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    from src.agents import policy_sync

    monkeypatch.setattr(policy_sync, "SessionLocal", factory)
    # prompts._rules_from_db imports SessionLocal at call time, so patching the module
    # attribute is what redirects it.
    monkeypatch.setattr("src.db.session.SessionLocal", factory)
    return factory


# ---- Always-applied rules ------------------------------------------------------------


def test_rules_rows_become_the_system_instruction(db):
    from src.llm.prompts import get_company_rules

    with db() as session:
        session.add_all(
            [
                PolicySource(
                    label="톤", doc_key="file:01_tone.md", mode="rules",
                    order_index=10, body="항상 존댓말.",
                ),
                PolicySource(
                    label="CS", doc_key="file:04_cs.md", mode="rules",
                    order_index=20, body="사과는 한 번만.",
                ),
            ]
        )
        session.commit()

    rules = get_company_rules()
    assert "Company rules (must follow)" in rules
    assert rules.index("항상 존댓말.") < rules.index("사과는 한 번만.")  # order_index honoured


def test_a_paused_rule_leaves_the_prompt(db):
    from src.llm.prompts import get_company_rules

    with db() as session:
        session.add(
            PolicySource(
                label="톤", doc_key="file:x.md", mode="rules",
                body="적용되면 안 됨", status="paused",
            )
        )
        session.commit()
    assert "적용되면 안 됨" not in get_company_rules()


def test_knowledge_rows_are_not_in_the_system_instruction(db):
    """Per-inquiry documents go through the router; putting them in every prompt would
    blow the context and apply pricing policy to a report narration."""
    from src.llm.prompts import get_company_rules

    with db() as session:
        session.add(
            PolicySource(
                label="가격", doc_key="p" * 32, mode="knowledge", body="Tier 1 $1,000",
            )
        )
        session.commit()
    assert "Tier 1" not in get_company_rules()


# ---- The copy the router reads -------------------------------------------------------


def test_a_knowledge_row_reaches_the_table_the_router_reads(db):
    from src.agents.policy_sync import refresh_knowledge_copy

    with db() as session:
        session.add(
            PolicySource(
                label="크레딧 차감 정책", title="크레딧 차감 정책", doc_key="c" * 32,
                mode="knowledge", body="업로드 실패 시 크레딧은 복구됩니다.",
            )
        )
        session.commit()
        source_id = session.query(PolicySource).one().id

    refresh_knowledge_copy(source_id)
    with db() as session:
        doc = session.query(KnowledgeDocument).one()
        assert doc.title == "크레딧 차감 정책"
        assert "크레딧은 복구" in doc.body


def test_renaming_a_document_updates_its_copy_instead_of_making_a_second_one(db):
    """슬러그가 제목이 아니라 doc_key 에서 나오는 이유입니다. 제목으로 만들면 이름을 바꾼
    순간 옛 사본이 남고, 라우터가 한 정책을 서로 다른 두 문서로 인용합니다."""
    from src.agents.policy_sync import refresh_knowledge_copy

    with db() as session:
        session.add(
            PolicySource(
                label="가격 정책", title="가격 정책", doc_key="d" * 32,
                mode="knowledge", body="v1",
            )
        )
        session.commit()
        source_id = session.query(PolicySource).one().id
    refresh_knowledge_copy(source_id)

    with db() as session:
        source = session.get(PolicySource, source_id)
        source.title = "B2B 가격 정책"
        source.body = "v2"
        session.commit()
    refresh_knowledge_copy(source_id)

    with db() as session:
        docs = session.query(KnowledgeDocument).all()
        assert len(docs) == 1
        assert docs[0].title == "B2B 가격 정책"
        assert docs[0].body == "v2"


def test_a_rules_row_never_becomes_a_knowledge_document(db):
    """항상 적용 문서는 시스템 지시문으로 이미 들어갑니다. 사본까지 만들면 같은 글이 한
    프롬프트에 두 번 들어갑니다."""
    from src.agents.policy_sync import refresh_knowledge_copy

    with db() as session:
        session.add(
            PolicySource(label="가드레일", doc_key="e" * 32, mode="rules", body="가격 숫자 금지")
        )
        session.commit()
        source_id = session.query(PolicySource).one().id

    refresh_knowledge_copy(source_id)
    with db() as session:
        assert session.query(KnowledgeDocument).count() == 0
