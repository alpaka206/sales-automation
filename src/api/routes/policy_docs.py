"""정책 문서 — 초안이 읽는 문서의 등록부이자, 그 문서를 넣고 고치는 곳.

문서가 들어오는 길은 셋이고, 저장되는 곳은 하나입니다:

    노션 Export zip 드롭   여러 문서를 한 번에. 원본은 노션
    제목+본문 붙여넣기      한 문서를. 원본은 여기 (zip 을 만들 일이 아닐 때)
    본문 편집              이미 있는 문서를

한동안 본문이 읽기 전용이었습니다 — 원본이 노션이라 여기서 고치면 다음 업로드가 덮어쓰기
때문입니다. 그건 지금도 사실이고, 화면은 ``updated_at`` 으로 마지막 저장 시각을 말합니다.
조용히 사라지는 것이 문제이지 덮어쓰는 것 자체가 문제는 아닙니다.

어떤 문의에 어떤 문서를 쓸지는 여기서 정하지 않습니다. 모델이 문서 목록을 보고 고릅니다 —
정책도 문서 이름도 바뀌므로, 그 매핑을 코드나 등록부에 굳히면 바뀔 때마다 조용히 끊깁니다.

Every row shows when it was last read and, if the last read failed, what went wrong while
still using the previous copy. An operator must be able to see "정책이 3일째 갱신되지
않았다" without opening the server logs.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ...db.models import PolicySource
from ...db.revisions import snapshot_policy
from ...db.session import SessionLocal
from ...llm.knowledge import usage_note_from_body
from ..auth import actor_name, admin_required

logger = logging.getLogger(__name__)

router = APIRouter(tags=["web"])

MODES = (
    ("knowledge", "문의별 참고"),
    ("rules", "항상 적용"),
)
_MODE_KEYS = {key for key, _label in MODES}

# **어느 회신에 붙는 문서인가** (0108). `MODES` 와 같은 자리에 두는 이유도 같습니다 —
# 폼의 고르개와 목록의 라벨이 **한 튜플**을 읽어야, 값을 늘렸을 때 화면 한쪽만 모르는
# 상태가 안 생깁니다(`ui_api` 가 이것을 import 합니다).
#
# `mode='knowledge'` 일 때만 뜻이 있습니다. 「항상 적용」 문서는 고르는 대상이 아니라
# 모든 프롬프트에 통째로 들어갑니다.
SCOPES = (
    ("all", "모두"),
    ("first", "첫 회신에만"),
    ("followup", "후속 회신에만"),
)
_SCOPE_KEYS = {key for key, _label in SCOPES}


# **화면이 고르는 것은 이 다섯 중 하나입니다** (2026-09-10 운영자 지시: 「어느 회신에
# 이거 하나만 고르면 되도록, 그리고 문서 이름이랑 같은 줄에」).
#
# 저장되는 칸은 셋이지만(`model_access`·`mode`·`scope`) 뜻이 있는 조합은 다섯뿐이고,
# 운영자가 셋을 따로 맞춰야 하는 화면은 조합을 틀리게 만듭니다. **매핑은 여기 한 곳**
# 입니다 — 화면이 자기 사전을 들면 서버가 안 받는 값이 생깁니다(`SCOPES` 와 같은 이유).
#
# 「사람만 본다」에는 `mode`·`scope` 가 **없습니다(None)** — 바꾸지 않고 **보존**한다는
# 뜻입니다. 다시 고객용으로 돌릴 때 그 문서가 원래 어디에 붙던 것인지가 남아 있어야
# 합니다. 그래서 `placement_of` 는 `model_access` 를 **먼저** 봅니다.
PLACEMENTS = (
    ("rules_all", "모든 회신에 적용", "customer_context", "rules", "all"),
    ("rules_first", "첫 회신에만", "customer_context", "rules", "first"),
    ("rules_followup", "그 이후 회신에", "customer_context", "rules", "followup"),
    ("knowledge", "문의별 참고", "customer_context", "knowledge", "all"),
    ("human_only", "사람만 본다", "human_only", None, None),
)
_PLACEMENT_BY_KEY = {row[0]: row for row in PLACEMENTS}


def placement_of(source: PolicySource) -> str:
    """이 행이 다섯 중 어느 칸인가. 화면과 목록이 같은 답을 쓰게 하는 함수입니다."""
    if (source.model_access or "customer_context") != "customer_context":
        return "human_only"
    mode = source.mode or "knowledge"
    scope = source.scope or "all"
    for key, _label, _access, want_mode, want_scope in PLACEMENTS:
        if want_mode == mode and want_scope == scope:
            return key
    # `knowledge/first`·`knowledge/followup` 처럼 다섯에 없는 조합. **덮어쓰지 않습니다** —
    # 제목만 고친 저장이 그 문서를 조용히 「문의별 참고(모두)」로 되돌리면 안 됩니다.
    return "knowledge" if mode == "knowledge" else "rules_all"


def apply_placement(source: PolicySource, key: str) -> bool:
    """고른 칸을 행에 적습니다. 모르는 값이면 아무것도 안 합니다."""
    chosen = _PLACEMENT_BY_KEY.get(key)
    if chosen is None:
        return False
    _key, _label, access, mode, scope = chosen
    source.model_access = access
    if mode is not None:
        source.mode = mode
    if scope is not None:
        source.scope = scope
    return True


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
    scope: str = Form("all"),
    placement: str = Form(""),
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
    # 화면은 칸 하나를 보냅니다. 옛 폼(`mode`+`scope`)도 그대로 받습니다 — 새 값을
    # 모르는 곳에서 저장해도 동작이 안 바뀝니다.
    chosen = _PLACEMENT_BY_KEY.get(placement)
    if chosen is not None:
        _k, _l, access, mode, scope = chosen
        mode = mode or "knowledge"
        scope = scope or "all"
    else:
        access = "customer_context"
        if mode not in _MODE_KEYS:
            mode = "knowledge"
        if scope not in _SCOPE_KEYS:
            scope = "all"

    # **읽는 곳이 없으면 안 만듭니다** (0119). 이 한 줄을 읽는 것은 라우터 인덱스뿐이고
    # (`knowledge._build_index`), 라우터는 `mode='knowledge'` 행만 봅니다. 「항상 적용」
    # 문서에 대해 부르면 저장할 때마다 flash 를 한 번 태우고 아무도 그 값을 안 읽습니다.
    #
    # **모델을 세션 밖에서 부릅니다.** 열어 둔 세션이 저쪽 응답을 기다리면, DB 가 도쿄에
    # 있고 모델이 느린 날 그 커넥션이 몇 초씩 잡혀 있습니다.
    usage_note = (
        await asyncio.to_thread(usage_note_from_body, label, body)
        if mode == "knowledge" and access == "customer_context"
        else ""
    )

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
            scope=scope,
            model_access=access,
            body=body,
            # **「언제 쓰는가」는 본문을 읽어 만듭니다** (2026-09-10 운영자 지시: 「사람이
            # 쓰는 게 아니라 본문을 보고 알아서 정리하도록, ai 가 읽기 좋은 형식으로」).
            # 실패하면 빈 값이고 그때는 `summary_of` 가 본문 앞부분으로 떨어집니다 —
            # 문서를 저장하는 일이 모델 사정으로 막히면 안 됩니다.
            usage_note=usage_note or None,
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
    scope: str = Form(""),
    placement: str = Form(""),
):
    """본문을 고칩니다. 어떤 문서든 고칠 수 있습니다.

    노션에서 온 문서를 여기서 고치면 **같은 문서를 다시 업로드하는 순간 파일 내용으로
    돌아갑니다.** 그게 문제인 것이 아니라 조용히 그러는 것이 문제라서, 화면이 마지막
    저장 시각(``updated_at``)을 말합니다. 노션이 원본인 문서는 노션에서 고치는 편이 낫습니다.
    """
    if not admin_required(request):
        raise HTTPException(status_code=403, detail="관리자만 접근할 수 있습니다.")

    # **부르기 전에 이 문서가 어떤 문서인지 먼저 봅니다** (0119).
    #
    # 이 호출은 문서 **본문을 모델에게 보냅니다**. 그래서 「사람만 본다」로 둔 문서는
    # 여기서도 막아야 합니다 — 쿼리 필터(`_rules_from_db`·`router_docs`)와 system 미주입
    # 만으로는 **이 경로를 못 막습니다.** 그리고 그 값을 읽는 것은 라우터 인덱스뿐이라
    # `mode='knowledge'` 가 아니면 만들 이유도 없습니다.
    #
    # 짧은 읽기 세션을 따로 여는 이유: 모델을 기다리는 동안 쓰기 세션을 잡고 있으면
    # 안 됩니다(아래 주석과 같은 이유).
    with SessionLocal() as peek:
        row = peek.get(PolicySource, source_id)
        if row is None:
            raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다")
        chosen = _PLACEMENT_BY_KEY.get(placement)
        if chosen is not None:
            effective_mode = chosen[3] or row.mode
            effective_access = chosen[2]
        else:
            effective_mode = mode if mode in _MODE_KEYS else row.mode
            effective_access = row.model_access
        may_ask_model = effective_access == "customer_context" and effective_mode == "knowledge"

    # 본문이 안 왔으면 만들 것도 없습니다 — 빈 값은 「안 보냈다」입니다.
    usage_note = (
        await asyncio.to_thread(usage_note_from_body, label.strip(), body)
        if body.strip() and may_ask_model
        else ""
    )

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
        # 칸 하나로 왔으면 그것이 이깁니다. 옛 폼의 `mode`·`scope` 는 그대로 받습니다.
        if not apply_placement(source, placement) and mode in _MODE_KEYS:
            source.mode = mode
        # 빈 값은 「안 보냈다」입니다 — 안 고칩니다. `mode` 와 같은 규칙이라, 이 칸을
        # 모르는 옛 폼이 저장해도 문서가 조용히 「모두」로 되돌아가지 않습니다.
        if scope in _SCOPE_KEYS:
            source.scope = scope
        # **본문이 바뀌면 「언제 쓰는가」도 다시 만듭니다.** 본문에서 나온 값이라 본문이
        # 바뀌면 낡습니다 — 파생값을 저장할 때의 규칙이고, 이 저장소가 그 어긋남으로 이미
        # 두 번 당했습니다(`plan_starts_on` 0117 · `qualification` 0104).
        if body.strip():
            source.body = body
            # 본문이 바뀌었는데 요약을 **안 만든** 경우(사람용이거나 라우터가 안 읽는
            # 문서), 옛 요약을 남겨 두면 새 본문 옆에 낡은 한 줄이 앉습니다. 비웁니다.
            source.usage_note = usage_note or None
        session.commit()

    _publish(source_id)
    return {"ok": True}


def _publish(source_id: int) -> None:
    """저장 직후 프롬프트 캐시를 비웁니다.

    예전에는 여기서 초안이 읽는 **사본**까지 밀어 넣었습니다(``refresh_knowledge_copy``).
    사본이 없어졌으므로(2026-08-27) 밀 것이 없습니다 — 라우터가 이 행을 직접 읽습니다.
    """
    from ...llm.knowledge import reset_cache

    try:
        reset_cache()
    except Exception:
        logger.warning("Prompt cache reset failed for %s.", source_id, exc_info=True)


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
    with SessionLocal() as session:
        source = session.get(PolicySource, source_id)
        if source is not None:
            # 스냅샷이 **먼저**입니다 — 행이 사라진 뒤에는 남길 것이 없습니다.
            snapshot_policy(session, source, change_note="deleted", edited_by="web")
            session.delete(source)
            session.commit()
    return RedirectResponse("/policy-docs", status_code=303)


# 「되돌리기」가 여기 있었습니다 — 이메일 템플릿과 같은 이유로 지웠습니다(2026-08-27).


# ``_set_knowledge_status`` 가 여기 있었습니다. 정책 문서를 지우면 초안이 읽는 **사본**도
# 같이 재워야 했는데, 사본이 없어졌으므로 재울 것이 없습니다 — 이 행의 ``status`` 하나가
# 곧 「초안이 이 문서를 보는가」입니다 (2026-08-27).
