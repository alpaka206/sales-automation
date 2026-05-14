"""
Tiny migration runner. Placeholder — todo/003 will replace this with the real one
once models exist.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.config import settings  # noqa: E402


def main() -> None:
    print(f"DATABASE_URL = {settings.DATABASE_URL}")
    print("Models not yet implemented (see todo/003-db-init-and-models.md).")


if __name__ == "__main__":
    main()
