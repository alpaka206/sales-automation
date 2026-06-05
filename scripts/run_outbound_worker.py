"""Local outbound worker — runs prospect discovery on your machine, not on Render.

Why local: outbound discovery crawls the web (Playwright + headless Chromium) and is
CPU/RAM heavy. The deployed (Render free) instance has no browser installed and too little
memory, so it only ROUTES a natural-language request and parks a ``queued`` OutboundIntent
in the shared DB. This worker runs on a developer laptop, polls that same DB, and executes
the crawl → ICP scoring → draft → schedule, writing prospects/messages back to the DB.
Approval still happens in the deployed web UI (it reads the same DB).

Dedup is preserved: OutboundAgent skips any email already in `prospects` or `contacts`
(recorded as a `skipped_dup` audit row), and every discovered candidate is persisted.

Setup (local):
    1. Point DATABASE_URL at the SAME Postgres the deploy uses (Supabase/Neon), e.g. in .env:
           DATABASE_URL=postgresql://...supabase...
       and install the Postgres driver:  pip install -r requirements-postgres.txt
    2. Install the browser:  pip install playwright && playwright install chromium
    3. Provide the same LLM creds (GOOGLE_CREDENTIALS_JSON) the deploy uses.

Usage:
    python scripts/run_outbound_worker.py                 # poll forever (default 30s)
    python scripts/run_outbound_worker.py --once          # drain currently-queued, then exit
    python scripts/run_outbound_worker.py --interval 15   # poll every 15s
    python scripts/run_outbound_worker.py --query "유튜브에서 다국어 자막 운영하는 채널 운영자"
                                                          # route + run one query locally now
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agents.outbound.dispatcher import (  # noqa: E402
    route_and_enqueue,
    run_queued_intent,
)
from src.common.logging import setup_logging  # noqa: E402
from src.db.models import OutboundIntent  # noqa: E402
from src.db.session import SessionLocal  # noqa: E402
from src.llm.client import LLMClient  # noqa: E402

logger = logging.getLogger("outbound_worker")


def _queued_intent_ids() -> list[int]:
    """IDs of intents waiting for local execution, oldest first."""
    with SessionLocal() as session:
        rows = (
            session.query(OutboundIntent.id)
            .filter(OutboundIntent.status == "queued")
            .order_by(OutboundIntent.created_at.asc())
            .all()
        )
    return [r[0] for r in rows]


def _drain_once(llm: LLMClient) -> int:
    """Run every currently-queued intent. Returns how many were processed."""
    ids = _queued_intent_ids()
    if not ids:
        return 0
    logger.info("Found %d queued intent(s): %s", len(ids), ids)
    for iid in ids:
        result = run_queued_intent(llm, iid)
        logger.info("Intent #%d → %s (stats=%s)", iid, result.get("status"), result.get("stats"))
    return len(ids)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local outbound discovery worker")
    parser.add_argument("--once", action="store_true", help="Drain currently-queued intents then exit")
    parser.add_argument("--interval", type=int, default=30, help="Poll interval seconds (default 30)")
    parser.add_argument("--query", type=str, default=None, help="Route + run a single query locally now")
    args = parser.parse_args()

    setup_logging()
    llm = LLMClient()

    if args.query:
        enq = route_and_enqueue(llm, args.query.strip())
        logger.info("Routed query → %s", enq)
        if enq.get("status") == "queued":
            run_queued_intent(llm, enq["intent_id"])
        else:
            logger.warning("Query not queued (status=%s); nothing to run.", enq.get("status"))
        return

    if args.once:
        n = _drain_once(llm)
        logger.info("Drained %d intent(s). Exiting.", n)
        return

    logger.info("Outbound worker started — polling every %ds. Ctrl+C to stop.", args.interval)
    try:
        while True:
            try:
                _drain_once(llm)
            except Exception:
                logger.exception("Poll cycle failed; will retry next interval.")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logger.info("Outbound worker stopped.")


if __name__ == "__main__":
    main()
