"""scripts/restore_deleted.py — 7일이 지난 뒤에도 되살릴 데가 남아 있는 두 가지.

이메일 템플릿은 여기 없습니다. 콘솔의 7일 휴지통이 그 일을 하고, 그 뒤에는 개정 이력까지
같이 청소됩니다 — 7일이 지나면 정말 없어진다는 것이 그 기능의 전부라서, 뒷문을 하나 더 두면
운영자가 일부러 흘려보낸 것이 되살아납니다. 그 청소는 tests/test_email_template_form.py 가
고정합니다.

여기 남은 둘은 휴지통이 아니라 **다른 목적의 사본**이 우연히 남는 경우입니다.
"""

from __future__ import annotations

import pytest

from src.db.models import KnowledgeDocument, PolicySource
from src.db.session import SessionLocal
from scripts.restore_deleted import (
    _orphan_knowledge,
    _restore_policy_doc,
    _restore_rule,
    _rule_seeds,
)


@pytest.fixture
def 지운_정책문서():
    """정책 문서 삭제는 등록부 행만 지웁니다 — 사본은 그대로 남습니다."""
    from src.api.routes.policy_docs import _doc_key

    key = _doc_key("톤앤 매너 가이드")
    slug = f"notion-{key[:12]}"
    with SessionLocal() as session:
        session.add(
            KnowledgeDocument(
                slug=slug, title="톤앤 매너 가이드", body="사본 본문",
                summary="말투를 정할 때", tags=["notion", "subject:안내"],
            )
        )
        session.commit()
    yield slug
    with SessionLocal() as session:
        session.query(KnowledgeDocument).filter_by(slug=slug).delete()
        session.query(PolicySource).filter_by(doc_key=key).delete()
        session.commit()


def test_a_deleted_policy_doc_comes_back_from_the_copy(지운_정책문서):
    with SessionLocal() as session:
        고아 = _orphan_knowledge(session)
        assert 지운_정책문서 in 고아
        _restore_policy_doc(session, 고아[지운_정책문서])

    with SessionLocal() as session:
        source = session.query(PolicySource).filter_by(label="톤앤 매너 가이드").one()
        assert source.body == "사본 본문" and source.mode == "knowledge"
        assert source.subject == "안내"        # 제목은 태그에서 되짚습니다
        assert 지운_정책문서 not in _orphan_knowledge(session)


def test_an_always_applied_rule_comes_back_from_its_seed_file():
    """DB 사본이 없는 대신 씨앗 파일이 저장소에 있습니다 — 0043 이 처음 넣은 그 텍스트."""
    from src.llm.prompts import get_company_rules

    seeds = _rule_seeds()
    path = seeds["rule_01_common_principles.md"]
    key = f"file:{path.name}"
    with SessionLocal() as session:
        session.query(PolicySource).filter_by(doc_key=key).delete()
        session.commit()
    try:
        with SessionLocal() as session:
            _restore_rule(session, path)
        with SessionLocal() as session:
            source = session.query(PolicySource).filter_by(doc_key=key).one()
            assert source.mode == "rules"
            # 제목 줄은 label 로 올라갑니다 — 안 그러면 프롬프트에 제목이 두 번 들어갑니다.
            assert source.label == "공통 원칙 및 가드레일"
            assert not source.body.startswith("#")
            # 캐시가 없으므로 복원 즉시 다음 초안이 읽습니다.
            assert "공통 원칙 및 가드레일" in get_company_rules()

            with pytest.raises(SystemExit):
                _restore_rule(session, path)     # 이미 있는 것을 두 번 넣지 않습니다
    finally:
        with SessionLocal() as session:
            session.query(PolicySource).filter_by(doc_key=key).delete()
            session.commit()


def test_a_renamed_copy_is_refused_rather_than_split_in_two(지운_정책문서):
    """제목이 바뀐 뒤였다면 doc_key 가 안 맞습니다. 지어내면 같은 문서가 둘이 됩니다."""
    with SessionLocal() as session:
        doc = _orphan_knowledge(session)[지운_정책문서]
        doc.title = "다른 이름"
        with pytest.raises(SystemExit):
            _restore_policy_doc(session, doc)
        session.rollback()
