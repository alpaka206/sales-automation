"""Health-check module — verifies live connectivity of external dependencies."""

from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from .config import settings

logger = logging.getLogger(__name__)


class CheckResult(BaseModel):
    """Single health-check result."""

    name: str
    status: str  # PASS, WARN, FAIL
    detail: str
    latency_ms: int = 0


class HealthReport(BaseModel):
    """Aggregated health-check report."""

    checks: list[CheckResult]
    overall_status: str  # PASS, WARN, FAIL


def run_healthchecks() -> HealthReport:
    """Run all configured health-checks and return an aggregated report."""
    checks: list[CheckResult] = []

    checks.append(_check_db())
    checks.append(_check_operational_queue())
    checks.append(_check_worker_heartbeats())

    checks.append(_check_gemini())

    if settings.HUBSPOT_PRIVATE_APP_TOKEN:
        checks.append(_check_hubspot())

    checks.append(_check_smtp())

    checks.append(_check_disk_space())

    if settings.SEND_WORKER_ENABLED:
        checks.append(_check_send_quota())

    statuses = {c.status for c in checks}
    if "FAIL" in statuses:
        overall = "FAIL"
    elif "WARN" in statuses:
        overall = "WARN"
    else:
        overall = "PASS"

    return HealthReport(checks=checks, overall_status=overall)


def _check_db() -> CheckResult:
    """Verify DB connectivity with SELECT 1."""
    start = time.monotonic()
    try:
        from ..db.session import SessionLocal
        from sqlalchemy import text

        session = SessionLocal()
        session.execute(text("SELECT 1"))
        session.close()
        ms = int((time.monotonic() - start) * 1000)
        return CheckResult(name="db_connectivity", status="PASS", detail="OK", latency_ms=ms)
    except Exception as e:
        ms = int((time.monotonic() - start) * 1000)
        return CheckResult(name="db_connectivity", status="FAIL", detail=str(e)[:200], latency_ms=ms)


def _check_operational_queue() -> CheckResult:
    """Surface durable failures and a queue that has stopped draining."""
    try:
        from sqlalchemy import func, select

        from ..db.models import InboundJob, Message
        from ..db.session import SessionLocal

        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(minutes=20)
        stale_lease_before = now - timedelta(minutes=35)
        with SessionLocal() as session:
            dead = session.scalar(
                select(func.count(InboundJob.id)).where(InboundJob.status == "dead")
            ) or 0
            stale = session.scalar(
                select(func.count(InboundJob.id)).where(
                    InboundJob.status == "pending",
                    InboundJob.available_at <= stale_before,
                )
            ) or 0
            stuck = session.scalar(
                select(func.count(InboundJob.id)).where(
                    InboundJob.status == "processing",
                    InboundJob.locked_at <= stale_lease_before,
                )
            ) or 0
            delivery = session.scalar(
                select(func.count(Message.id)).where(
                    Message.status.in_(["send_failed", "delivery_unknown"])
                )
            ) or 0
            sync = session.scalar(
                select(func.count(Message.id)).where(
                    Message.status == "sent",
                    Message.post_send_synced_at.is_(None),
                    Message.post_send_sync_error.is_not(None),
                )
            ) or 0
        detail = (
            f"dead={dead}, stale={stale}, stuck={stuck}, "
            f"delivery={delivery}, sync={sync}"
        )
        return CheckResult(
            name="operational_queue",
            status="WARN" if any((dead, stale, stuck, delivery, sync)) else "PASS",
            detail=detail,
        )
    except Exception as exc:
        return CheckResult(name="operational_queue", status="WARN", detail=str(exc)[:200])


def _check_worker_heartbeats() -> CheckResult:
    """Detect an enabled background task that silently stopped looping."""
    enabled = {
        "inbound": bool(settings.INBOUND_WORKER_ENABLED),
        "poller": bool(settings.INBOUND_POLL_ENABLED),
        "send": bool(settings.SEND_WORKER_ENABLED),
    }
    active = [name for name, is_enabled in enabled.items() if is_enabled]
    if not active:
        return CheckResult(name="worker_heartbeats", status="PASS", detail="disabled")
    try:
        from sqlalchemy import select

        from ..agents.worker_heartbeat import heartbeat_kind
        from ..db.models import Event
        from ..db.session import SessionLocal

        now = datetime.now(timezone.utc)
        stale: list[str] = []
        ages: list[str] = []
        with SessionLocal() as session:
            for worker in active:
                row = session.scalar(
                    select(Event)
                    .where(Event.kind == heartbeat_kind(worker))
                    .order_by(Event.created_at.desc())
                    .limit(1)
                )
                threshold = (
                    settings.INBOUND_POLL_INTERVAL_SECONDS + 120
                    if worker == "poller"
                    else 120
                )
                if row is None:
                    stale.append(worker)
                    ages.append(f"{worker}=missing")
                    continue
                created_at = row.created_at.replace(
                    tzinfo=row.created_at.tzinfo or timezone.utc
                )
                age = max(0, int((now - created_at).total_seconds()))
                ages.append(f"{worker}={age}s")
                if age > threshold:
                    stale.append(worker)
        return CheckResult(
            name="worker_heartbeats",
            status="WARN" if stale else "PASS",
            detail=", ".join(ages),
        )
    except Exception as exc:
        return CheckResult(name="worker_heartbeats", status="WARN", detail=str(exc)[:200])


def _check_gemini() -> CheckResult:
    """Issue a minimal generation to verify Gemini (Vertex AI) credentials."""
    start = time.monotonic()
    if not settings.GOOGLE_CREDENTIALS_JSON.strip():
        return CheckResult(name="Gemini (Vertex)", status="FAIL", detail="GOOGLE_CREDENTIALS_JSON is empty", latency_ms=0)
    try:
        from ..llm.providers.gemini_vertex import call_gemini

        call_gemini("ping", max_tokens=1)
        ms = int((time.monotonic() - start) * 1000)
        return CheckResult(name="Gemini (Vertex)", status="PASS", detail="OK", latency_ms=ms)
    except Exception as e:
        ms = int((time.monotonic() - start) * 1000)
        err = str(e).lower()
        status_code = getattr(e, "code", None) or getattr(e, "status_code", None)
        if status_code in (401, 403) or "permission" in err or "credential" in err:
            return CheckResult(name="Gemini (Vertex)", status="FAIL", detail="Invalid credentials / permission denied", latency_ms=ms)
        if status_code == 429 or "429" in err or "quota" in err:
            return CheckResult(name="Gemini (Vertex)", status="WARN", detail="Rate limited / quota (429)", latency_ms=ms)
        return CheckResult(name="Gemini (Vertex)", status="FAIL", detail=str(e)[:200], latency_ms=ms)


def _check_hubspot() -> CheckResult:
    """Verify HubSpot token with a minimal contacts query."""
    start = time.monotonic()
    try:
        import httpx

        resp = httpx.get(
            "https://api.hubapi.com/crm/v3/objects/contacts",
            params={"limit": 1},
            headers={"Authorization": f"Bearer {settings.HUBSPOT_PRIVATE_APP_TOKEN}"},
            timeout=10,
        )
        ms = int((time.monotonic() - start) * 1000)
        if resp.status_code in (401, 403):
            return CheckResult(name="hubspot_token", status="FAIL", detail=f"Auth failed ({resp.status_code})", latency_ms=ms)
        if resp.status_code >= 400:
            return CheckResult(name="hubspot_token", status="WARN", detail=f"HTTP {resp.status_code}", latency_ms=ms)
        return CheckResult(name="hubspot_token", status="PASS", detail="OK", latency_ms=ms)
    except Exception as e:
        ms = int((time.monotonic() - start) * 1000)
        return CheckResult(name="hubspot_token", status="FAIL", detail=str(e)[:200], latency_ms=ms)


_SMTP_PROVIDER_MAP = {
    "smtp.gmail.com": "Gmail",
    "smtp-mail.outlook.com": "Outlook",
    "smtp-relay.brevo.com": "Brevo",
    "smtp.sendgrid.net": "SendGrid",
}


def _smtp_provider_label() -> str:
    """Return a human-readable SMTP provider name based on SMTP_HOST."""
    host = (settings.SMTP_HOST or "").lower().strip()
    return _SMTP_PROVIDER_MAP.get(host, host or "unknown")


def _check_smtp() -> CheckResult:
    """Open and close SMTP connection to verify credentials."""
    provider = _smtp_provider_label()
    start = time.monotonic()
    try:
        import smtplib

        server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10)
        server.starttls()
        if settings.SMTP_USERNAME and settings.SMTP_PASSWORD:
            server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        server.quit()
        ms = int((time.monotonic() - start) * 1000)
        return CheckResult(name="smtp_login", status="PASS", detail=f"Using {provider} SMTP", latency_ms=ms)
    except Exception as e:
        ms = int((time.monotonic() - start) * 1000)
        return CheckResult(name="smtp_login", status="FAIL", detail=f"[{provider}] {str(e)[:180]}", latency_ms=ms)


def _check_send_quota() -> CheckResult:
    """Report daily send count vs limit."""
    try:
        from ..agents.send_worker import get_daily_count

        count = get_daily_count()
        limit = settings.DAILY_SEND_LIMIT
        detail = f"{count}/{limit} sent today"
        if limit > 0 and count >= limit:
            return CheckResult(name="send_quota", status="WARN", detail=detail)
        return CheckResult(name="send_quota", status="PASS", detail=detail)
    except Exception as e:
        return CheckResult(name="send_quota", status="WARN", detail=str(e)[:200])


def _check_disk_space() -> CheckResult:
    """Warn if data/ partition has less than 500 MB free."""
    try:
        usage = shutil.disk_usage("data")
        free_mb = usage.free // (1024 * 1024)
        if free_mb < 500:
            return CheckResult(name="disk_space", status="WARN", detail=f"{free_mb} MB free (< 500 MB)")
        return CheckResult(name="disk_space", status="PASS", detail=f"{free_mb} MB free")
    except Exception as e:
        return CheckResult(name="disk_space", status="WARN", detail=str(e)[:200])
