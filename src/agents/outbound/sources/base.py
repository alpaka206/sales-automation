"""Base protocol for outbound prospect sources."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class ProspectCandidate(BaseModel):
    """Raw prospect data from a source, before scoring or dedup."""

    name: str
    email: str | None = None
    company: str | None = None
    domain: str | None = None
    country: str | None = None
    source: str = ""
    source_ref: str | None = None
    extra: dict = {}


@runtime_checkable
class BaseSource(Protocol):
    name: str

    def discover(self, filters: dict | None = None) -> list[ProspectCandidate]: ...
