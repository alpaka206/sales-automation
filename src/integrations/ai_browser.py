"""AI browser harness — Playwright + Claude CLI for structured page extraction."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from contextlib import contextmanager
from typing import Any, Generator

from pydantic import BaseModel

from ..llm.providers.claude_cli import call_claude_cli

logger = logging.getLogger(__name__)

_MAX_CONCURRENT = 3
_semaphore: asyncio.Semaphore | None = None

_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)


def _strip_fences(text: str) -> str:
    m = _FENCE_RE.match(text)
    return m.group(1).strip() if m else text.strip()


def _clean_html(raw_html: str, max_chars: int = 30000) -> str:
    """Remove scripts, styles, ads, and noise. Keep headings, footer, contact areas."""
    html = re.sub(
        r"<(script|style|noscript|svg|iframe)[^>]*>.*?</\1>",
        "",
        raw_html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    html = re.sub(
        r'<(div|section|aside)[^>]*(ad-|ads-|advert|banner|cookie-consent|popup)[^>]*>.*?</\1>',
        "",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    html = re.sub(r"\s+", " ", html)
    return html[:max_chars]


@contextmanager
def create_browser_context(
    cookies: list[dict] | None = None,
) -> Generator[Any, None, None]:
    """Context manager providing a Playwright browser context for interactive scraping."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "playwright is not installed. Run `pip install playwright && playwright install chromium`."
        ) from e

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        if cookies:
            context.add_cookies(cookies)
        try:
            yield context
        finally:
            browser.close()


def fetch_and_extract_sync(
    url: str,
    extraction_prompt: str,
    schema: type[BaseModel] | None = None,
    cookies: list[dict] | None = None,
    max_html_chars: int = 30000,
) -> Any:
    """Synchronous version: fetch URL with Playwright, extract with Claude CLI."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("playwright not installed, falling back to httpx.")
        return _fallback_httpx(url, extraction_prompt, schema, max_html_chars)

    html = ""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        if cookies:
            context.add_cookies(cookies)

        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            html = page.content()
        except Exception:
            logger.warning("Playwright fetch failed for %s.", url, exc_info=True)
        finally:
            page.close()
            browser.close()

    if not html:
        return _fallback_httpx(url, extraction_prompt, schema, max_html_chars)

    return _extract_with_llm(html, url, extraction_prompt, schema, max_html_chars)


async def fetch_and_extract(
    url: str,
    extraction_prompt: str,
    schema: type[BaseModel] | None = None,
    cookies: list[dict] | None = None,
    max_html_chars: int = 30000,
) -> Any:
    """Async version with concurrency limiting."""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

    async with _semaphore:
        await asyncio.sleep(random.uniform(0.5, 2.0))
        return await asyncio.to_thread(
            fetch_and_extract_sync,
            url,
            extraction_prompt,
            schema,
            cookies,
            max_html_chars,
        )


async def fetch_and_extract_batch(
    tasks: list[dict],
    extraction_prompt: str,
    schema: type[BaseModel] | None = None,
) -> list[Any]:
    """Process multiple URLs concurrently (max 3 at a time)."""
    coros = [
        fetch_and_extract(
            task["url"],
            extraction_prompt,
            schema,
            task.get("cookies"),
            task.get("max_html_chars", 30000),
        )
        for task in tasks
    ]
    return await asyncio.gather(*coros, return_exceptions=True)


def _fallback_httpx(
    url: str,
    extraction_prompt: str,
    schema: type[BaseModel] | None,
    max_html_chars: int,
) -> Any:
    """Fallback to httpx when Playwright is not available."""
    import httpx

    try:
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            r = client.get(
                url, headers={"User-Agent": "Mozilla/5.0 (compatible; SalesBot/1.0)"}
            )
            r.raise_for_status()
            html = r.text
    except Exception:
        logger.warning("httpx fallback also failed for %s.", url)
        return None

    return _extract_with_llm(html, url, extraction_prompt, schema, max_html_chars)


def _extract_with_llm(
    html: str,
    url: str,
    extraction_prompt: str,
    schema: type[BaseModel] | None,
    max_html_chars: int,
) -> Any:
    """Send cleaned HTML to Claude CLI with the extraction prompt."""
    cleaned = _clean_html(html, max_html_chars)

    prompt = (
        f"URL: {url}\n\n"
        f"Below is the cleaned HTML content of the page:\n\n{cleaned}\n\n"
        f"---\n\n{extraction_prompt}"
    )

    if schema is not None:
        fields = schema.model_json_schema().get("properties", {})
        prompt += (
            "\n\nReturn ONLY valid JSON matching this schema: "
            + json.dumps(fields, ensure_ascii=False)
            + "\nNo markdown fences, no prose."
        )

    try:
        result = call_claude_cli(prompt, timeout=120)
        text = result.text
    except Exception:
        logger.warning("Claude CLI extraction failed for %s.", url, exc_info=True)
        return None

    if schema is not None:
        try:
            return schema.model_validate_json(_strip_fences(text))
        except Exception:
            logger.warning("Failed to parse LLM response as %s.", schema.__name__)
            return None

    return text
