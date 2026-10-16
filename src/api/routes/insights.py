"""영업 인사이트 — 쓰기 라우트 (2026-10-15).

읽기는 ``ui_api`` 에 있습니다(``GET /api/ui/insight-rules``). 여기는 화면이 사람의 손으로 바꾼 것을
저장하는 곳이고, 다른 쓰기와 같이 **폼 POST** 입니다 — JSON 쓰기 API 는 만들지 않습니다(콘솔의
``postForm`` 이 이 모양 하나만 보내고, 관문 · 변경 알림이 한 벌로 남습니다).

경로는 이미 등록된 ``/operations`` 아래입니다(``security.WEB_UI_PREFIXES`` · ``vite.config.ts``) —
로그인한 사람이면 누구나 고칠 수 있고(읽기 전용 viewer 는 관문이 막습니다), 누가 고쳤는지 남습니다.
옛 주소 ``GET /operations`` 의 되돌리기(``legacy_redirects``)는 그 한 주소의 GET 만 잡습니다.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from ...db.insights import RuleError, save_mark, save_rules
from ...db.session import SessionLocal
from ..auth import actor_name

router = APIRouter(tags=["web"])


def _save_rules(sent: dict[str, str], actor: str) -> dict:
    with SessionLocal() as session:
        try:
            changed = save_rules(session, sent, actor)
        except RuleError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        session.commit()
    return {"ok": True, "changed": changed}


@router.post("/operations/insights/rules")
async def save_insight_rules(request: Request):
    """「기준 수정」 줄의 저장. 칸 이름이 곧 기준의 열쇠입니다(``db/insights.RULES``).

    DB 일은 실(thread)로 밉니다 — 이 서비스는 DB 가 다른 대륙에 있어 왕복 몇 번이 1초 가까이 되고,
    ``async`` 핸들러 안에서 그대로 하면 그동안 SSE 와 다른 요청이 멈춥니다.
    """
    form = await request.form()
    sent = {key: str(value) for key, value in form.items()}
    actor = actor_name(request, fallback="") or "web"
    return await asyncio.to_thread(_save_rules, sent, actor)


def _save_mark(sent: dict[str, str], actor: str) -> dict:
    with SessionLocal() as session:
        try:
            changed = save_mark(session, sent, actor)
            session.commit()
        except RuleError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except IntegrityError:
            # 같은 줄을 두 사람이 같은 순간 처음 저장했다 — 한쪽만 들어갔다. 다시 누르면 그 행을 고친다.
            session.rollback()
            raise HTTPException(status_code=409, detail="다른 사람이 방금 같은 줄을 저장했습니다 — 다시 눌러 주세요") from None
    return {"ok": True, "changed": changed}


@router.post("/operations/insights/marks")
async def save_insight_mark(request: Request):
    """상세 패널의 상태 버튼 · 「메모 저장」. 칸은 ``tab`` · ``space_seq``(업셀링) 또는 ``contract_id`` · ``status`` ·
    ``memo`` 다섯뿐이고 그 밖의 칸은 거절합니다(``db/insights.save_mark``) — 업셀링 줄의 번호가 서버에 남는 것이
    「사용 데이터는 서버로 안 간다」의 세 번째 예외이고, 그 예외는 번호 · 상태 · 메모까지입니다."""
    form = await request.form()
    sent = {key: str(value) for key, value in form.items()}
    actor = actor_name(request, fallback="") or "web"
    return await asyncio.to_thread(_save_mark, sent, actor)
