"""콘솔에서 실수로 지운 이메일 템플릿·정책 문서를 되살립니다.

지우는 화면은 둘인데 남는 것이 서로 달라서, 되살리는 방법도 다릅니다:

  이메일 템플릿(서명)  삭제 직전 본문이 ``email_template_revisions`` 에 통째로 남습니다
                       (``change_note='deleted'``). 온전히 복원됩니다.
  정책 문서 · 문의별 참고  등록부 행은 지워지지만 초안이 읽는 **사본**
                       (``knowledge_documents``)은 남습니다 — 삭제 라우트가 사본을 안
                       건드립니다. 그 사본에서 되살립니다.
  정책 문서 · 항상 적용  사본이 없습니다(``mode='rules'`` 는 등록부에서 직접 읽힙니다).
                       지우면 그것으로 끝이라, 여기서도 못 되살립니다.

되살릴 화면은 만들지 않았습니다. 실수로 지우는 일이 드물고, 화면 하나는 영원히 도는
코드입니다 — 필요할 때 한 번 실행하는 편이 쌉니다.

    python scripts/restore_deleted.py                     # 되살릴 수 있는 것 목록
    python scripts/restore_deleted.py <key 또는 slug>      # 하나 되살리기

사내망에서는 DB(5432/6543)가 막혀 있습니다. 서버 셸이나 망 밖에서 실행하세요.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.models import (  # noqa: E402
    EmailTemplate,
    EmailTemplateRevision,
    KnowledgeDocument,
    PolicySource,
)
from src.db.session import SessionLocal  # noqa: E402


def _deleted_templates(session) -> dict[str, EmailTemplateRevision]:
    """지워진 템플릿의 key → 마지막 스냅샷.

    key 로 찾습니다. 되살린 행은 id 가 새로 붙어서, template_id 로 찾으면 한 번 되살린
    것이 목록에 영원히 남습니다.
    """
    live = {key for (key,) in session.query(EmailTemplate.key)}
    latest: dict[str, EmailTemplateRevision] = {}
    for rev in session.query(EmailTemplateRevision).order_by(EmailTemplateRevision.id):
        if rev.key in live:
            continue
        # 마지막 스냅샷이 곧 삭제 직전 상태입니다 — 삭제가 마지막 스냅샷을 남깁니다.
        latest[rev.key] = rev
    return latest


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


def _restore_template(session, rev: EmailTemplateRevision) -> str:
    session.add(
        EmailTemplate(
            key=rev.key, name=rev.name, language=rev.language, channel=rev.channel,
            body=rev.body, description=rev.description, status=rev.status or "active",
        )
    )
    session.commit()
    return f"이메일 템플릿 복원: {rev.name} ({rev.key})"


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
        templates = _deleted_templates(session)
        docs = _orphan_knowledge(session)

        if wanted is None:
            print("되살릴 수 있는 이메일 템플릿:")
            for key, rev in templates.items() or ():
                print(f"  {key:40} {rev.name}  ({rev.edited_by} 삭제)")
            print("\n되살릴 수 있는 정책 문서(문의별 참고):")
            for slug, doc in docs.items() or ():
                print(f"  {slug:40} {doc.title}")
            if not templates and not docs:
                print("  없습니다.")
            print(
                "\n※ 「항상 적용」 정책 문서는 사본이 없어 여기 안 나옵니다 — 지우면 끝입니다."
            )
            return

        if wanted in templates:
            print(_restore_template(session, templates[wanted]))
        elif wanted in docs:
            print(_restore_policy_doc(session, docs[wanted]))
        else:
            raise SystemExit(f"'{wanted}' 를 되살릴 목록에서 못 찾았습니다. 인자 없이 실행해 보세요.")


if __name__ == "__main__":
    main()
