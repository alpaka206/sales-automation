"""Base protocol for outbound prospect sources."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class SourceFilters(BaseModel):
    """Shared filter surface for all outbound sources."""

    domains_allow: list[str] | None = None
    domains_block: list[str] | None = None
    countries: list[str] | None = None
    min_audience: int | None = None
    extra: dict = {}


class ProspectCandidate(BaseModel):
    """Raw prospect data from a source, before scoring or dedup."""

    name: str
    email: str | None = None
    company: str | None = None
    domain: str | None = None
    country: str | None = None
    role: str | None = None
    audience_size: int | None = None
    source: str = ""
    source_ref: str | None = None
    extra: dict = {}


def parse_filters(filters: dict | None) -> tuple[dict, "SourceFilters"]:
    """Normalize a raw filters dict and build a SourceFilters from its known keys.

    Returns ``(raw_filters, source_filters)`` — sources read source-specific keys
    (query/path/...) off the raw dict and pass ``source_filters`` to
    ``apply_common_filters``.
    """
    filters = filters or {}
    known = {k: v for k, v in filters.items() if k in SourceFilters.model_fields}
    return filters, SourceFilters(**known)


def _email_domain(email: str | None) -> str | None:
    """Extract lowercased domain from an email address."""
    if not email or "@" not in email:
        return None
    return email.rsplit("@", 1)[1].lower()


def apply_common_filters(
    candidates: list[ProspectCandidate],
    filters: SourceFilters,
) -> list[ProspectCandidate]:
    """Apply domain, country, and audience-size filters to a candidate list."""
    result: list[ProspectCandidate] = []
    allow = {d.lower() for d in filters.domains_allow} if filters.domains_allow else None
    block = {d.lower() for d in filters.domains_block} if filters.domains_block else set()
    countries = {c.lower() for c in filters.countries} if filters.countries else None

    for c in candidates:
        dom = (c.domain or "").lower() or _email_domain(c.email)

        if allow is not None and (not dom or dom not in allow):
            continue
        if dom and dom in block:
            continue

        if countries is not None:
            c_country = (c.country or "").lower()
            if not c_country or c_country not in countries:
                continue

        if filters.min_audience is not None:
            if (c.audience_size or 0) < filters.min_audience:
                continue

        result.append(c)

    return result


@runtime_checkable
class BaseSource(Protocol):
    name: str

    def discover(self, filters: dict | None = None) -> list[ProspectCandidate]: ...
