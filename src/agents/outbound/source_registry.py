"""Registry of outbound prospect sources."""

from __future__ import annotations

from .sources.base import BaseSource
from .sources.google_search import GoogleSearchSource
from .sources.job_board import JobBoardSource
from .sources.linkedin_comments import LinkedInCommentsSource
from .sources.linkedin_csv import LinkedInCSVSource
from .sources.manual_csv import ManualCSVSource
from .sources.youtube import YouTubeSource

_SOURCES: dict[str, BaseSource] = {
    "manual_csv": ManualCSVSource(),
    "youtube": YouTubeSource(),
    "linkedin_csv": LinkedInCSVSource(),
    "linkedin_comments": LinkedInCommentsSource(),
    "google_search": GoogleSearchSource(),
    "job_board": JobBoardSource(),
}


def get_source(name: str) -> BaseSource:
    """Look up a source by name."""
    if name not in _SOURCES:
        raise KeyError(f"Unknown source: {name}. Available: {list(_SOURCES.keys())}")
    return _SOURCES[name]


def register_source(source: BaseSource) -> None:
    """Register a new source at runtime."""
    _SOURCES[source.name] = source
