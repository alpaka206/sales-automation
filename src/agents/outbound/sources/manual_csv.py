"""Manual CSV source - reads a CSV file with prospect data."""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from .base import ProspectCandidate, SourceFilters, apply_common_filters

logger = logging.getLogger(__name__)


class ManualCSVSource:
    """Reads prospects from a local CSV file."""

    name: str = "manual_csv"

    def discover(self, filters: dict | None = None) -> list[ProspectCandidate]:
        """Parse CSV at filters['path']. Expects columns: name, email, company, domain, country, notes."""
        filters = filters or {}
        path = Path(filters.get("path", ""))
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {path}")

        sf = SourceFilters(**{k: v for k, v in filters.items() if k in SourceFilters.model_fields})

        prospects: list[ProspectCandidate] = []
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                prospects.append(
                    ProspectCandidate(
                        name=row.get("name", "").strip(),
                        email=row.get("email", "").strip() or None,
                        company=row.get("company", "").strip() or None,
                        domain=row.get("domain", "").strip() or None,
                        country=row.get("country", "").strip() or None,
                        source="manual_csv",
                        source_ref=str(path),
                        extra={"notes": row.get("notes", "").strip()},
                    )
                )

        prospects = apply_common_filters(prospects, sf)
        logger.info("ManualCSV: loaded %d prospects from %s", len(prospects), path)
        return prospects
