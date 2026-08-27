"""정책 문서 — 초안이 읽는 문서의 등록부이자, 그 문서를 넣고 고치는 곳.

문서가 들어오는 길은 셋이고, 저장되는 곳은 하나입니다:

    노션 Export zip 드롭   여러 문서를 한 번에. 원본은 노션
    제목+본문 붙여넣기      한 문서를. 원본은 여기 (zip 을 만들 일이 아닐 때)
    본문 편집              이미 있는 문서를

한동안 본문이 읽기 전용이었습니다 — 원본이 노션이라 여기서 고치면 다음 업로드가 덮어쓰기
때문입니다. 그건 지금도 사실이라, 막는 대신 ``edited_at`` 을 남겨 **화면이 그렇게 말합니다.**
조용히 사라지는 것이 문제이지 덮어쓰는 것 자체가 문제는 아닙니다.

어떤 문의에 어떤 문서를 쓸지는 여기서 정하지 않습니다. 모델이 문서 목록을 보고 고릅니다 —
정책도 문서 이름도 바뀌므로, 그 매핑을 코드나 등록부에 굳히면 바뀔 때마다 조용히 끊깁니다.

Every row shows when it was last read and, if the last read failed, what went wrong while
still using the previous copy. An operator must be able to see "정책이 3일째 갱신되지
않았다" without opening the server logs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ...db.models import PolicySource
from ...db.revisions import snapshot_policy
from ...db.session import SessionLocal
from ..auth import actor_name, admin_required

logger = logging.getLogger(__name__)

router = APIRouter(tags=["web"])

MODES = (
    ("knowledge", "문의별 참고"),
    ("rules", "항상 적용"),
)
_MODE_KEYS = {key for key, _label in MODES}


def _doc_key(title: str) -> str:
    """이 문서의 신원. 제목에서 만들어 냅니다.

    **안정적으로** 만드는 것이 요점입니다 — 같은 제목으로 다시 만들면 새 행이 아니라 충돌이
    되어야, 같은 문서가 둘로 갈라져 라우터가 한 정책을 두 번 인용하는 일이 없습니다.
    """
    import hashlib

    return hashlib.sha256(title.strip().lower().encode("utf-8")).hexdigest()[:32]


@router.post("/policy-docs")
async def policy_docs_create(
    request: Request,
    label: str = Form(...),
    body: str = Form(""),
    mode: str = Form("knowledge"),
    effective_on: str = Form(""),
    subject: str = Form(""),
    usage_note: str = Form(""),
):
    """제목과 본문을 붙여넣어 문서를 하나 만듭니다.

    zip 을 만들기 귀찮을 때의 경로입니다. 한때 여기 있던 "노션 URL 등록" 폼과는 다릅니다 —
    그건 URL만 받고 본문을 가져올 수단이 없어서 영원히 빈 행을 만들었습니다(설계 문서 §2④).
    이건 본문을 같이 받으므로 만든 즉시 초안이 읽을 수 있습니다.
    """
    if not admin_required(request):
        raise HTTPException(status_code=403, detail="관리자만 접근할 수 있습니다.")

    label = label.strip()
    if not label:
        raise HTTPException(status_code=400, detail="문서 이름을 입력해 주세요")
    if mode not in _MODE_KEYS:
        mode = "knowledge"

    key = _doc_key(label)
    with SessionLocal() as session:
        if (
            session.query(PolicySource).filter(PolicySource.doc_key == key).one_or_none()
            is not None
        ):
            raise HTTPException(status_code=400, detail="같은 이름의 문서가 이미 있습니다")
        source = PolicySource(
            label=label,
            title=label,
            doc_key=key,
            mode=mode,
            body=body,
            subject=subject.strip() or None,
            usage_note=usage_note.strip() or None,
            effective_on=effective_on.strip() or None,
            edited_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(source)
        # 만든 직후에는 이력을 남기지 않습니다 — 이 표는 「이전 판본」을 들고 있고,
        # 갓 만든 행에는 이전이 없습니다(이메일 템플릿과 같은 규칙).
        session.commit()
        source_id = source.id

    _publish(source_id)
    return {"id": source_id}


@router.put("/policy-docs/{source_id}")
async def policy_docs_update(
    source_id: int,
    request: Request,
    label: str = Form(""),
    body: str = Form(""),
    mode: str = Form(""),
    effective_on: str = Form(""),
    subject: str = Form(""),
    usage_note: str = Form(""),
):
    """본문을 고칩니다. 어떤 문서든 고칠 수 있습니다.

    노션에서 온 문서를 여기서 고치면 **같은 문서를 다시 업로드하는 순간 파일 내용으로
    돌아갑니다.** 그게 문제인 것이 아니라 조용히 그러는 것이 문제라서, ``edited_at`` 을
    남기고 화면이 그렇게 말합니다. 노션이 원본인 문서는 노션에서 고치는 편이 낫습니다.
    """
    if not admin_required(request):
        raise HTTPException(status_code=403, detail="관리자만 접근할 수 있습니다.")

    with SessionLocal() as session:
        source = session.get(PolicySource, source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다")
        # 고치기 **전** 상태를 먼저 남기고, 판 번호를 올린 뒤 적용합니다.
        snapshot_policy(session, source, change_note="edited", edited_by=actor_name(request, fallback="web") or "web")
        source.version = (source.version or 1) + 1
        if label.strip():
            source.label = label.strip()
            source.title = label.strip()
        if mode in _MODE_KEYS:
            source.mode = mode
        # 빈 문자열은 "지운다" 로 읽습니다 — 그러면 edited_at 이 다시 날짜 역할을 합니다.
        source.effective_on = effective_on.strip() or None
        source.subject = subject.strip() or None
        source.usage_note = usage_note.strip() or None
        if body.strip():
            source.body = body
            source.edited_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.commit()

    _publish(source_id)
    return {"ok": True}


def _publish(source_id: int) -> None:
    """등록부에서 바뀐 것을 초안이 읽는 사본까지 밀어 넣습니다."""
    from ...agents.policy_sync import refresh_knowledge_copy

    try:
        refresh_knowledge_copy(source_id)
    except Exception:
        logger.warning("Knowledge copy refresh failed for %s.", source_id, exc_info=True)


@router.post("/policy-docs/{source_id}/delete")
async def policy_docs_delete(source_id: int):
    """지웁니다 — 일주일 동안 되돌릴 수 있습니다.

    행을 지우지 않는 이유는 이 화면에서 지운 문서 하나가 실제로 사라져 봤기 때문입니다.
    「항상 적용」 규칙은 DB 어디에도 사본이 없어서, 저장소의 씨앗 파일에서 **원본**을 다시
    넣는 것이 최선이었습니다 — 그 사이 콘솔에서 고친 내용은 돌아오지 않았습니다.

    ``mode='rules'`` 는 ``_rules_from_db`` 가 ``status='active'`` 만 읽으므로 그것으로 끝이고,
    ``mode='knowledge'`` 는 초안이 읽는 **사본**까지 같이 재워야 합니다. 안 그러면 지운
    문서를 라우터가 계속 인용합니다 — 하드 삭제 시절에도 그랬습니다(사본은 안 지웠으니까).
    """
    from ...db.soft_delete import DELETED, utcnow

    with SessionLocal() as session:
        source = session.get(PolicySource, source_id)
        if source is not None:
            snapshot_policy(session, source, change_note="deleted", edited_by="web")
            source.status = DELETED
            source.deleted_at = utcnow()
            _set_knowledge_status(session, source, "archived")
            session.commit()
    return RedirectResponse("/policy-docs", status_code=303)


# 「되돌리기」가 여기 있었습니다 — 이메일 템플릿과 같은 이유로 지웠습니다(2026-08-27).


def _set_knowledge_status(session, source: PolicySource, status: str) -> None:
    """초안이 읽는 사본을 재우거나 깨웁니다. 「문의별 참고」에만 사본이 있습니다."""
    from ...agents.policy_sync import knowledge_slug
    from ...db.models import KnowledgeDocument

    doc = (
        session.query(KnowledgeDocument)
        .filter(KnowledgeDocument.slug == knowledge_slug(source))
        .one_or_none()
    )
    if doc is not None:
        doc.status = status


