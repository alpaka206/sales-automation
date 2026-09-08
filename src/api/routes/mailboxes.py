"""메일함 연결 — 사서함마다 한 번 동의를 받아 토큰을 보관합니다 (이관 0113).

시트 연결(`customer_ops` 의 `/integrations/google-sheets/*`)과 **같은 모양**이고, 다른 점은
하나뿐입니다: 저쪽은 연결이 하나라 라우트가 어느 계정인지 물을 필요가 없었고, 이쪽은
여럿이라 주소가 값입니다.

**이 모듈로는 메일이 안 나갑니다.** 스코프가 `gmail.readonly` 하나이고 발송은 허브스팟
Conversations 입니다.
"""

from __future__ import annotations

import hmac
import logging
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ...common.config import settings
from ...integrations.gmail import (
    add_delegated_mailbox,
    authorization_url,
    delete_account,
    delegation_configured,
    exchange_code,
    set_enabled,
)
from ...integrations.google_oauth import make_state, validate_state

logger = logging.getLogger(__name__)
router = APIRouter(tags=["mailboxes"])

STATE_COOKIE = "mailbox_oauth_state"
_CALLBACK_PATH = "/integrations/mailboxes/callback"
_BACK = "/app/settings/mailboxes"


def _require_admin(request: Request) -> None:
    """**관리자만** 사서함을 붙이고 뗍니다 (2026-09-07 운영자 확인: 「어차피 다 관계자」).

    구글 로그인은 이것과 **다른 이야기**입니다 — 이 관문은 「콘솔의 이 화면에 들어올 수
    있나」이고, 어느 사서함이 붙느냐는 그 브라우저에서 **구글에 누가 로그인하느냐**가
    정합니다. 그래서 관리자가 링크를 열어 두고 당사자가 자기 계정으로 로그인하면 그
    사람의 사서함이 붙습니다.
    """
    if settings.AUTH_MODE != "google_oauth":
        return
    from ..auth import admin_required

    if not admin_required(request):
        raise HTTPException(status_code=403, detail="관리자만 메일함을 연결할 수 있습니다")


def _callback_url(request: Request) -> str:
    base = settings.PUBLIC_BASE_URL.strip().rstrip("/")
    if not base:
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        base = f"{scheme}://{request.url.netloc}"
    return f"{base}{_CALLBACK_PATH}"


@router.get("/integrations/mailboxes/connect")
async def mailbox_connect(request: Request, email: str = ""):
    """동의 화면으로 보냅니다. `email` 은 계정 고르개에 띄울 **힌트**일 뿐입니다.

    강제가 아닌 이유: 강제할 방법이 없습니다. 구글이 정하는 것은 그 브라우저에서 실제로
    로그인한 계정이고, 우리는 끝난 뒤 userinfo 로 **누가 동의했는지 되읽어** 그 주소로
    저장합니다. 그래서 화면이 저장된 주소를 크게 적습니다 — 잘못 붙었으면 그때 보입니다.
    """
    _require_admin(request)
    try:
        state = make_state()
        url = authorization_url(_callback_url(request), state, login_hint=email)
    except Exception as exc:
        logger.warning("메일함 OAuth 시작 실패", exc_info=True)
        return RedirectResponse(
            f"{_BACK}?mailbox=setup_required&detail={quote(str(exc)[:180])}", status_code=303
        )
    response = RedirectResponse(url, status_code=302)
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    response.set_cookie(
        STATE_COOKIE, state, max_age=600, httponly=True,
        secure=proto == "https", samesite="lax", path=_CALLBACK_PATH,
    )
    return response


@router.get("/integrations/mailboxes/self-connect")
async def mailbox_self_connect(request: Request, email: str = ""):
    """**본인이 자기 사서함을 붙이는 길** — 로그인 직후 여기로 옵니다 (이관 0114).

    `_require_admin` 이 **없습니다.** 관리자 관문은 「남의 사서함을 붙이고 뗄 수 있나」를
    막는 것이고, 여기는 자기 것입니다 — 관리자만 통과시키면 체크된 일반 운영자가 영영
    동의할 수 없습니다.

    그래도 아무나 오는 자리가 아닙니다: 접근 승인 화면에서 **체크된 사람**만 로그인 뒤
    이리로 보내지고, 무엇이 저장되는지는 우리가 정하는 것이 아니라 **구글에 누가
    로그인했느냐**가 정합니다(`exchange_code` 가 userinfo 로 되읽습니다).

    `login_hint` 로 방금 로그인한 주소를 띄웁니다 — 콘솔에 로그인한 계정과 사서함을
    내주는 계정이 다르면 그건 실수입니다.
    """
    try:
        state = make_state()
        url = authorization_url(_callback_url(request), state, login_hint=email)
    except Exception as exc:
        logger.warning("본인 메일함 연결 시작 실패", exc_info=True)
        return RedirectResponse(f"/?mailbox=error&detail={quote(str(exc)[:180])}", status_code=303)
    response = RedirectResponse(url, status_code=302)
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    response.set_cookie(
        STATE_COOKIE, state, max_age=600, httponly=True,
        secure=proto == "https", samesite="lax", path=_CALLBACK_PATH,
    )
    return response


@router.get(_CALLBACK_PATH)
async def mailbox_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    """동의 결과. 토큰을 받아 저장하고 **저장된 주소를 화면에 돌려줍니다.**

    **관리자 관문이 없습니다.** 이 콜백은 관리자가 시작한 연결과 본인이 로그인 뒤 하는
    연결을 겸하고, 뒤엣것은 일반 운영자입니다. 관문을 여기 두면 그 사람이 동의를 다 마친
    뒤 마지막 한 걸음에서 막힙니다 — 그리고 관문이 지킬 것도 없습니다: 무엇이 저장될지는
    우리가 정하는 것이 아니라 **구글에 누가 로그인했느냐**가 정하고(userinfo 로 되읽습니다),
    시작하는 두 라우트는 각자 자기 관문을 이미 지납니다.
    """
    from ..auth import actor_name

    try:
        expected = request.cookies.get(STATE_COOKIE, "")
        if not expected or not hmac.compare_digest(expected, state):
            raise ValueError("연결 요청 세션이 일치하지 않습니다. 다시 시도해 주세요.")
        validate_state(state)
        if error:
            raise ValueError(f"연결이 취소되었습니다: {error[:120]}")
        email = await exchange_code(
            code, _callback_url(request), connected_by=actor_name(request, "local-admin")
        )
    except Exception as exc:
        logger.warning("메일함 연결 실패", exc_info=True)
        response = RedirectResponse(
            f"{_BACK}?mailbox=error&detail={quote(str(exc)[:180])}", status_code=303
        )
        response.delete_cookie(STATE_COOKIE, path=_CALLBACK_PATH)
        return response

    response = RedirectResponse(
        f"{_BACK}?mailbox=connected&email={quote(email)}", status_code=303
    )
    response.delete_cookie(STATE_COOKIE, path=_CALLBACK_PATH)
    return response


@router.post("/integrations/mailboxes/add")
async def mailbox_add(request: Request, email: str = Form("")):
    """**주소만으로** 사서함을 추가합니다 — 그 사람이 로그인할 필요가 없습니다.

    2026-09-07 운영자 요구: 「운태가 로그인 하지 않아도 운태의 기록을 불러올 수 있도록」.
    그 길은 도메인 전체 위임 하나뿐이고, 서비스 계정이 그 사람을 가장합니다.

    **여기서는 열리는지 확인하지 않습니다.** 위임이 등록됐는지는 토큰을 실제로 받아 봐야
    알고(읽기로는 못 가립니다), 그 실패는 수집기가 행에 적어 화면이 「위임 미등록」이라고
    말합니다. 저장 한 번 하자고 외부 왕복에 매달릴 이유가 없고, 관리자 등록이 아직
    안 끝난 상태에서 주소를 미리 넣어 둘 수 있어야 합니다.
    """
    _require_admin(request)
    from ..auth import actor_name

    if not delegation_configured():
        detail = quote("서비스 계정(GOOGLE_CREDENTIALS_JSON)이 없어 주소만으로 추가할 수 없습니다.")
        return RedirectResponse(f"{_BACK}?mailbox=error&detail={detail}", status_code=303)
    try:
        add_delegated_mailbox(email, actor_name(request, "local-admin"))
    except Exception as exc:
        return RedirectResponse(
            f"{_BACK}?mailbox=error&detail={quote(str(exc)[:180])}", status_code=303
        )
    return RedirectResponse(f"{_BACK}?mailbox=added&email={quote(email.strip())}", status_code=303)


@router.post("/integrations/mailboxes/link")
async def mailbox_link(request: Request, external_id: str = Form(""),
                       conversation_id: str = Form("")):
    """「이 메일을 그 티켓에 연결할까요?」에 대한 답 (이관 0115).

    **거절도 저장합니다** — 안 적으면 그 메일이 회차마다 다시 물어봅니다.

    관리자 관문을 둡니다: 붙이면 **허브스팟 티켓에 노트가 남고**, 잘못 붙은 것은 되돌리기
    전까지 남아 아무도 눈치채지 못합니다(`attach_personal_emails` 가 여럿일 때 손대지
    않는 이유와 같습니다).
    """
    _require_admin(request)
    from ...agents.mailbox_sync import decide_link
    from ..auth import actor_name

    try:
        await decide_link(
            external_id,
            int(conversation_id) if conversation_id.strip().isdigit() else None,
            actor_name(request, "local-admin"),
        )
    except Exception as exc:
        return RedirectResponse(
            f"{_BACK}?mailbox=error&detail={quote(str(exc)[:180])}", status_code=303
        )
    return RedirectResponse(_BACK, status_code=303)


@router.post("/integrations/mailboxes/toggle")
async def mailbox_toggle(request: Request, email: str = Form(""), enabled: str = Form("")):
    """수집 대상에서 넣고 뺍니다 (운영자 지시: 「이메일을 골라서 쓸 수도 있게」).

    **연결은 안 지웁니다** — 다시 켜는 데 재동의가 필요 없어야 합니다. 빈 문자열이 아니라
    `"1"` 로 켭니다: 빈 폼 값은 중간에서 사라져 「켜기」가 조용히 「끄기」가 됩니다(수주
    고객 되돌리기에서 이미 겪은 자리입니다).
    """
    _require_admin(request)
    set_enabled(email, enabled == "1")
    return RedirectResponse(_BACK, status_code=303)


@router.post("/integrations/mailboxes/disconnect")
async def mailbox_disconnect(request: Request, email: str = Form("")):
    """연결을 끊고 토큰을 지웁니다.

    **구글 쪽 권한은 안 풉니다** — 그건 그 사람이 자기 계정 설정에서 할 일이고, 우리가
    대신 철회하면 같은 클라이언트로 연결한 다른 것까지 끊길 수 있습니다.
    """
    _require_admin(request)
    delete_account(email)
    return RedirectResponse(_BACK, status_code=303)
