"""Issue a Google Sheets refresh token for your own account, once.

Google has no password-based API access: to act as the workbook owner the app needs a
refresh token, and only a human consent screen can mint one. This script is that
consent screen, run once from a terminal instead of from the /pipeline page. Paste the
result into ``.env`` and the app connects on its own from then on — after a redeploy,
after a database reset, on any machine.

    python scripts/connect_google_sheets.py
    python scripts/connect_google_sheets.py --write-env      # also update .env in place
    python scripts/connect_google_sheets.py --port 8765      # if 8000 is taken

Prerequisites, in Google Cloud console (see docs/설정.md for the click path):
  1. APIs & Services -> Library -> enable "Google Sheets API".
  2. OAuth consent screen -> Internal (Workspace) -> add scope .../auth/spreadsheets.
  3. Credentials -> Create -> OAuth client ID -> Web application, and register
     http://127.0.0.1:8000/integrations/google-sheets/callback as an authorized
     redirect URI (the same path the app uses, so one entry covers both).
  4. Put the client id/secret in GOOGLE_SHEETS_OAUTH_CLIENT_ID / _SECRET.

Sign in as the account that can EDIT the workbook. The token is a credential: it is
printed to your terminal and nowhere else, and it is never sent to this app's server.
"""

from __future__ import annotations

import argparse
import secrets
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from src.integrations.google_oauth import (  # noqa: E402
    AUTHORIZE_URL,
    SCOPES,
    TOKEN_URL,
    USERINFO_URL,
    client_id,
    client_secret,
)

CALLBACK_PATH = "/integrations/google-sheets/callback"
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def _use_os_trust_store() -> None:
    """Trust the OS certificate store as well as certifi's bundle.

    The ESTsoft network re-signs HTTPS with a private root that Windows trusts but
    Python's bundled certifi does not, so oauth2.googleapis.com would fail with
    CERTIFICATE_VERIFY_FAILED. Keeps verification ON — never pass verify=False.
    """
    try:
        import truststore
    except ImportError:
        return
    truststore.inject_into_ssl()


_use_os_trust_store()


class _CallbackHandler(BaseHTTPRequestHandler):
    """Catches the single redirect Google sends back, then stops."""

    result: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802 — http.server's required spelling
        parsed = urlsplit(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_response(404)
            self.end_headers()
            return
        params = parse_qs(parsed.query)
        _CallbackHandler.result = {
            "code": (params.get("code") or [""])[0],
            "state": (params.get("state") or [""])[0],
            "error": (params.get("error") or [""])[0],
        }
        body = (
            "<html><head><meta charset='utf-8'><title>Google Sheets</title></head>"
            "<body style='font-family:sans-serif;padding:3rem'>"
            "<h2>연결 완료</h2><p>터미널로 돌아가 refresh token 을 확인하세요.</p>"
            "</body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        """Silence the default stderr access log."""


def _exchange(code: str, redirect_uri: str) -> tuple[str, str | None]:
    with httpx.Client(timeout=30) as client:
        response = client.post(
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
            raise SystemExit(f"토큰 발급 실패 {response.status_code}: {response.text[:300]}")
        token = response.json()
        refresh_token = str(token.get("refresh_token") or "")
        if not refresh_token:
            raise SystemExit(
                "refresh_token 이 발급되지 않았습니다. "
                "https://myaccount.google.com/permissions 에서 이 앱의 권한을 제거한 뒤 다시 실행하세요."
            )
        info = client.get(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {token.get('access_token') or ''}"},
        )
        email = None if info.is_error else (str(info.json().get("email") or "") or None)
    return refresh_token, email


def _write_env(refresh_token: str, email: str | None) -> None:
    """Set the two keys in .env, replacing them in place if they already exist."""
    if not ENV_PATH.exists():
        raise SystemExit(f"{ENV_PATH} 이 없습니다. --write-env 없이 실행해 값을 직접 붙여넣으세요.")
    values = {
        "GOOGLE_SHEETS_OAUTH_REFRESH_TOKEN": refresh_token,
        "GOOGLE_SHEETS_ACCOUNT_EMAIL": email or "",
    }
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    for key, value in values.items():
        replacement = f"{key}={value}"
        for index, line in enumerate(lines):
            if line.split("=", 1)[0].strip() == key:
                lines[index] = replacement
                break
        else:
            lines.append(replacement)
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n.env 갱신 완료 ({ENV_PATH})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000, help="로컬 콜백 포트 (기본 8000)")
    parser.add_argument("--write-env", action="store_true", help=".env 를 직접 수정")
    args = parser.parse_args()

    if not client_id() or not client_secret():
        raise SystemExit(
            "GOOGLE_SHEETS_OAUTH_CLIENT_ID / GOOGLE_SHEETS_OAUTH_CLIENT_SECRET 를 .env 에 먼저 넣으세요.\n"
            "만드는 방법은 docs/설정.md 의 'Google Sheets를 내 계정으로 연결' 절을 보세요."
        )

    redirect_uri = f"http://127.0.0.1:{args.port}{CALLBACK_PATH}"
    state = secrets.token_urlsafe(24)
    url = f"{AUTHORIZE_URL}?" + urlencode(
        {
            "client_id": client_id(),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            # Without this Google returns a refresh token only on the very first
            # consent, so a re-run after any earlier grant would come back empty.
            "prompt": "consent",
            "state": state,
        }
    )

    print("승인된 리디렉션 URI 로 아래 값이 Google Cloud 콘솔에 등록돼 있어야 합니다:")
    print(f"    {redirect_uri}\n")
    print("브라우저에서 시트를 편집할 수 있는 계정으로 로그인하세요.")
    print("창이 열리지 않으면 이 주소를 직접 여세요:\n")
    print(url + "\n")

    try:
        server = HTTPServer(("127.0.0.1", args.port), _CallbackHandler)
    except OSError as exc:
        raise SystemExit(
            f"포트 {args.port} 를 열 수 없습니다 ({exc}). "
            f"앱이 실행 중이면 종료하거나 --port 로 다른 포트를 지정하세요."
        ) from exc

    webbrowser.open(url)
    with server:
        server.handle_request()

    result = _CallbackHandler.result
    if result.get("error"):
        raise SystemExit(f"연결이 취소되었습니다: {result['error']}")
    if result.get("state") != state:
        raise SystemExit("state 가 일치하지 않습니다. 다시 실행해 주세요.")
    if not result.get("code"):
        raise SystemExit("authorization code 를 받지 못했습니다. 다시 실행해 주세요.")

    refresh_token, email = _exchange(result["code"], redirect_uri)

    print("\n" + "=" * 72)
    print(f"연결된 계정: {email or '(확인 불가)'}")
    print("=" * 72)
    print("\n.env 에 아래 두 줄을 넣으세요 (비밀번호처럼 취급):\n")
    print(f"GOOGLE_SHEETS_OAUTH_REFRESH_TOKEN={refresh_token}")
    print(f"GOOGLE_SHEETS_ACCOUNT_EMAIL={email or ''}")
    print("\n배포 환경에는 Render 대시보드의 Environment 탭에 같은 값을 넣으세요.")

    if args.write_env:
        _write_env(refresh_token, email)


if __name__ == "__main__":
    main()
