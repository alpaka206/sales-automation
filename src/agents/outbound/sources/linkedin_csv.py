"""LinkedIn CSV source - reads Sales Navigator or manual LinkedIn exports."""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from .base import ProspectCandidate, parse_filters, apply_common_filters

logger = logging.getLogger(__name__)

DEFAULT_COLUMN_MAP = {
    "name": ["Full Name", "Name", "full_name", "name"],
    "email": ["Email", "Email Address", "email"],
    "company": ["Company", "Company Name", "company"],
    "domain": ["Website", "Domain", "domain"],
    "country": ["Location", "Country", "country"],
    "title": ["Title", "Job Title", "title"],
    "followers": ["Followers", "followers", "Follower Count"],
}


def _find_column(headers: list[str], aliases: list[str]) -> str | None:
    lower_headers = {h.lower().strip(): h for h in headers}
    for alias in aliases:
        if alias.lower() in lower_headers:
            return lower_headers[alias.lower()]
    return None


def _parse_int(value: str) -> int | None:
    """Parse a string to int, returning None on failure."""
    try:
        return int(value.strip().replace(",", ""))
    except (ValueError, AttributeError):
        return None


class LinkedInCSVSource:
    """Reads prospects from a LinkedIn Sales Navigator CSV export."""

    name: str = "linkedin_csv"

    def discover(self, filters: dict | None = None) -> list[ProspectCandidate]:
        """Parse CSV at filters['path']."""
        filters = filters or {}
        path = Path(filters.get("path", ""))
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {path}")

        _, sf = parse_filters(filters)
        column_map = filters.get("column_map", DEFAULT_COLUMN_MAP)

        prospects: list[ProspectCandidate] = []
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []

            col_name = _find_column(headers, column_map.get("name", []))
            col_email = _find_column(headers, column_map.get("email", []))
            col_company = _find_column(headers, column_map.get("company", []))
            col_domain = _find_column(headers, column_map.get("domain", []))
            col_country = _find_column(headers, column_map.get("country", []))
            col_title = _find_column(headers, column_map.get("title", []))
            col_followers = _find_column(headers, column_map.get("followers", []))

            for row in reader:
                name = row.get(col_name, "").strip() if col_name else ""
                if not name:
                    continue

                email = row.get(col_email, "").strip() or None if col_email else None
                title = row.get(col_title, "").strip() if col_title else ""
                followers = _parse_int(row.get(col_followers, "")) if col_followers else None

                prospects.append(
                    ProspectCandidate(
                        name=name,
                        email=email,
                        company=row.get(col_company, "").strip() or None if col_company else None,
                        domain=row.get(col_domain, "").strip() or None if col_domain else None,
                        country=row.get(col_country, "").strip() or None if col_country else None,
                        role=title or None,
                        audience_size=followers,
                        source="linkedin_csv",
                        source_ref=str(path),
                        extra={"title": title},
                    )
                )

        prospects = apply_common_filters(prospects, sf)
        logger.info("LinkedInCSV: loaded %d prospects from %s", len(prospects), path)
        return prospects
