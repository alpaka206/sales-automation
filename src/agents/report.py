"""Report agent - generates daily and weekly activity reports."""

from __future__ import annotations

import logging
import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

from sqlalchemy import func

from ..common.config import settings
from ..db.models import Message
from ..db.session import SessionLocal
from ..llm.client import LLMClient
from ..llm.pricing import format_cost, get_usage_since

logger = logging.getLogger(__name__)


class ReportAgent:
    """Generates daily or weekly reports from DB data."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or LLMClient()

    def generate(self, kind: str = "daily") -> str:
        """Generate a report. Returns Markdown string."""
        session = SessionLocal()
        try:
            if kind == "weekly":
                since = datetime.now(timezone.utc) - timedelta(days=7)
                period = "Weekly"
            else:
                since = datetime.now(timezone.utc) - timedelta(days=1)
                period = "Daily"

            stats = self._gather_stats(session, since)
            narrative = self._generate_narrative(stats, period)
            report = self._format_report(stats, narrative, period, since)
            self._save_report(report, kind)
            self._distribute(report, kind)
            return report
        finally:
            session.close()

    def _gather_stats(self, session, since: datetime) -> dict:
        sent = (
            session.query(func.count(Message.id))
            .filter(Message.status == "sent", Message.sent_at >= since)
            .scalar()
            or 0
        )
        replied = (
            session.query(func.count(Message.id))
            .filter(Message.replied.is_(True), Message.created_at >= since)
            .scalar()
            or 0
        )
        pending = (
            session.query(func.count(Message.id))
            .filter(Message.status == "pending_approval")
            .scalar()
            or 0
        )
        return {
            "sent": sent,
            "replied": replied,
            "pending": pending,
        }

    def _generate_narrative(self, stats: dict, period: str) -> str:
        try:
            return self.llm.complete(
                "report/narrative",
                {
                    "period": period,
                    "sent_count": str(stats["sent"]),
                    "replied_count": str(stats["replied"]),
                    "pending_count": str(stats["pending"]),
                },
            )
        except Exception:
            logger.warning("LLM narrative failed, using template.", exc_info=True)
            return (
                f"{period} report: {stats['sent']} messages sent, "
                f"{stats['replied']} replies."
            )

    def _format_report(self, stats: dict, narrative: str, period: str, since: datetime | None = None) -> str:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            f"# {period} Report",
            f"*Generated: {now}*",
            "",
            narrative,
            "",
            "## Summary",
            f"- Messages sent: **{stats['sent']}**",
            f"- Replies received: **{stats['replied']}**",
            f"- Pending approval: **{stats['pending']}**",
            "",
        ]

        llm_section = self._llm_cost_summary(since)
        if llm_section:
            lines.append(llm_section)

        return "\n".join(lines)

    def _llm_cost_summary(self, since: datetime | None) -> str:
        """Build the LLM Usage section from usage records."""
        if since is None:
            since = datetime.now(timezone.utc) - timedelta(days=1)
        usage = get_usage_since(since)
        if usage["calls"] == 0:
            return ""
        lines = [
            "## LLM Usage",
            f"- API calls: **{usage['calls']}**",
            f"- Input tokens: **{usage['total_input']:,}**",
            f"- Output tokens: **{usage['total_output']:,}**",
            f"- Estimated cost: **{format_cost(usage['total_cost'])}**",
        ]
        for model, counts in sorted(usage["models"].items()):
            lines.append(f"  - {model}: {counts['input']:,} in / {counts['output']:,} out")
        lines.append("")
        return "\n".join(lines)

    def _save_report(self, report: str, kind: str) -> None:
        reports_dir = os.path.join("data", "reports")
        os.makedirs(reports_dir, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = os.path.join(reports_dir, f"{date_str}-{kind}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        logger.info("Report saved to %s", path)

    def _distribute(self, report: str, kind: str) -> None:
        """Best-effort email delivery. Slack is reserved for reply-ready alerts."""
        if settings.REPORT_EMAIL_TO:
            self._email_report(report, kind)

    def _email_report(self, report: str, kind: str) -> None:
        """Send the report via SMTP to REPORT_EMAIL_TO recipients."""
        # This path builds its own smtplib connection instead of going through
        # senders.send_smtp, so it has to check the delivery gate itself.
        from ..common.safe_mode import email_delivery_enabled

        if not email_delivery_enabled():
            logger.warning(
                "Report email suppressed: live email delivery is disabled."
            )
            return

        if not settings.SMTP_USERNAME or not settings.SMTP_PASSWORD:
            logger.warning("SMTP not configured — skipping report email.")
            return

        recipients = [e.strip() for e in settings.REPORT_EMAIL_TO.split(",") if e.strip()]
        if not recipients:
            return

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        subject = f"{kind.title()} Report — {date_str}"

        msg = MIMEText(report, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL}>"
        msg["To"] = ", ".join(recipients)

        try:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
                server.starttls()
                server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                server.send_message(msg)
            logger.info("Report emailed to %s.", recipients)
        except Exception:
            logger.warning("Failed to email report.", exc_info=True)
