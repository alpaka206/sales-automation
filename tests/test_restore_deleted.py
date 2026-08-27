"""scripts/restore_deleted.py — 저장소의 씨앗 파일에서 「항상 적용」 규칙을 다시 넣는 길.

**되살릴 데가 여기밖에 없는 경우는 이제 하나뿐입니다.** 2026-08-27 부터 콘솔에서 지운 것은
행이 남습니다 — 목록에서만 사라지고 DB 에서는 안 사라집니다. 그래서 이메일 템플릿이든 정책
문서든 되살리는 길은 그 행의 ``status`` 를 되돌리는 것이고, 본문은 판본 기록에 있습니다.

여기 「사본에서 정책 문서 되짚기」 테스트가 둘 있었습니다. 그 사본 표(``knowledge_documents``)
가 없어졌고(0098), 그 길이 존재하던 이유(등록부 행이 하드 삭제로 사라짐)도 같이 없어졌습니다.
"""

from __future__ import annotations

import pytest

from scripts.restore_deleted import _restore_rule, _rule_seeds
from src.db.models import PolicySource
from src.db.session import SessionLocal


def test_an_always_applied_rule_comes_back_from_its_seed_file():
    """「항상 적용」은 DB 에 사본이 없는 대신 씨앗 파일이 저장소에 있습니다 — 0043 이 처음
    넣은 그 텍스트입니다. 되살아나는 것은 **원본**이라, 그 뒤 콘솔에서 고친 내용은 돌아오지
    않습니다."""
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


def test_the_copy_recovery_path_is_gone():
    """사본에서 등록부를 되짚던 길입니다. 사본 표가 없어졌으므로 되짚을 것이 없고, 지운
    문서는 이제 행이 남으므로 되짚을 이유도 없습니다."""
    import scripts.restore_deleted as restore

    for gone in ("_orphan_knowledge", "_restore_policy_doc"):
        assert not hasattr(restore, gone), gone
