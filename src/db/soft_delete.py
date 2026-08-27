"""지운 것은 일주일 동안 되돌릴 수 있습니다.

정책 문서 하나를 실수로 지웠는데 되돌릴 방법이 없었습니다. 「항상 적용」 규칙은 DB 어디에도
사본이 없어서 — 저장소의 씨앗 파일에서 **원본**을 다시 넣는 것이 최선이었고, 그 뒤 콘솔에서
고친 내용은 영영 사라졌습니다.

그래서 삭제는 행을 지우지 않습니다. ``status='deleted'`` 로 바꾸고 ``deleted_at`` 에 시각을
박습니다. 읽는 쪽은 **이미 전부 ``status='active'`` 만 봅니다**(서명 고르개, 접수확인 조회,
``_rules_from_db``) — 그래서 새로 거를 곳이 없고, 지운 즉시 발송·초안에서 빠지는 것도 그대로
입니다. 달라지는 것은 목록뿐입니다: 지운 행이 일주일 동안 흐리게 남아 되돌릴 수 있습니다.

청소는 목록을 읽을 때 합니다. 스케줄러를 하나 더 두는 것보다 작고, 휴지통은 아무도 안 볼 때
비어 있을 필요가 없습니다 — 일주일은 최소 보관 기간이지 정확한 만료 시각이 아닙니다.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete as sql_delete
from sqlalchemy import select

from .models import DocumentRevision, EmailTemplate, PolicySource
from .revisions import EMAIL_TEMPLATE, POLICY_SOURCE
from .session import SessionLocal

RETENTION_DAYS = 7

DELETED = "deleted"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def days_left(deleted_at: datetime | None) -> int:
    """되돌릴 수 있는 날이 며칠 남았나. 화면이 「N일 후 완전 삭제」로 그립니다."""
    if deleted_at is None:
        return RETENTION_DAYS
    left = (deleted_at + timedelta(days=RETENTION_DAYS)) - utcnow()
    # 올림입니다. 남은 시간이 반나절이면 화면에는 「1일 후」라고 떠야 합니다 — 0일이라고
    # 쓰면 이미 지난 것처럼 읽히는데, 그 사이에도 되돌릴 수 있습니다.
    return max(0, math.ceil(left.total_seconds() / 86400))


def purge_expired() -> int:
    """보관 기간이 지난 것을 진짜로 지웁니다. 목록 라우트가 부릅니다.

    판본 이력도 같이 갑니다(``document_revisions``). 한동안 이력만 남겨 뒀는데, 그러면
    「7일 뒤 사라진다」가 사실이 아닙니다 — 화면에서는 없어졌는데 본문은 그대로 남아 있고,
    ``scripts/restore_deleted.py`` 가 그것을 계속 "되살릴 수 있음" 으로 보여 줍니다. 운영자가
    일부러 흘려보낸 것을 되살릴 수 있으면 그건 휴지통이 아니라 서랍입니다.

    지금 살아 있는 문서의 이력은 그대로 둡니다 — 그쪽은 「판본 기록」 화면이 읽는 것이고,
    되돌리기의 재료이기도 합니다. 기준은 하나, **가리키는 원본이 없는 이력**. 하드 삭제
    시절에 남은 고아 행들도 여기서 정리됩니다. 종류마다 원본 표가 다르므로 둘을 각각 봅니다 —
    한쪽만 보고 지우면 다른 종류의 이력이 통째로 고아로 보여 전부 사라집니다.

    정책 문서의 사본(``knowledge_documents``)은 건드리지 않습니다. 그 표에는 다른 경로로
    들어온 문서도 있어서, 짝을 못 찾은 것을 지우는 규칙이 초안이 읽는 문서를 지울 수 있습니다.
    """
    cutoff = utcnow() - timedelta(days=RETENTION_DAYS)
    removed = 0
    with SessionLocal() as session:
        for model in (EmailTemplate, PolicySource):
            result = session.execute(
                sql_delete(model).where(
                    model.status == DELETED,
                    model.deleted_at.is_not(None),
                    model.deleted_at < cutoff,
                )
            )
            removed += result.rowcount or 0
        # 위에서 방금 지운 것과, 전에 하드 삭제로 사라진 것 모두.
        for kind, model in ((EMAIL_TEMPLATE, EmailTemplate), (POLICY_SOURCE, PolicySource)):
            session.execute(
                sql_delete(DocumentRevision).where(
                    DocumentRevision.kind == kind,
                    DocumentRevision.document_id.is_not(None),
                    ~select(model.id).where(model.id == DocumentRevision.document_id).exists(),
                )
            )
        session.commit()
    return removed
