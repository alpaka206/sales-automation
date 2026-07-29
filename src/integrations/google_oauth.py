"""Delegated Google Sheets OAuth with encrypted refresh-token storage.

The sales workbook is owned by a Workspace user and cannot always be shared
with a service account.  This module implements the narrow user-consent flow
needed by Sheets without coupling it to the web-login authentication mode.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

from ..common.config import settings
from ..db.models import IntegrationCredential
from ..db.session import SessionLocal

PROVIDER = "google_sheets_user"
SCOPES = (
    "openid",
    "email",
    "https://www.googleapis.com/auth/spreadsheets",
)
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
STATE_TTL_SECONDS = 600


class GoogleOAuthError(RuntimeError):
    """The delegated Google connection is missing, invalid, or rejected."""


def client_id() -> str:
    return (settings.GOOGLE_SHEETS_OAUTH_CLIENT_ID or settings.GOOGLE_OAUTH_CLIENT_ID).strip()


def client_secret() -> str:
    return (
        settings.GOOGLE_SHEETS_OAUTH_CLIENT_SECRET or settings.GOOGLE_OAUTH_CLIENT_SECRET
    ).strip()


def client_is_configured() -> bool:
    return bool(client_id() and client_secret())


def _state_secret() -> bytes:
    value = settings.SESSION_SECRET.strip()
    if not value:
        raise GoogleOAuthError(
            "SESSION_SECRET이 있어야 Google 연결 요청을 안전하게 검증할 수 있습니다."
        )
    return value.encode("utf-8")


def _token_encryption_secret() -> bytes:
    value = settings.GOOGLE_TOKEN_ENCRYPTION_KEY.strip()
    if not value:
        raise GoogleOAuthError(
            "GOOGLE_TOKEN_ENCRYPTION_KEY가 있어야 Google 토큰을 안전하게 저장할 수 있습니다."
        )
    return value.encode("utf-8")


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(_token_encryption_secret()).digest())
    return Fernet(key)


def _state_signature(payload: str) -> str:
    return base64.urlsafe_b64encode(
        hmac.new(_state_secret(), payload.encode("ascii"), hashlib.sha256).digest()
    ).decode("ascii").rstrip("=")


def make_state() -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {"exp": int(time.time()) + STATE_TTL_SECONDS, "nonce": secrets.token_urlsafe(18)},
            separators=(",", ":"),
        ).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"{payload}.{_state_signature(payload)}"


def validate_state(state: str) -> None:
    try:
        payload, supplied = state.split(".", 1)
        if not hmac.compare_digest(supplied, _state_signature(payload)):
            raise GoogleOAuthError("Google 연결 요청의 서명이 올바르지 않습니다.")
        decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if int(decoded["exp"]) < int(time.time()):
            raise GoogleOAuthError("Google 연결 요청이 만료되었습니다. 다시 연결해 주세요.")
    except GoogleOAuthError:
        raise
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise GoogleOAuthError("Google 연결 요청 상태가 올바르지 않습니다.") from exc


def authorization_url(redirect_uri: str, state: str) -> str:
    if not client_is_configured():
        raise GoogleOAuthError("Google OAuth 클라이언트 ID와 Secret이 설정되지 않았습니다.")
    params = {
        'client_id': client_id(),
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': ' '.join(SCOPES),
        'access_type': 'offline',
        'prompt': 'consent',
        'include_granted_scopes': 'true',
        'state': state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _encrypt(payload: dict) -> str:
    return _fernet().encrypt(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode()


def _decrypt(value: str) -> dict:
    try:
        decoded = _fernet().decrypt(value.encode("ascii"))
        payload = json.loads(decoded)
    except (InvalidToken, UnicodeError, json.JSONDecodeError) as exc:
        raise GoogleOAuthError("저장된 Google 연결 정보를 해독할 수 없습니다.") from exc
    if not isinstance(payload, dict):
        raise GoogleOAuthError("저장된 Google 연결 정보 형식이 올바르지 않습니다.")
    return payload


def env_grant() -> tuple[dict, str | None] | None:
    """The grant supplied entirely by ``.env``, or None when no token is configured.

    Only ``refresh_token`` is load-bearing. Nothing in this app ever persists a
    refreshed access token — ``google_sheets._build_service`` rebuilds credentials per
    call — so an ``expires_at`` of 0 (epoch, i.e. already expired) makes google-auth
    fetch a fresh access token on the first API call, exactly as it does for a grant
    that has been sitting in the database for an hour.

    The client id/secret still come from env either way: the refresh_token grant needs
    them. What this removes is the browser round trip, and with it the need for
    SESSION_SECRET and GOOGLE_TOKEN_ENCRYPTION_KEY on this path.
    """
    refresh_token = settings.GOOGLE_SHEETS_OAUTH_REFRESH_TOKEN.strip()
    if not refresh_token:
        return None
    payload = {
        "access_token": "",
        "refresh_token": refresh_token,
        "expires_at": 0,
        "scopes": list(SCOPES),
    }
    return payload, settings.GOOGLE_SHEETS_ACCOUNT_EMAIL.strip() or None


def load_grant() -> tuple[dict, str | None] | None:
    """The active grant: .env first, then whatever the browser flow stored.

    Env wins deliberately. A refresh token in the deployment config is the operator's
    explicit statement of which account to use, and it must survive a database reset —
    if a stale IntegrationCredential row could shadow it, "connected" would depend on
    which of the two was written last.
    """
    from_env = env_grant()
    if from_env is not None:
        return from_env
    with SessionLocal() as session:
        row = session.get(IntegrationCredential, PROVIDER)
        if row is None:
            return None
        return _decrypt(row.encrypted_payload), row.account_email


def save_grant(payload: dict, account_email: str | None) -> None:
    encrypted = _encrypt(payload)
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        row = session.get(IntegrationCredential, PROVIDER)
        if row is None:
            row = IntegrationCredential(provider=PROVIDER, encrypted_payload=encrypted)
        row.account_email = account_email
        row.encrypted_payload = encrypted
        row.updated_at = now
        session.add(row)
        session.commit()


def delete_grant() -> None:
    with SessionLocal() as session:
        row = session.get(IntegrationCredential, PROVIDER)
        if row is not None:
            session.delete(row)
            session.commit()


async def exchange_code(code: str, redirect_uri: str) -> tuple[dict, str | None]:
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
            raise GoogleOAuthError("오프라인 권한이 발급되지 않았습니다. 연결을 해제한 뒤 다시 동의해 주세요.")
        access_token = str(token.get("access_token") or "")
        info_response = await client.get(
            USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
        )
        account_email = None
        if not info_response.is_error:
            account_email = str(info_response.json().get("email") or "") or None

    expires_in = int(token.get("expires_in") or 3600)
    payload = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": int(time.time()) + expires_in,
        "scopes": str(token.get("scope") or " ".join(SCOPES)).split(),
    }
    save_grant(payload, account_email)
    return payload, account_email
