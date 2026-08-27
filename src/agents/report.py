"""Report agent - generates daily and weekly activity reports."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from ..db.models import Message
from ..db.session import SessionLocal
from ..llm.client import LLMClient

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

        # 「LLM Usage」 절이 여기 있었습니다. 사용량 기록을 그만두면서 같이 나갔습니다
        # (0095) — 읽는 사람이 없는 표를 매 호출마다 쓰고 있었습니다.

        return "\n".join(lines)

    def _save_report(self, report: str, kind: str) -> None:
        reports_dir = os.path.join("data", "reports")
        os.makedirs(reports_dir, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = os.path.join(reports_dir, f"{date_str}-{kind}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        logger.info("Report saved to %s", path)
