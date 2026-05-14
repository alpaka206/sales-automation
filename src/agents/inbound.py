"""
Inbound agent — placeholder. The ralph loop will fill this in per
`plan/01_inbound_agent.md` and `todo/007-inbound-agent.md`.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class InboundAgent:
    def handle(self, event: dict) -> None:
        logger.info("inbound.handle called (placeholder): %s", event)
