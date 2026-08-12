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

from .models import EmailTemplate, PolicySource
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

    이메일 템플릿의 개정 이력(``email_template_revisions``)은 남습니다 — 행과 달리 그쪽은
    append-only 이고, 지운 본문을 마지막으로 들고 있는 곳입니다.
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
        session.commit()
    return removed
