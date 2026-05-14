"""Initialize the database: create data/ directory and run pending migrations."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.logging import setup_logging  # noqa: E402
from src.db.migrate import run_migrations  # noqa: E402

setup_logging()
logger = logging.getLogger(__name__)


def main() -> None:
    os.makedirs("data", exist_ok=True)
    applied = run_migrations()
    if applied:
        logger.info("Applied %d migration(s): %s", len(applied), ", ".join(applied))
    else:
        logger.info("No pending migrations.")


if __name__ == "__main__":
    main()
