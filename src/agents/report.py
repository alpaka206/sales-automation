"""
Report agent — placeholder. Will be implemented per `plan/03_report_agent.md`
and `todo/012-report-agent.md`.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class ReportAgent:
    def generate(self, kind: str = "daily") -> str:
        logger.info("report.generate called (placeholder): kind=%s", kind)
        return f"# {kind.title()} report (placeholder)"
