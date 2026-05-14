"""
Outbound agent — placeholder. The ralph loop will fill this in per
`plan/02_outbound_agent.md` and todos 008–009.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class OutboundAgent:
    def run(self, source: str, filters: dict | None = None) -> None:
        logger.info("outbound.run called (placeholder): source=%s filters=%s", source, filters)
