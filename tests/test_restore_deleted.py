"""scripts/restore_deleted.py — 실수로 지운 것을 되살립니다.

지우는 화면은 둘인데 남는 것이 서로 다릅니다. 여기서 고정하는 것은 그 **차이**입니다:
서명은 스냅샷에서, 「문의별 참고」 정책 문서는 초안이 읽는 사본에서 돌아오고, 「항상 적용」
정책 문서는 돌아올 곳이 없습니다. 셋을 한 화면에서 지우니까 셋이 같아 보입니다.
"""

from __future__ import annotations

import pytest

from src.db.models import (
    EmailTemplate,
    EmailTemplateRevision,
    KnowledgeDocument,
    PolicySource,
)
from src.db.session import SessionLocal
from scripts.restore_deleted import (
    _deleted_templates,
    _orphan_knowledge,
    _restore_policy_doc,
    _restore_template,
)


@pytest.fixture
def 지운_서명():
    """콘솔의 삭제 라우트가 남기는 그대로 — 행은 없고 스냅샷만 있습니다."""
    with SessionLocal() as session:
        session.add(
            EmailTemplateRevision(
                template_id=9999, key="signature_톤앤매너", name="톤앤 매너",
                body="지워진 본문", change_note="deleted", edited_by="운영자",
            )
        )
        session.commit()
    yield "signature_톤앤매너"
    with SessionLocal() as session:
        session.query(EmailTemplateRevision).filter_by(key="signature_톤앤매너").delete()
        session.query(EmailTemplate).filter_by(key="signature_톤앤매너").delete()
        session.commit()


def test_a_deleted_signature_comes_back_whole(지운_서명):
    with SessionLocal() as session:
        되살릴것 = _deleted_templates(session)
        assert 지운_서명 in 되살릴것
        _restore_template(session, 되살릴것[지운_서명])

    with SessionLocal() as session:
        tpl = session.query(EmailTemplate).filter_by(key=지운_서명).one()
        assert tpl.name == "톤앤 매너" and tpl.body == "지워진 본문"
        # 되살린 것은 목록에서 빠져야 합니다. id 로 찾으면 새 id 가 붙어서 영원히 남습니다.
        assert 지운_서명 not in _deleted_templates(session)


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


def test_a_renamed_copy_is_refused_rather_than_split_in_two(지운_정책문서):
    """제목이 바뀐 뒤였다면 doc_key 가 안 맞습니다. 지어내면 같은 문서가 둘이 됩니다."""
    with SessionLocal() as session:
        doc = _orphan_knowledge(session)[지운_정책문서]
        doc.title = "다른 이름"
        with pytest.raises(SystemExit):
            _restore_policy_doc(session, doc)
        session.rollback()
