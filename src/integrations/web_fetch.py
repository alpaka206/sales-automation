"""Fetch homepage metadata for domain enrichment with SSRF protection."""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx

from ..common.config import settings

logger = logging.getLogger(__name__)

_MAX_RESPONSE_BYTES = 1_000_000
_MAX_REDIRECTS = 3
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}

FetchStatus = Literal["ok", "timeout", "http_4xx", "http_5xx", "blocked", "skipped"]


@dataclass
class HomepageMeta:
    """Extracted metadata from a domain's homepage."""

    title: str = ""
    description: str = ""
    og_description: str = ""
    keywords: str = ""
    body_text: str = ""
    status: FetchStatus = "ok"


def _is_private_ip(host: str) -> bool:
    """Return True if the host resolves to a private/reserved IP."""
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for _family, _type, _proto, _canonname, sockaddr in infos:
            ip = ipaddress.ip_address(sockaddr[0])
            if not ip.is_global:
                return True
    except (socket.gaierror, OSError, ValueError):
        return True
    return False


_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254", "[::1]"}


def _is_ssrf_target(domain: str) -> bool:
    """Guard against SSRF by checking for private/metadata IPs."""
    lower = domain.lower().strip()
    if lower in _BLOCKED_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(lower)
        return not ip.is_global
    except ValueError:
        pass
    return _is_private_ip(lower)


def _blocked_url(url: str) -> bool:
    """Validate every requested URL, including each redirect destination."""
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 80, 443}
        ):
            return True
    except ValueError:
        return True
    return _is_ssrf_target(parsed.hostname)


def _stream_html(client: httpx.Client, url: str) -> tuple[FetchStatus, str]:
    """Fetch bounded HTML while manually validating redirects."""
    for hop in range(_MAX_REDIRECTS + 1):
        if _blocked_url(url):
            return "blocked", ""
        with client.stream("GET", url, follow_redirects=False) as resp:
            if resp.status_code in _REDIRECT_STATUSES:
                location = resp.headers.get("location", "")
                if not location or hop == _MAX_REDIRECTS:
                    return "blocked", ""
                url = urljoin(url, location)
                continue
            if resp.status_code >= 500:
                return "http_5xx", ""
            if resp.status_code >= 400:
                return "http_4xx", ""
            if "text/html" not in resp.headers.get("content-type", "").lower():
                return "blocked", ""
            try:
                if int(resp.headers.get("content-length", "0")) > _MAX_RESPONSE_BYTES:
                    return "blocked", ""
            except ValueError:
                pass

            body = bytearray()
            for chunk in resp.iter_bytes():
                body.extend(chunk)
                if len(body) > _MAX_RESPONSE_BYTES:
                    return "blocked", ""
            encoding = resp.encoding or "utf-8"
            return "ok", bytes(body).decode(encoding, errors="replace")
    return "blocked", ""


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_META_RE = re.compile(
    r'<meta\s[^>]*?(?:name|property)\s*=\s*["\']([^"\']+)["\'][^>]*?content\s*=\s*["\']([^"\']*)["\']',
    re.IGNORECASE | re.DOTALL,
)
_META_REV_RE = re.compile(
    r'<meta\s[^>]*?content\s*=\s*["\']([^"\']*)["\'][^>]*?(?:name|property)\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE | re.DOTALL,
)


_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style|noscript)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _extract_body_text(html: str, limit: int = 2000) -> str:
    """Strip tags/scripts and return a whitespace-collapsed text snippet.

    Gives the LLM real on-page content (not just meta tags), which is what lets it
    describe what a company does when the homepage has thin/empty meta.
    """
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:limit]


def _extract_meta(html: str) -> dict[str, str]:
    """Extract title and meta tags from HTML using regex."""
    result: dict[str, str] = {}
    m = _TITLE_RE.search(html)
    if m:
        result["title"] = m.group(1).strip()

    for pattern in (_META_RE, _META_REV_RE):
        for match in pattern.finditer(html):
            if pattern is _META_REV_RE:
                content, name = match.group(1), match.group(2)
            else:
                name, content = match.group(1), match.group(2)
            name_lower = name.lower()
            if name_lower == "description":
                result.setdefault("description", content.strip())
            elif name_lower == "og:description":
                result.setdefault("og_description", content.strip())
            elif name_lower == "keywords":
                result.setdefault("keywords", content.strip())
    return result


def fetch_homepage_meta(
    domain: str,
    *,
    timeout: float | None = None,
) -> HomepageMeta:
    """Fetch homepage metadata from a domain.

    Tries https first, falls back to http. Returns HomepageMeta with
    status indicating success or failure mode.
    """
    if timeout is None:
        timeout = settings.INBOUND_DOMAIN_FETCH_TIMEOUT_SECONDS

    if _is_ssrf_target(domain):
        logger.warning("SSRF guard blocked fetch for domain: %s", domain)
        return HomepageMeta(status="blocked")

    # Browser-like headers — many real company sites sit behind a WAF/CDN that
    # 403s non-browser User-Agents, which is why even live domains used to come
    # back as http_4xx. A standard Chrome UA gets through most of them.
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    with httpx.Client(timeout=timeout, headers=headers) as client:
        for scheme in ("https", "http"):
            try:
                status, body = _stream_html(client, f"{scheme}://{domain}")
            except httpx.TimeoutException:
                if scheme == "https":
                    continue
                return HomepageMeta(status="timeout")
            except Exception:
                if scheme == "https":
                    continue
                logger.debug("Homepage fetch failed for %s", domain, exc_info=True)
                return HomepageMeta(status="blocked")

            if status == "http_4xx" and scheme == "https":
                continue
            if status != "ok":
                return HomepageMeta(status=status)

            meta = _extract_meta(body)
            return HomepageMeta(
                title=meta.get("title", ""),
                description=meta.get("description", ""),
                og_description=meta.get("og_description", ""),
                keywords=meta.get("keywords", ""),
                body_text=_extract_body_text(body),
                status="ok",
            )

    return HomepageMeta(status="timeout")
