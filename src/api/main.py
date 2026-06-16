"""FastAPI entrypoint — HubSpot webhook, approval, agent run endpoints, web UI."""

from __future__ import annotations

import asyncio
import hmac
import logging
import uuid
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from ..agents.approval import ApprovalError, approve, mark_sent, reject
from ..common.config import settings
from ..common.logging import setup_logging
from .schemas import ApprovalBody, OutboundRunBody
from .security import (
    API_SKIP_PATHS,
    check_web_ui_basic_auth,
    is_localhost,
    is_web_ui_path,
)
from .web.auth import current_user, router as auth_router
from .web.routes import router as web_router
from .webhook import router as webhook_router

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background workers on startup, signal graceful shutdown on exit.

    Atomic claim in send_worker makes the loop safe under multiple processes,
    but each FastAPI worker still spins its own task — when running under
    `uvicorn --workers N` you'll get N concurrent workers competing for the
    same DB rows. Set DISABLE_BACKGROUND_WORKERS=true on N-1 of them, or run
    a single worker with `gunicorn --workers 1` + separate process for sends.
    """
    tasks: list[asyncio.Task] = []

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


app = FastAPI(title="PERSO Sales Console", version="0.1.0", lifespan=lifespan)
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
    if request.url.path in API_SKIP_PATHS:
        return await call_next(request)

    # HubSpot webhook signature is verified inside the route handler — the middleware
    # must let the request through so the route can run the verifier. The fail-closed
    # behavior (reject unsigned requests) is enforced inside _verify_hubspot_signature.
    if request.url.path == "/webhook/hubspot/inbound":
        return await call_next(request)

    # Web UI routes are allowed from localhost without API token.
    if is_web_ui_path(request.url.path):
        path = request.url.path
        # Always-public web paths: unsubscribe (signed token), the auth flow itself,
        # and static assets (needed to render the login page before sign-in).
        if (
            path.startswith("/unsubscribe")
            or path.startswith("/auth")
            or path.startswith("/static")
        ):
            request.state.user = (
                current_user(request) if settings.AUTH_MODE == "google_oauth" else None
            )
            return await call_next(request)

        # Google OAuth mode: require a signed session (Google sign-in, domain + allowlist).
        if settings.AUTH_MODE == "google_oauth":
            user = current_user(request)
            request.state.user = user
            if user:
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
                headers={"WWW-Authenticate": 'Basic realm="PERSO Sales Console"'},
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


# ---------- Health ----------


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


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


@app.post("/run/outbound")
def run_outbound(body: OutboundRunBody) -> dict:
    from ..agents.outbound import OutboundAgent

    agent = OutboundAgent()
    agent.run(source=body.source, filters=body.filters)
    return {"status": "started"}


@app.post("/run/reply_check")
def run_reply_check() -> dict:
    from ..agents.reply_check import run

    stats = run()
    return {"status": "ok", **stats}


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

    from datetime import datetime, timezone as tz

    if msg.scheduled_at is None:
        from ..db.models import Message
        from ..db.session import SessionLocal as _SL

        with _SL() as _sess:
            _m = _sess.get(Message, message_id)
            if _m:
                _m.scheduled_at = datetime.now(tz.utc)
                _sess.commit()

    if settings.SEND_WORKER_ENABLED:
        return {"status": "approved", "message_id": msg.id, "scheduled_at": str(msg.scheduled_at)}

    try:
        from ..integrations.senders import send

        await send(msg)
        mark_sent(message_id)
    except Exception:
        logger.exception("Send failed for message %d", message_id)
        raise HTTPException(status_code=500, detail="Send failed")

    try:
        contact_id = str(msg.conversation.contact_id)
    except Exception:
        # msg was returned by approve() whose session is now closed; accessing the
        # lazy `conversation` relationship on a detached instance raises. Re-resolve
        # the contact from a fresh session so a successfully-sent message still gets
        # logged to the HubSpot timeline.
        from ..db.models import Message as _MsgModel
        from ..db.session import SessionLocal as _SLc

        with _SLc() as _csess:
            _cm = _csess.get(_MsgModel, message_id)
            _conv = _cm.conversation if _cm else None
            contact_id = str(_conv.contact_id) if _conv and _conv.contact_id else ""

    try:
        from ..integrations.hubspot import HubSpotClient

        hs = HubSpotClient()
        engagement_id = await hs.create_email_engagement(
            contact_id=contact_id,
            subject=msg.subject or "",
            body=msg.body,
        )
        logger.info("Logged HubSpot engagement %s for message %d", engagement_id, message_id)
    except Exception:
        logger.warning(
            "HubSpot engagement logging failed for message %d", message_id, exc_info=True
        )
        hs = None

    if settings.HUBSPOT_UPDATE_CONTACT_INBOUND_STATUS:
        try:
            if hs is None:
                from ..integrations.hubspot import HubSpotClient

                hs = HubSpotClient()
            await hs.update_inbound_status(contact_id, "meeting_link_sent")
        except Exception:
            logger.warning("inbound_status update failed for contact %s", contact_id, exc_info=True)
            try:
                from ..db.models import Event
                from ..db.session import SessionLocal

                with SessionLocal() as session:
                    session.add(
                        Event(
                            kind="hubspot_status_update_failed",
                            payload={
                                "contact_id": contact_id,
                                "target_status": "meeting_link_sent",
                            },
                        )
                    )
                    session.commit()
            except Exception:
                logger.exception("Failed to queue status update retry")
        finally:
            if hs:
                await hs.close()
    elif hs:
        await hs.close()

    return {"status": "sent", "message_id": msg.id}
