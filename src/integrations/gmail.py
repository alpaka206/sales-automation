"""연결된 Gmail 사서함들 — 동의 받기, 토큰 살려 두기 (2026-09-07 운영자 지시, 이관 0113).

**여기는 「어느 사서함을 읽을 수 있나」까지입니다.** 무엇을 가져올지(조회 조건·중복 처리)는
아직 정해지지 않았고, 그건 이 파일이 아니라 수집기의 일입니다.

「토큰을 우리가 알아서 가져오나, 누가 넣어줘야 하나」에 대한 답: **우리가 가져옵니다.**
그 사람은 브라우저에서 로그인하고 동의를 누르기만 하면 되고, 토큰 문자열을 볼 일도
없습니다. 시트 연결(`google_oauth`)이 이미 그렇게 돌고 있고 이건 그 흐름을 계정별로
편 것입니다.

**refresh token 이 죽는 조건을 알고 만들었습니다** (구글 문서 확인):

- 6개월 미사용 → 수집이 계속 도니까 해당 없음.
- **비밀번호 변경 → Gmail 스코프가 든 토큰은 죽습니다.** 다른 스코프에는 없는 조건이라
  시트 연결은 안 겪던 일입니다. 그래서 죽은 것을 **행에 적고**(`last_error`) 화면이
  「재연결 필요」를 띄웁니다 — 로그에만 남기면 운영자는 수집이 멈춘 줄 모릅니다.
- 본인 철회 · 관리자가 서비스를 Restricted 로 설정 → 위와 같습니다.
- 계정당 클라이언트당 **100개 한도**(넘으면 오래된 것부터 경고 없이 무효) → 주소가
  기본키라 같은 사서함을 다시 연결하면 줄이 덮어써집니다. 줄이 쌓이지 않습니다.

**스코프는 `gmail.readonly` 하나입니다**(+ 누가 로그인했는지 알기 위한 openid·email).
`gmail.metadata` 는 본문을 못 읽고("but not the email body"), 발송 스코프는 이 앱이 메일을
보내는 길이 아니라 필요 없습니다 — 발송은 허브스팟 Conversations 입니다.
"""

from __future__ import annotations

import base64
import logging
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx

from ..common.config import settings
from ..common.safe_mode import guard_external_write
from ..db.models import MailboxAccount
from ..db.session import SessionLocal
from .google_oauth import (
    AUTHORIZE_URL,
    TOKEN_URL,
    USERINFO_URL,
    GoogleOAuthError,
    _decrypt,
    _encrypt,
    client_id,
    client_is_configured,
    client_secret,
)

logger = logging.getLogger(__name__)

READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
# **개인함으로 온 메일에는 개인함으로 답합니다** (2026-09-07 운영자 지시). 여기서 읽고
# Gmail 로 가서 답장을 쓰라고 하면 그건 화면을 둘로 쪼개는 것이고, 중요한 메일 한 통을
# 그냥 보내야 할 때도 있습니다.
#
# 등급이 다릅니다: `gmail.send` 는 **sensitive** 이고 `gmail.readonly` 는 restricted 입니다.
# Internal 앱이라 둘 다 검증이 필요 없습니다(2026-09-07 운영자 실측: restricted 를 붙인
# 뒤에도 "Verification is not required since your app is configured with an Internal
# user type"). **읽기만 있는 것보다 넓은 권한이므로**, 발송이 어디로 나가는지는
# `hubspot_note` 가 티켓에 남깁니다 — 안 그러면 대화가 두 갈래가 됩니다.
SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
# `openid`·`email` 은 **누가 동의했는지 되읽기 위한 것**입니다. 그 값이 곧 행의 기본키라
# 사람이 적는 값이 아니어야 합니다.
SCOPES = ("openid", "email", READ_SCOPE, SEND_SCOPE)

# 만료 직전에 미리 갱신합니다. 정확히 만료 시각에 맞춰 쓰면 요청이 날아가는 동안 만료되는
# 창이 있고, 그 실패는 「토큰이 죽었다」와 구별이 안 됩니다.
_REFRESH_MARGIN_SECONDS = 120


def authorization_url(redirect_uri: str, state: str, *, login_hint: str = "") -> str:
    """동의 화면 주소.

    **`select_account` 를 같이 보냅니다.** 관리자가 자기 계정으로 로그인해 둔 브라우저에서
    링크를 열면 구글이 계정을 안 묻고 **그 계정으로 조용히 동의**해 버립니다 — 「untae
    연결」을 눌렀는데 관리자 사서함이 붙습니다. 여기서 한 번 물어보게 하고, 그래도 틀릴 수
    있으므로 저장된 주소를 화면이 크게 적습니다.

    `login_hint` 는 계정 고르개에 그 주소를 미리 띄웁니다. **강제가 아닙니다** — 다른
    계정으로 로그인하면 그 계정이 저장되고, 그래서 화면 표시가 필요합니다.
    """
    if not client_is_configured():
        raise GoogleOAuthError("Google OAuth 클라이언트 ID와 Secret이 설정되지 않았습니다.")
    params = {
        "client_id": client_id(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        # `consent` 가 없으면 이미 동의한 계정에 refresh token 이 **다시 안 나옵니다**.
        "prompt": "select_account consent",
        "state": state,
    }
    if login_hint.strip():
        params["login_hint"] = login_hint.strip()
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def list_accounts() -> list[MailboxAccount]:
    """연결된 사서함들 — 주소순. 토큰은 안 풉니다(화면이 볼 값이 아닙니다)."""
    with SessionLocal() as session:
        return list(
            session.query(MailboxAccount).order_by(MailboxAccount.email).all()
        )


def enabled_accounts() -> list[str]:
    """지금 **수집 대상**인 주소들 — 켜져 있고 끊기지 않은 것.

    수집기가 이 목록만 봅니다. 꺼 둔 사서함(운영자가 고른 것)과 토큰이 죽은 사서함은 같은
    이유로 빠집니다: 지금 읽을 수 없거나 읽으면 안 되는 자리입니다.
    """
    with SessionLocal() as session:
        return [
            row.email
            for row in session.query(MailboxAccount)
            .filter(MailboxAccount.enabled.is_(True), MailboxAccount.last_error.is_(None))
            .order_by(MailboxAccount.email)
            .all()
        ]


def has_token(email: str) -> bool:
    """그 사서함이 이미 붙어 있나 — 로그인 흐름이 「한 번 더 물어볼까」를 이걸로 정합니다.

    **끊긴 줄은 없는 것으로 칩니다**(`last_error`). 토큰이 죽었으면 다시 받아야 하고,
    그 사람이 다음에 로그인할 때가 가장 자연스러운 기회입니다 — 관리자가 눈치채고
    부탁할 때까지 기다릴 이유가 없습니다.
    """
    with SessionLocal() as session:
        row = session.get(MailboxAccount, email.strip().lower())
    return bool(row and not row.last_error)


def set_enabled(email: str, enabled: bool) -> None:
    """켜고 끄기. **연결은 안 지웁니다** — 다시 켜는 데 재동의가 필요 없어야 합니다."""
    with SessionLocal() as session:
        row = session.get(MailboxAccount, email)
        if row is not None:
            row.enabled = enabled
            session.commit()


def delete_account(email: str) -> None:
    """연결 해제 — 토큰까지 지웁니다.

    **구글 쪽 권한은 안 풉니다.** 그건 그 사람이 자기 계정 설정에서 할 일이고, 우리가
    대신 철회하면 같은 클라이언트로 연결한 다른 것까지 같이 끊길 수 있습니다.
    """
    with SessionLocal() as session:
        row = session.get(MailboxAccount, email)
        if row is not None:
            session.delete(row)
            session.commit()


def _save(email: str, payload: dict, connected_by: str | None) -> None:
    """토큰을 넣습니다. 같은 주소면 **덮어씁니다** — 줄이 둘이면 어느 것이 사는지 모릅니다."""
    with SessionLocal() as session:
        row = session.get(MailboxAccount, email)
        if row is None:
            row = MailboxAccount(email=email, encrypted_payload="")
            session.add(row)
        row.encrypted_payload = _encrypt(payload)
        row.connected_by = connected_by
        # 다시 연결했으면 **끊긴 이유는 지웁니다** — 안 지우면 고쳐 놓고도 화면이 계속
        # 「재연결 필요」라고 적고, 수집기가 계속 건너뜁니다.
        row.last_error = None
        row.enabled = True
        # **처음 붙일 때만 찍습니다.** 재연결(비밀번호 변경 뒤)에 다시 찍으면 그 사이에
        # 온 메일이 통째로 사라집니다 — 그리고 그건 화면 어디에도 안 보입니다.
        if row.collect_from is None:
            row.collect_from = datetime.now(timezone.utc)
        session.commit()


async def exchange_code(code: str, redirect_uri: str, *, connected_by: str = "") -> str:
    """동의 코드를 토큰으로 바꿔 저장하고, **실제로 로그인한 주소**를 돌려줍니다.

    주소를 userinfo 로 되읽는 것이 핵심입니다 — 그 값이 행의 기본키이고 수집 대상입니다.
    못 읽으면 저장하지 않습니다: 누구 사서함인지 모르는 토큰은 쓸 데가 없고, 임의로
    이름을 붙이면 그 라벨이 틀렸을 때 남의 메일을 읽습니다.
    """
    if not client_is_configured():
        raise GoogleOAuthError("Google OAuth 클라이언트가 설정되지 않았습니다.")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id(),
                "client_secret": client_secret(),
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if response.is_error:
            raise GoogleOAuthError("Google 토큰 발급에 실패했습니다. OAuth 설정을 확인해 주세요.")
        token = response.json()
        refresh_token = str(token.get("refresh_token") or "")
        if not refresh_token:
            raise GoogleOAuthError(
                "오프라인 권한이 발급되지 않았습니다. 구글 계정 설정에서 이 앱의 권한을 "
                "지운 뒤 다시 동의해 주세요."
            )
        access_token = str(token.get("access_token") or "")
        info = await client.get(
            USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
        )
        email = "" if info.is_error else str(info.json().get("email") or "")

    if not email:
        raise GoogleOAuthError("어느 계정이 동의했는지 확인하지 못했습니다. 다시 시도해 주세요.")

    _save(
        email,
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": int(time.time()) + int(token.get("expires_in") or 3600),
            "scopes": str(token.get("scope") or " ".join(SCOPES)).split(),
        },
        connected_by.strip() or None,
    )
    return email


class MailboxTokenError(RuntimeError):
    """그 사서함을 지금 못 엽니다. 이유는 행(`last_error`)에도 적혀 있습니다."""


def delegation_configured() -> bool:
    """**본인 로그인 없이** 사서함을 열 수 있나 (2026-09-07 운영자 요구).

    「운태가 로그인 하지 않아도 운태의 기록을 불러올 수 있도록」 — 그 길은 **도메인 전체
    위임 하나뿐**입니다. 서비스 계정이 그 도메인의 사용자를 가장(impersonate)하고, 사람마다
    받던 동의를 **Workspace 슈퍼관리자가 한 번** 대신 해 줍니다.

    여기서 보는 것은 「서비스 계정 열쇠가 있나」까지입니다. 그 열쇠로 정말 남의 사서함을
    열 수 있는지는 **관리자가 Admin console 에 그 client ID 와 스코프를 등록했는지**에
    달려 있고, 그건 읽기로 못 가립니다 — 토큰을 실제로 받아 봐야 압니다(허브스팟 actorId
    때와 같은 자리입니다). 그래서 실패는 행에 적어 화면이 말하게 합니다.
    """
    return bool(settings.GOOGLE_CREDENTIALS_JSON.strip())


def add_delegated_mailbox(email: str, connected_by: str | None = None) -> None:
    """주소만으로 사서함을 추가합니다 — **동의 절차가 없습니다** (도메인 전체 위임).

    토큰을 안 담습니다. 열 때마다 서비스 계정이 그 사람을 가장해 새로 받으므로, 보관할
    refresh token 자체가 없습니다 — 비밀번호 변경으로 죽지도, 6개월 미사용으로 만료되지도
    않습니다. 「토큰을 안정적으로 계속 쓸 수 있게」의 가장 안정적인 답이 이것입니다.
    """
    clean = email.strip().lower()
    if "@" not in clean:
        raise GoogleOAuthError("메일 주소를 정확히 입력해 주세요.")
    _save(clean, {}, connected_by)


def _delegated_token(email: str) -> str:
    """서비스 계정이 그 사람을 가장해 받은 access token.

    실패의 대부분은 **관리자 등록이 안 된 것**입니다(`unauthorized_client`). 그 문장을
    그대로 행에 적습니다 — 「토큰이 죽었다」와 「위임이 등록 안 됐다」는 고치는 사람도
    고치는 곳도 다릅니다.
    """
    import json

    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    try:
        info = json.loads(settings.GOOGLE_CREDENTIALS_JSON.strip())
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=[READ_SCOPE]
        ).with_subject(email)
        creds.refresh(Request())
    except Exception as exc:
        detail = str(exc)[:200]
        if "unauthorized_client" in detail or "invalid_grant" in detail:
            _mark_broken(
                email,
                "도메인 전체 위임이 등록되지 않았습니다 — Admin console → 보안 → API 제어 "
                "→ 도메인 전체 위임에 이 서비스 계정의 client ID 와 gmail.readonly 를 "
                f"등록해 주세요 ({detail})",
            )
        raise MailboxTokenError(f"{email}: {detail}") from exc
    return str(creds.token)


def access_token(email: str) -> str:
    """그 사서함을 열 access token. 만료가 가까우면 갱신해 저장합니다.

    **끊긴 것을 조용히 넘기지 않습니다.** 구글이 `invalid_grant` 로 답하면 refresh token 이
    죽은 것이고(비밀번호 변경 · 본인 철회 · 관리자 제한), 그건 재동의 말고는 고칠 방법이
    없습니다. 그 사실을 `last_error` 에 적어 화면이 「재연결 필요」를 띄우고 수집기가
    건너뛰게 합니다 — 안 적으면 3시간마다 같은 실패가 로그에만 쌓이고, 운영자는 수집이
    멈춘 줄 모릅니다.

    일시적 실패(네트워크·5xx)는 **적지 않습니다.** 그건 다음 회차에 저절로 낫는 것이라
    「재연결 필요」로 적으면 멀쩡한 연결을 사람이 다시 잇게 만듭니다.
    """
    with SessionLocal() as session:
        row = session.get(MailboxAccount, email)
        if row is None:
            raise MailboxTokenError(f"{email} 은 연결되어 있지 않습니다.")
        payload = _decrypt(row.encrypted_payload)

    # **저장된 refresh token 이 없으면 위임 방식입니다** (주소만으로 추가한 줄). 설정을
    # 따로 두지 않는 이유: 한 화면에 두 방식이 섞일 수 있고, 그때 「이 줄은 어느 쪽인가」를
    # 설정 하나로 답하면 반드시 틀립니다 — 줄이 스스로 답하게 합니다.
    if not payload.get("refresh_token"):
        if not delegation_configured():
            raise MailboxTokenError(
                f"{email}: 서비스 계정(GOOGLE_CREDENTIALS_JSON)이 없어 열 수 없습니다."
            )
        return _delegated_token(email)

    if payload.get("access_token") and int(payload.get("expires_at") or 0) > (
        time.time() + _REFRESH_MARGIN_SECONDS
    ):
        return str(payload["access_token"])

    with httpx.Client(timeout=20) as client:
        response = client.post(
            TOKEN_URL,
            data={
                "client_id": client_id(),
                "client_secret": client_secret(),
                "refresh_token": payload.get("refresh_token", ""),
                "grant_type": "refresh_token",
            },
        )
    if response.is_error:
        detail = ""
        try:
            detail = str(response.json().get("error") or "")
        except Exception:
            detail = f"HTTP {response.status_code}"
        # 4xx 는 거절 — 다시 물어봐도 같은 답입니다. 5xx·네트워크는 다음 회차에 낫습니다.
        if response.status_code < 500:
            _mark_broken(email, detail)
            raise MailboxTokenError(f"{email}: 재연결이 필요합니다 ({detail}).")
        raise MailboxTokenError(f"{email}: 토큰 갱신이 일시적으로 실패했습니다 ({detail}).")

    token = response.json()
    payload["access_token"] = str(token.get("access_token") or "")
    payload["expires_at"] = int(time.time()) + int(token.get("expires_in") or 3600)
    # **새 refresh token 이 오면 그것으로 바꿉니다.** 안 오는 것이 보통이라 있을 때만.
    if token.get("refresh_token"):
        payload["refresh_token"] = str(token["refresh_token"])

    with SessionLocal() as session:
        row = session.get(MailboxAccount, email)
        if row is not None:
            row.encrypted_payload = _encrypt(payload)
            row.last_error = None
            session.commit()
    return str(payload["access_token"])


def _mark_broken(email: str, detail: str) -> None:
    with SessionLocal() as session:
        row = session.get(MailboxAccount, email)
        if row is not None:
            row.last_error = (
                f"재연결이 필요합니다 ({detail}). 비밀번호를 바꿨거나 권한을 철회하면 "
                "Gmail 토큰이 만료됩니다."
            )
            session.commit()
    logger.warning("사서함 %s: 토큰이 죽었습니다 — %s", email, detail)


def mark_polled(email: str) -> None:
    """수집기가 한 바퀴 돈 시각. 「연결은 됐는데 아무것도 안 오는」 것과 「도는데 새 메일이
    없는」 것을 화면이 구별할 수 있어야 합니다."""
    with SessionLocal() as session:
        row = session.get(MailboxAccount, email)
        if row is not None:
            row.last_polled_at = datetime.now(timezone.utc)
            session.commit()


# --------------------------------------------------------------------------- #
# 발송 — 개인 사서함에서 (2026-09-08 운영자 지시)
#
# 「개인함으로 온 메일에는 개인함으로 답한다」가 규칙입니다. 여기서 읽고 Gmail 로 가서
# 답장을 쓰라고 하면 화면을 둘로 쪼개는 것이고, 중요한 메일 한 통을 그냥 보내야 할
# 때도 있습니다.
#
# **원본이 있으면 답장, 없으면 새 메일입니다.** 허브스팟으로 온 문의에 개인 주소를 골라
# 보내는 경우가 뒤엣것입니다 — 붙일 스레드도 `In-Reply-To` 도 없으니 새 메일이 유일하게
# 정직한 결과입니다(운영자 확인).
# --------------------------------------------------------------------------- #

SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
_MESSAGE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"


def source_message(mailbox: str, gmail_message_id: str) -> dict:
    """답장할 원본의 `threadId` 와 `Message-ID`.

    **저장해 두지 않고 그때 물어봅니다.** 수집할 때 베껴 두면 열이 둘 늘고, 그 사본은
    원본과 갈릴 수 있습니다 — 그리고 이 값이 필요한 순간은 발송 직전 한 번뿐이라 왕복
    하나가 아깝지 않습니다.
    """
    with httpx.Client(
        headers={"Authorization": f"Bearer {access_token(mailbox)}"}, timeout=30.0
    ) as client:
        response = client.get(
            f"{_MESSAGE_URL}/{gmail_message_id}",
            params={"format": "metadata", "metadataHeaders": "Message-ID"},
        )
    response.raise_for_status()
    body = response.json()
    headers = {
        str(h.get("name", "")).lower(): str(h.get("value", ""))
        for h in ((body.get("payload") or {}).get("headers") or [])
    }
    return {"thread_id": str(body.get("threadId") or ""),
            "message_id": headers.get("message-id", "")}


def send_mail(
    mailbox: str,
    *,
    to: str,
    subject: str,
    html: str,
    cc: Sequence[str] = (),
    thread_id: str = "",
    in_reply_to: str = "",
) -> str:
    """그 사서함에서 한 통 보냅니다. 보낸 메시지의 Gmail id 를 돌려줍니다.

    **쓰기 관문을 가장 먼저 지납니다** — 안전 모드에서는 네트워크에 닿기도 전에
    막힙니다. 허브스팟 발송과 **같은 규칙**이어야 합니다: 문이 둘인데 관문이 하나뿐이면
    그 대전제는 대전제가 아닙니다.

    `thread_id` 와 `in_reply_to` 는 **둘 다 있어야 답장이 됩니다.** 헤더만 넣으면 받는
    쪽에서는 묶이는데 우리 사서함에서는 새 대화로 서고, `threadId` 만 넣으면 Gmail 이
    거절합니다(그 스레드의 메시지와 헤더가 안 맞습니다).
    """
    guard_external_write("gmail:send_mail")

    from email.message import EmailMessage

    mail = EmailMessage()
    mail["To"] = to
    mail["From"] = mailbox
    mail["Subject"] = subject
    if cc:
        mail["Cc"] = ", ".join(cc)
    if in_reply_to:
        mail["In-Reply-To"] = in_reply_to
        # `References` 도 같이 넣습니다 — 어떤 메일 클라이언트는 이쪽만 봅니다.
        mail["References"] = in_reply_to
    # 본문은 HTML 한 벌입니다. 글자 대역을 같이 실으면 두 벌을 맞춰 두어야 하고, 이
    # 저장소는 이미 HTML 로 보내고 있습니다(`to_html_email`).
    mail.set_content(html, subtype="html")

    payload: dict[str, str] = {
        "raw": base64.urlsafe_b64encode(mail.as_bytes()).decode("ascii")
    }
    if thread_id and in_reply_to:
        payload["threadId"] = thread_id

    with httpx.Client(
        headers={"Authorization": f"Bearer {access_token(mailbox)}"}, timeout=30.0
    ) as client:
        response = client.post(SEND_URL, json=payload)
    if response.is_error:
        raise MailboxTokenError(
            f"{mailbox}: 발송 실패 (HTTP {response.status_code}) {response.text[:200]}"
        )
    return str(response.json().get("id") or "")
