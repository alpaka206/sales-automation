"""FastAPI entrypoint — HubSpot webhook, approval, agent run endpoints, web UI."""

from __future__ import annotations

import asyncio
import hmac
import logging
import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from ..agents.approval import ApprovalError, approve, reject
from ..common.config import settings
from ..common.logging import setup_logging
from .schemas import ApprovalBody
from .security import (
    API_SKIP_PATHS,
    LOCAL_DOC_PATHS,
    check_web_ui_basic_auth,
    is_localhost,
    is_same_origin_browser_request,
    is_web_ui_path,
    web_role_allows,
)
from .web.auth import current_user, router as auth_router
from .web.routes import router as web_router
from .webhook import router as webhook_router

setup_logging()
logger = logging.getLogger(__name__)


def validate_startup_settings() -> None:
    """Fail fast on configurations that are unsafe with this in-process runtime."""
    errors: list[str] = []
    workers_enabled = any(
        (
            settings.INBOUND_WORKER_ENABLED,
            settings.INBOUND_POLL_ENABLED,
            settings.SEND_WORKER_ENABLED,
        )
    )
    if settings.WEB_CONCURRENCY > 1 and workers_enabled:
        errors.append("WEB_CONCURRENCY must be 1 while in-process workers are enabled")

    public_url = urlsplit(settings.PUBLIC_BASE_URL) if settings.PUBLIC_BASE_URL else None
    if public_url and (public_url.scheme not in {"http", "https"} or not public_url.hostname):
        errors.append("PUBLIC_BASE_URL must be an absolute http(s) URL")
    public_host = bool(public_url and public_url.hostname not in {"localhost", "127.0.0.1", "::1"})
    public_bind = settings.APP_HOST not in {"localhost", "127.0.0.1", "::1"}
    if (public_bind or public_host) and settings.AUTH_MODE == "basic" and not settings.WEB_UI_PASSWORD:
        errors.append("WEB_UI_PASSWORD is required for a public basic-auth deployment")

    if settings.AUTH_MODE == "google_oauth":
        required = {
            "GOOGLE_OAUTH_CLIENT_ID": settings.GOOGLE_OAUTH_CLIENT_ID,
            "GOOGLE_OAUTH_CLIENT_SECRET": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "SESSION_SECRET": settings.SESSION_SECRET,
            "ALLOWED_EMAIL_DOMAIN": settings.ALLOWED_EMAIL_DOMAIN,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            errors.append("google_oauth requires " + ", ".join(missing))
    sheets_oauth_configured = bool(
        settings.GOOGLE_SHEETS_OAUTH_CLIENT_ID
        or settings.GOOGLE_SHEETS_OAUTH_CLIENT_SECRET
    )
    if sheets_oauth_configured:
        required = {
            "GOOGLE_SHEETS_OAUTH_CLIENT_ID": settings.GOOGLE_SHEETS_OAUTH_CLIENT_ID,
            "GOOGLE_SHEETS_OAUTH_CLIENT_SECRET": settings.GOOGLE_SHEETS_OAUTH_CLIENT_SECRET,
            "SESSION_SECRET": settings.SESSION_SECRET,
            "GOOGLE_TOKEN_ENCRYPTION_KEY": settings.GOOGLE_TOKEN_ENCRYPTION_KEY,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            errors.append("Google Sheets OAuth requires " + ", ".join(missing))
    if public_host and public_url and public_url.scheme != "https":
        errors.append("PUBLIC_BASE_URL must use https in production")
    if errors:
        raise RuntimeError("Unsafe startup configuration: " + "; ".join(errors))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background workers on startup, signal graceful shutdown on exit.

    The startup guard rejects multiple web processes while these in-process
    workers are enabled; scale them as separate services instead.
    """
    validate_startup_settings()
    tasks: list[asyncio.Task] = []

    if settings.INBOUND_WORKER_ENABLED:
        from ..agents.inbound_worker import run_inbound_worker

        tasks.append(asyncio.create_task(run_inbound_worker(), name="inbound_worker"))
        logger.info("Durable inbound worker background task started.")

    if settings.INBOUND_POLL_ENABLED:
        from ..agents.inbound_poller import run_poller

        tasks.append(asyncio.create_task(run_poller(), name="inbound_poller"))
        logger.info("Inbound poller background task started.")

    if settings.SEND_WORKER_ENABLED:
        from ..agents.send_worker import run_send_worker

        tasks.append(asyncio.create_task(run_send_worker(), name="send_worker"))
        logger.info("Send worker background task started.")

    try:
        yield
    finally:
        try:
            from ..agents.send_worker import request_shutdown as _send_shutdown

            _send_shutdown()
        except Exception:
            logger.exception("Send worker shutdown signal failed.")

        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await asyncio.wait_for(t, timeout=10)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        logger.info("Background tasks stopped.")


app = FastAPI(title="PERSO Inbound Console", version="0.1.0", lifespan=lifespan)
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent / "web" / "static")),
    name="static",
)
app.include_router(auth_router)
app.include_router(web_router)
app.include_router(webhook_router)


# ---------- Middleware ----------


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request.state.request_id = str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


@app.middleware("http")
async def error_capture_middleware(request: Request, call_next):
    """Record HTTP 4xx/5xx responses into the log buffer for the /logs viewer.

    Skips static assets, favicon, healthz, and 401s (the normal "log in" gate)
    so the viewer surfaces real problems, not auth-redirect noise.
    """
    response = await call_next(request)
    try:
        status = response.status_code
        path = request.url.path
        noisy = path.startswith("/static") or path in ("/favicon.ico", "/healthz")
        if status >= 400 and status != 401 and not noisy:
            from ..common.log_buffer import note_http

            note_http(request.method, path, status)
    except Exception:
        pass
    return response


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path in API_SKIP_PATHS:
        return await call_next(request)
    if path in LOCAL_DOC_PATHS:
        if is_localhost(request):
            return await call_next(request)
        return Response(status_code=404)

    # HubSpot webhook signature is verified inside the route handler — the middleware
    # must let the request through so the route can run the verifier. The fail-closed
    # behavior (reject unsigned requests) is enforced inside _verify_hubspot_signature.
    if request.url.path == "/webhook/hubspot/inbound":
        return await call_next(request)

    # Web UI routes are allowed from localhost without API token.
    if is_web_ui_path(request.url.path):
        path = request.url.path
        # The auth flow and static assets must render before sign-in.
        if path.startswith("/auth") or path.startswith("/static"):
            request.state.user = (
                current_user(request) if settings.AUTH_MODE == "google_oauth" else None
            )
            return await call_next(request)

        if not is_same_origin_browser_request(request):
            return JSONResponse(status_code=403, content={"detail": "cross-site request blocked"})

        # Google OAuth mode: require a signed session (Google sign-in, domain + allowlist).
        if settings.AUTH_MODE == "google_oauth":
            user = current_user(request)
            request.state.user = user
            if user:
                if not web_role_allows(user["role"], request.method, path):
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "insufficient role for this action"},
                    )
                return await call_next(request)
            accepts_html = "text/html" in request.headers.get("accept", "")
            if request.method == "GET" and accepts_html:
                return RedirectResponse("/auth/login", status_code=302)
            return JSONResponse(status_code=401, content={"detail": "login required"})

        # Basic mode (default): localhost, or HTTP Basic Auth when WEB_UI_PASSWORD is set.
        request.state.user = None
        if is_localhost(request):
            return await call_next(request)
        if settings.WEB_UI_PASSWORD:
            if check_web_ui_basic_auth(request):
                return await call_next(request)
            return JSONResponse(
                status_code=401,
                content={"detail": "web UI login required"},
                headers={"WWW-Authenticate": 'Basic realm="PERSO Inbound Console"'},
            )
        return JSONResponse(status_code=403, content={"detail": "web UI is localhost-only"})

    if not settings.INTERNAL_API_TOKEN:
        return JSONResponse(
            status_code=503,
            content={"detail": "INTERNAL_API_TOKEN is not configured; refusing requests."},
        )
    token = request.headers.get("X-Internal-Token", "")
    if not hmac.compare_digest(token, settings.INTERNAL_API_TOKEN):
        return JSONResponse(status_code=401, content={"detail": "invalid or missing token"})
    return await call_next(request)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.scheme == "https" or settings.PUBLIC_BASE_URL.startswith("https://"):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# ---------- Health ----------


# Accept HEAD as well as GET: uptime monitors (UptimeRobot, etc.) default to HEAD,
# and a GET-only route returns 405 → the monitor reports the service as Down even
# though it's healthy. Allowing HEAD keeps the free-tier keepalive ping working.
@app.api_route("/healthz", methods=["GET", "HEAD"])
def healthz():
    """Readiness check: traffic is accepted only when the database responds."""
    from sqlalchemy import text

    from ..db.session import engine

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Database readiness check failed.")
        return JSONResponse(status_code=503, content={"ok": False, "database": False})
    return {"ok": True, "database": True}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    from fastapi.responses import FileResponse

    logo = Path(__file__).parent / "web" / "static" / "logo.png"
    if logo.exists():
        return FileResponse(str(logo), media_type="image/png")
    return Response(status_code=404)


@app.post("/internal/healthcheck")
def internal_healthcheck() -> dict:
    """Run live connectivity checks and return the report."""
    from ..common.healthcheck import run_healthchecks

    report = run_healthchecks()
    return report.model_dump()


# ---------- Agent routes ----------


@app.post("/run/report")
def run_report(kind: str = "daily") -> dict:
    from ..agents.report import ReportAgent

    agent = ReportAgent()
    result = agent.generate(kind=kind)
    return {"status": "ok", "report": result}


@app.post("/approve/{message_id}")
async def approve_message(message_id: int, body: ApprovalBody, request: Request) -> dict:
    """Process approve/edit/reject for a pending message, then send and log.

    Authorization model:
      - The auth middleware already gates this route behind INTERNAL_API_TOKEN
        (X-Internal-Token header) for any non-localhost caller.
      - In addition, when APPROVAL_REQUIRE_TOKEN=true, the body must include a
        per-message HMAC token. This prevents a leaked X-Internal-Token from
        being replayed against arbitrary message IDs (IDOR guard).
    """
    logger.info(
        "Approval for message %d: %s by %s",
        message_id,
        body.action,
        body.approver,
    )

    if settings.APPROVAL_REQUIRE_TOKEN:
        from ..agents.approval import verify_approval_token

        if not verify_approval_token(message_id, body.token or ""):
            raise HTTPException(status_code=403, detail="invalid or missing approval token")

    try:
        if body.action in ("approve", "edit"):
            msg = approve(message_id, body.approver, body.edited_body)
        else:
            msg = reject(message_id, body.approver, body.reason)
            return {"status": msg.status, "message_id": msg.id}
    except ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if settings.SEND_WORKER_ENABLED:
        return {"status": "approved", "message_id": msg.id, "scheduled_at": str(msg.scheduled_at)}

    from ..agents.send_worker import send_approved_now

    if not await send_approved_now(message_id):
        raise HTTPException(status_code=500, detail="Send failed")

    return {"status": "sent", "message_id": msg.id}
