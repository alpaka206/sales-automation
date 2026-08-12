"""보관 기간이 지난 뒤에도 되살릴 데가 남아 있는 두 가지.

**이메일 템플릿은 여기 없습니다.** 콘솔이 7일 휴지통을 들고 있고(0070), 그 안에서는 목록의
「되돌리기」가, 그 뒤에는 아무것도 되살리지 않습니다 — 개정 이력까지 같이 청소하기 때문입니다
(``soft_delete.purge_expired``). 7일이 지나면 정말 없어진다는 것이 그 기능의 전부라, 여기에
뒷문을 하나 더 두면 운영자가 일부러 흘려보낸 것이 되살아납니다.

남은 두 가지는 휴지통이 아니라 **다른 목적의 사본**이 우연히 남는 경우입니다:

  정책 문서 · 문의별 참고  초안이 읽는 사본(``knowledge_documents``)은 등록부와 수명이
                       다릅니다. 그 사본에서 등록부 행을 되짚습니다.
  정책 문서 · 항상 적용  DB 에는 사본이 없습니다(``mode='rules'`` 는 등록부에서 직접
                       읽힙니다). 대신 **씨앗 파일이 저장소에 있습니다** —
                       ``src/db/seeds/policy/rule_*.md``, 마이그레이션 0043 이 처음
                       넣은 그 텍스트입니다. 그 뒤 콘솔에서 고친 것은 여기 없으니
                       되살아나는 것은 **원본**이고, 화면에서 한 번 훑어야 합니다.

    python scripts/restore_deleted.py                     # 되살릴 수 있는 것 목록
    python scripts/restore_deleted.py <slug>              # 사본에서 정책 문서 되살리기
    python scripts/restore_deleted.py rule_01_tone.md      # 씨앗 파일에서 규칙 되살리기

사내망에서는 DB(5432/6543)가 막혀 있습니다. 서버 셸이나 망 밖에서 실행하세요.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.models import KnowledgeDocument, PolicySource  # noqa: E402
from src.db.session import SessionLocal  # noqa: E402


def _orphan_knowledge(session) -> dict[str, KnowledgeDocument]:
    """등록부에서 사라진 정책 문서의 사본 — slug → 문서."""
    from src.agents.policy_sync import _slug_for

    live = {_slug_for(s) for s in session.query(PolicySource)}
    return {
        doc.slug: doc
        for doc in session.query(KnowledgeDocument).filter(
            KnowledgeDocument.slug.like("notion-%")
        )
        if doc.slug not in live
    }


SEEDS_DIR = Path(__file__).resolve().parents[1] / "src" / "db" / "seeds" / "policy"


def _rule_seeds() -> dict[str, Path]:
    """씨앗 파일 이름 → 경로. 0043 이 ``file:<이름>`` 을 doc_key 로 넣은 그 파일들입니다."""
    return {path.name: path for path in sorted(SEEDS_DIR.glob("rule_*.md"))}


def _restore_rule(session, path: Path) -> str:
    """씨앗 파일을 「항상 적용」 규칙으로 다시 넣습니다. 0043 이 하던 일 그대로.

    label 은 파일 이름이 아니라 **본문 첫 제목**입니다. 0043 은 파일 이름(``path.stem``)을
    썼는데, 화면에 ``rule_01_common_principles`` 라고 뜨면 운영자가 그게 뭔지 모릅니다 —
    지운 그 문서인지도 모릅니다.
    """
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    label, body = path.stem, "\n".join(lines).strip()
    if lines and lines[0].startswith("# "):
        # 제목 줄은 label 로 올립니다. 놔두면 프롬프트에 제목이 두 번 들어갑니다 —
        # `_rules_from_db` 가 본문 앞에 `# {label}` 을 다시 붙입니다.
        label, body = lines[0][2:].strip(), "\n".join(lines[1:]).strip()
    key = f"file:{path.name}"
    if session.query(PolicySource).filter_by(doc_key=key).one_or_none() is not None:
        raise SystemExit(f"'{key}' 는 이미 등록되어 있습니다. 지운 것이 아닙니다.")
    order = max(
        (s.order_index for s in session.query(PolicySource).filter_by(mode="rules")), default=0
    )
    session.add(
        PolicySource(
            label=label, title=label, doc_key=key, mode="rules",
            order_index=order + 10, body=body,
        )
    )
    session.commit()
    return (
        f"항상 적용 규칙 복원: {label} ({key})\n"
        "  ※ 마이그레이션이 처음 넣은 원본입니다. 콘솔에서 고친 내용은 들어 있지 않으니,\n"
        "    정책 문서 화면에서 한 번 읽어 보세요."
    )


def _restore_policy_doc(session, doc: KnowledgeDocument) -> str:
    """사본에서 등록부 행을 다시 만듭니다.

    doc_key 는 제목에서 나오는 값이라 지어내지 않고 되짚습니다. slug 와 안 맞으면 사본이
    만들어진 뒤 제목이 바뀐 것이라, 조용히 새 문서를 만드는 대신 멈춥니다 — 그러면 같은
    문서가 둘로 갈라져 라우터가 한 정책을 두 번 인용합니다.
    """
    from src.agents.policy_sync import SUBJECT_TAG, refresh_knowledge_copy
    from src.api.routes.policy_docs import _doc_key

    title = doc.title or ""
    key = _doc_key(title)
    if f"notion-{key[:12]}" != doc.slug:
        raise SystemExit(
            f"제목 '{title}' 이 사본의 slug({doc.slug}) 와 안 맞습니다. "
            "사본이 만들어진 뒤 제목이 바뀐 문서입니다 — 콘솔에서 직접 추가해 주세요."
        )
    subject = next(
        (t[len(SUBJECT_TAG):] for t in (doc.tags or []) if t.startswith(SUBJECT_TAG)), None
    )
    source = PolicySource(
        label=title, title=title, doc_key=key, mode="knowledge",
        body=doc.body, subject=subject, usage_note=doc.summary,
    )
    session.add(source)
    session.commit()
    refresh_knowledge_copy(source.id)
    return f"정책 문서 복원: {title} ({doc.slug})"


def main() -> None:
    wanted = sys.argv[1] if len(sys.argv) > 1 else None
    with SessionLocal() as session:
        docs = _orphan_knowledge(session)
        seeds = _rule_seeds()

        if wanted is None:
            print("되살릴 수 있는 정책 문서(문의별 참고):")
            for slug, doc in docs.items() or ():
                print(f"  {slug:40} {doc.title}")
            if not docs:
                print("  없습니다.")

            # 지금 등록된 것과 씨앗 파일을 **나란히** 보여 줍니다. "없어진 것" 으로 계산해서
            # 보여 주지 않는 이유: 씨앗 파일은 이름이 바뀐 적이 있고(rule_01_tone.md →
            # rule_01_common_principles.md, 마이그레이션 없이) 콘솔에서 제목도 바뀝니다.
            # 그러면 멀쩡히 있는 문서가 매번 "지워졌다" 로 뜹니다.
            print("\n지금 등록된 「항상 적용」 규칙:")
            live_rules = session.query(PolicySource).filter_by(mode="rules").all()
            for source in live_rules or ():
                print(f"  {source.doc_key:40} {source.label}")
            if not live_rules:
                print("  없습니다.")
            print("\n씨앗 파일에서 다시 넣을 수 있는 규칙(위와 비교해서 고르세요):")
            for name, path in seeds.items():
                first = path.read_text(encoding="utf-8").lstrip("# \n").splitlines()[0].strip()
                print(f"  {name:40} {first}")
            return

        if wanted in docs:
            print(_restore_policy_doc(session, docs[wanted]))
        elif wanted in seeds:
            print(_restore_rule(session, seeds[wanted]))
        else:
            raise SystemExit(f"'{wanted}' 를 되살릴 목록에서 못 찾았습니다. 인자 없이 실행해 보세요.")


if __name__ == "__main__":
    main()
