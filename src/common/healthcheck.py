"""Health-check module — verifies live connectivity of external dependencies."""

from __future__ import annotations

import logging
import shutil
import time

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

    checks.append(_check_gemini())

    if settings.HUBSPOT_PRIVATE_APP_TOKEN:
        checks.append(_check_hubspot())

    if settings.EMAIL_PROVIDER == "smtp":
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
