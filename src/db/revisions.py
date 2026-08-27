"""판본을 남기고 다시 꺼내는 곳. **한 곳입니다.**

운영자가 콘솔에서 고치는 글은 두 종류입니다 — 이메일 템플릿과 정책 문서. 보고 싶은 것은
같습니다: 언제, 누가, 무엇을, 그때 본문은 무엇이었나. 그래서 표도 하나(``document_revisions``),
남기는 함수도 하나, 읽는 함수도 하나입니다.

표를 종류마다 두면 화면도 라우트도 둘이 되고, 둘 중 하나에만 이력이 달리는 날이 옵니다.
실제로 그랬습니다: ``email_template_revisions`` 는 쌓이는데 읽는 화면이 없었고, 정책 문서
몫이라던 ``knowledge_document_revisions`` 는 만들어만 놓고 아무도 쓰지 않았습니다(0095·0096).

**남기는 시점은 「고치기 직전」입니다.** 그래서 맨 위 행은 「지금 본문」이 아니라 「직전
본문」이고, 되돌릴 때 꺼내는 것이 그것입니다.
"""

from __future__ import annotations

from typing import Any

from .models import DocumentRevision, EmailTemplate, PolicySource

EMAIL_TEMPLATE = DocumentRevision.KIND_EMAIL_TEMPLATE
POLICY_SOURCE = DocumentRevision.KIND_POLICY_SOURCE

KINDS = (EMAIL_TEMPLATE, POLICY_SOURCE)

# 화면이 종류를 사람 말로 그릴 때 쓰는 이름. 라우트가 종류를 검증하는 목록이기도 합니다 —
# 여기 없는 종류는 400 입니다(경로에서 온 문자열로 조회하는 자리라).
KIND_LABELS = {EMAIL_TEMPLATE: "이메일 템플릿", POLICY_SOURCE: "정책 문서"}


def snapshot_template(session, tpl: EmailTemplate, *, change_note: str, edited_by: str) -> None:
    """이메일 템플릿의 **현재** 상태를 이력에 붙입니다."""
    session.add(
        DocumentRevision(
            kind=EMAIL_TEMPLATE,
            document_id=tpl.id,
            doc_key=tpl.key or "",
            title=tpl.name or "",
            body=tpl.body or "",
            version=tpl.version or 1,
            change_note=change_note,
            edited_by=edited_by,
            extra={
                key: value
                for key, value in (
                    ("language", tpl.language),
                    ("description", tpl.description),
                )
                if value
            },
        )
    )


def snapshot_policy(session, source: PolicySource, *, change_note: str, edited_by: str) -> None:
    """정책 문서의 **현재** 상태를 이력에 붙입니다.

    ``body`` 가 NULL 인 행이 있습니다(본문 없이 등록되던 시절). 빈 문자열로 넣습니다 —
    이력에서 「그때는 비어 있었다」와 「그때 행이 없었다」는 다른 이야기입니다.
    """
    session.add(
        DocumentRevision(
            kind=POLICY_SOURCE,
            document_id=source.id,
            doc_key=source.doc_key or "",
            title=source.label or source.title or "",
            body=source.body or "",
            version=source.version or 1,
            change_note=change_note,
            edited_by=edited_by,
            extra={
                key: value
                for key, value in (
                    ("mode", source.mode),
                    ("subject", source.subject),
                    ("usage_note", source.usage_note),
                )
                if value
            },
        )
    )


def history(session, kind: str, document_id: int, limit: int = 50) -> list[dict[str, Any]]:
    """그 문서의 판본들, **최신 먼저**.

    본문까지 같이 실어 보냅니다. 목록과 본문을 두 번에 나눠 받으면 판본을 하나 눌러 볼
    때마다 왕복이 한 번이고, 판본 본문은 짧습니다 — 서식 한 벌, 서명 한 벌, 문서 한 편.
    """
    rows = (
        session.query(DocumentRevision)
        .filter(DocumentRevision.kind == kind, DocumentRevision.document_id == document_id)
        .order_by(DocumentRevision.created_at.desc(), DocumentRevision.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": row.id,
            "version": row.version,
            "title": row.title,
            "body": row.body,
            "change_note": row.change_note,
            "edited_by": row.edited_by,
            "created_at": row.created_at,
            "extra": row.extra or {},
        }
        for row in rows
    ]
