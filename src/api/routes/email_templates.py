"""Email template CRUD web routes + revision history.

CRUD with a revision snapshot taken before each edit/delete, plus a history
view. This module owns only the editable store + UI; integration with the send
path is done elsewhere.
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from ..auth import actor_name

from ...db.email_templates import SIGNATURE_KEY_PREFIX, is_code_resolved
from ...db.models import EmailTemplate
from ...db.revisions import snapshot_template
from ...db.session import SessionLocal
from ...db.soft_delete import DELETED, utcnow

logger = logging.getLogger(__name__)

router = APIRouter(tags=["web"])


# 운영자가 키를 직접 적을 때 받아 주는 모양. 소문자·숫자·밑줄만 — 대문자나 공백이 섞이면
# ``get_email_template`` 이 찾는 이름과 눈으로는 같은데 실제로는 다른 행이 됩니다.
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,99}$")


def _generate_key(session, name: str) -> str:
    """키를 안 적었을 때 만들어 주는 이름 — **서명**으로 만듭니다.

    키는 코드 참조입니다: 답변 형식과 링크는 발송 경로가 정확한 이름으로
    꺼내 가고, 검토 화면의 고르개는 ``signature_`` 아래를 훑습니다. 이제 콘솔에서 아무 키나
    적을 수 있지만(운영자 결정), 비워 두면 **거의 언제나 서명**이라 그 접두사를 붙입니다 —
    그래야 만든 즉시 어딘가에서 읽힙니다. 다른 것을 만들 생각이면 키를 적으면 됩니다.
    """
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    # A Korean name romanizes to nothing. The key is never shown or typed, so the counter
    # below is the whole answer — there is no language to fall back to any more.
    base = base or "custom"
    candidate = f"{SIGNATURE_KEY_PREFIX}{base}"[:100]
    suffix = 2
    while session.query(EmailTemplate).filter_by(key=candidate).first():
        candidate = f"{SIGNATURE_KEY_PREFIX}{base}_{suffix}"[:100]
        suffix += 1
    return candidate


@router.post("/email-templates")
async def email_templates_create(
    request: Request,
    name: str = Form(""),
    body: str = Form(""),
    key: str = Form(""),
    language: str = Form(""),
):
    """Create a template. **The key is the operator's to choose** (2026-08-18).

    It used to be forced to ``signature_<derived>``, because a key with any other shape
    produces a row nothing in the app can ever read — the send path resolves the other
    templates by exact name, and there is no name it does not already know. That is still
    true and the operator decided to take it: free add/delete, the way 정책 문서 works.

    So the screen makes it visible instead of impossible — ``is_code_resolved`` puts a mark
    on the rows the send path actually reads, and a row without that mark is a row nothing
    will ever open. Leave the key empty and you get a signature, which is what almost every
    row created here has been.

    ``language`` is free too, except on a signature: nothing matches a signature to a
    language (0063), the operator picks one on the draft.
    """
    author = actor_name(request, fallback="") or "web"
    if not name.strip():
        return HTMLResponse(
            '<div class="text-red-600 text-sm">템플릿 이름은 필수입니다</div>',
            status_code=400,
        )
    key = key.strip().lower()
    if key and not _KEY_RE.match(key):
        return HTMLResponse(
            '<div class="text-red-600 text-sm">'
            "키는 영문 소문자로 시작하고 소문자·숫자·밑줄만 쓸 수 있습니다</div>",
            status_code=400,
        )
    if key.startswith("auto_ack"):
        return HTMLResponse(
            '<div class="text-red-600 text-sm">자동 접수확인 기능은 제거되었습니다</div>',
            status_code=400,
        )
    with SessionLocal() as session:
        if key and session.query(EmailTemplate).filter_by(key=key).first():
            return HTMLResponse(
                '<div class="text-red-600 text-sm">같은 키의 템플릿이 이미 있습니다</div>',
                status_code=400,
            )
        final_key = key or _generate_key(session, name.strip())
        tpl = EmailTemplate(
            key=final_key,
            name=name.strip(),
            language=(
                "all"
                if final_key.startswith(SIGNATURE_KEY_PREFIX)
                else (language.strip().lower() or "all")
            ),
            channel="email",
            status="active",
            version=1,
            author=author,
            body=body,
        )
        session.add(tpl)
        # 만든 직후에는 이력을 남기지 않습니다. 이 표가 들고 있는 것은 「이 판본 **이전**의
        # 것」이고, 갓 만든 행에는 이전이 없습니다 — 남기면 첫 수정 때의 스냅샷과 같은
        # 버전·같은 본문이 두 줄로 섭니다.
        session.commit()
    return HTMLResponse(
        '<div class="text-green-600 text-sm font-medium">템플릿 생성 완료</div>'
        '<script>setTimeout(()=>location.href="/email-templates",500)</script>'
    )


@router.put("/email-templates/{tpl_id}")
async def email_templates_update(
    tpl_id: int,
    request: Request,
    name: str = Form(""),
    language: str = Form(""),
    body: str = Form(""),
    subject: str = Form(""),
):
    """Update an email template, snapshotting the prior state into history.

    ``key``, ``description`` and ``status`` are deliberately absent. The key is a code
    reference the send path resolves and must not move; the description was a field
    nothing ever displayed; and only ``active`` rows are ever read, so draft/archived
    described a template that exists and does nothing. Saving revives a dormant row
    rather than leaving it unreachable now that nothing can set the value back.

    ``language`` is still posted for language-specific rows, but a SIGNATURE is pinned to
    ``all`` no matter what arrives. The screen
    stopped asking (0063), so anything posted for one is a stale value from a form that no
    longer has that field, and writing it back would resurrect a column the operator can
    neither see nor change. An ABSENT value leaves the row's language alone; it used to
    default to ``all``, which meant a save could quietly relabel a Korean row 전체.
    """
    author = actor_name(request, fallback="") or "web"
    with SessionLocal() as session:
        tpl = session.get(EmailTemplate, tpl_id)
        if not tpl:
            return HTMLResponse(
                '<div class="text-red-600 text-sm">템플릿을 찾을 수 없습니다</div>',
                status_code=404,
            )
        # Snapshot the current (pre-edit) state, then bump version and apply.
        snapshot_template(session, tpl, change_note="edited", edited_by=author)
        if name.strip():
            tpl.name = name.strip()
        is_signature = (tpl.key or "").startswith(SIGNATURE_KEY_PREFIX)
        # 값이 안 오면 **그 행의 언어를 그대로 둡니다.** 화면에 언어를 고르는 칸이 없으므로
        # (0063) 빈 값은 "전체로 바꿔 달라" 가 아니라 "이 폼은 언어를 모른다" 입니다.
        # 기본값이 ``"all"`` 이던 동안에는, 0074 가 국문 행이라고 표시해 둔
        # reply_format / meeting_link / whatsapp_link 가 콘솔에서 한 번 저장할 때마다
        # 조용히 '전체' 로 돌아갔습니다 — 그 한 글자가 영문 회신이 어느 행을 읽는지에
        # 대한 유일한 단서입니다.
        tpl.language = "all" if is_signature else (language.strip() or tpl.language or "all")
        tpl.status = "active"
        tpl.version = (tpl.version or 1) + 1
        tpl.body = body
        # 빈 문자열은 "제목 없음" 입니다 — 접수확인이 RE: 고객 제목으로 돌아갑니다.
        tpl.subject = subject.strip() or None
        session.commit()
    # 판 번호는 화면에 뜹니다 — 목록의 「v3」과 판본 기록의 정렬이 이 값입니다.
    return HTMLResponse('<div class="text-green-600 text-sm font-medium">저장 완료</div>')


@router.delete("/email-templates/{tpl_id}")
async def email_templates_delete(tpl_id: int, request: Request):
    """**어떤 행이든** 지웁니다 — 일주일 동안 되돌릴 수 있습니다 (2026-08-18).

    행을 지우지 않습니다. ``status='deleted'`` 로 바꾸고 ``deleted_at`` 을 박으면 읽는 쪽은
    전부 ``status='active'`` 만 보므로 발송·고르개에서 즉시 빠지고, 목록에는 흐리게 남아
    되돌릴 수 있습니다. 일주일 뒤 청소됩니다 — src/db/soft_delete.py.

    여기는 ``signature_`` 로 시작하는 행만 지웠습니다. 나머지는 코드가 이름으로 찾는 행이라
    지우면 기능이 없어지는 것이 아니라 **여전히 일어나는 조회의 답**이 없어지기 때문입니다:
    하드코딩된 문장으로 조용히 떨어지는 접수확인, ``{{MEETING_LINK}}`` 로 끝나는 회신, 자기를
    "배운태" 라고 소개하는 영문 메일. 되돌릴 방법도 일주일뿐입니다.

    운영자가 그 대가를 알고 자유 삭제를 선택했습니다. 그래서 막는 대신 **말합니다**:
    ``is_code_resolved`` 가 목록에 표를 달고, 삭제 확인 창이 그 행에서만 다른 문장을 띄웁니다.
    "안 쓰려면 내용을 비운다" 는 여전히 되돌리기 쉬운 쪽입니다.
    """
    editor = actor_name(request, fallback="web") or "web"
    with SessionLocal() as session:
        tpl = session.get(EmailTemplate, tpl_id)
        if not tpl:
            return HTMLResponse(
                '<div class="text-red-600 text-sm">템플릿을 찾을 수 없습니다</div>',
                status_code=404,
            )
        if is_code_resolved(tpl.key or ""):
            # 막지는 않습니다. 무엇이 없어지는지는 로그에 남습니다 — 일주일 뒤 완전히
            # 사라진 다음 "왜 접수확인이 예전 문장으로 나가지?" 를 여기서 찾습니다.
            logger.warning(
                "Email template %s (%s) — a key the send path resolves — was deleted by %s.",
                tpl.key,
                tpl.name,
                editor,
            )
        snapshot_template(session, tpl, change_note="deleted", edited_by=editor)
        tpl.status = DELETED
        tpl.deleted_at = utcnow()
        session.commit()
    return HTMLResponse(
        '<div class="text-orange-600 text-sm font-medium">삭제 완료</div>'
        '<script>setTimeout(()=>location.href="/email-templates",500)</script>'
    )


# 「되돌리기」가 여기 있었습니다. 삭제가 7일 휴지통이던 시절, 목록에 흐리게 남은 행에
# 달려 있던 버튼입니다. 지금은 지우면 목록에서 바로 사라지므로 누를 자리가 없습니다
# (2026-08-27 운영자 지시). 행과 판본 이력은 DB 에 그대로 남으니 되살릴 재료는 있습니다 —
# 다시 만들 거면 콘솔에서 새로 만들고, 본문은 판본 기록에서 가져오면 됩니다.
