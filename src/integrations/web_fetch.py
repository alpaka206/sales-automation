"""Fetch homepage metadata for domain enrichment with SSRF protection."""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from typing import Literal

import httpx

from ..common.config import settings

logger = logging.getLogger(__name__)

_MAX_RESPONSE_BYTES = 1_000_000
_MAX_REDIRECTS = 3

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
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
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
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        pass
    return _is_private_ip(lower)


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

    for scheme in ("https", "http"):
        url = f"{scheme}://{domain}"
        try:
            with httpx.Client(
                timeout=timeout,
                follow_redirects=True,
                max_redirects=_MAX_REDIRECTS,
                headers=headers,
            ) as client:
                resp = client.get(url)

            content_type = resp.headers.get("content-type", "")
            if "text/html" not in content_type.lower():
                return HomepageMeta(status="blocked")

            body = resp.text[:_MAX_RESPONSE_BYTES]

            if resp.status_code >= 500:
                return HomepageMeta(status="http_5xx")
            if resp.status_code >= 400:
                if scheme == "https":
                    continue
                return HomepageMeta(status="http_4xx")

            meta = _extract_meta(body)
            return HomepageMeta(
                title=meta.get("title", ""),
                description=meta.get("description", ""),
                og_description=meta.get("og_description", ""),
                keywords=meta.get("keywords", ""),
                body_text=_extract_body_text(body),
                status="ok",
            )

        except httpx.TimeoutException:
            # One quick retry on the same scheme before giving up / falling back.
            try:
                with httpx.Client(
                    timeout=timeout,
                    follow_redirects=True,
                    max_redirects=_MAX_REDIRECTS,
                    headers=headers,
                ) as client:
                    resp = client.get(url)
                if (
                    resp.status_code < 400
                    and "text/html" in resp.headers.get("content-type", "").lower()
                ):
                    body = resp.text[:_MAX_RESPONSE_BYTES]
                    meta = _extract_meta(body)
                    return HomepageMeta(
                        title=meta.get("title", ""),
                        description=meta.get("description", ""),
                        og_description=meta.get("og_description", ""),
                        keywords=meta.get("keywords", ""),
                        body_text=_extract_body_text(body),
                        status="ok",
                    )
            except Exception:
                pass
            if scheme == "https":
                continue
            return HomepageMeta(status="timeout")
        except Exception:
            if scheme == "https":
                continue
            logger.debug("Homepage fetch failed for %s", domain, exc_info=True)
            return HomepageMeta(status="blocked")

    return HomepageMeta(status="timeout")
