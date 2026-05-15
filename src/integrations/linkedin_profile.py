"""LinkedIn profile email extraction via AI browser harness."""

from __future__ import annotations

import logging
import re

from .ai_browser import create_browser_context

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
MAX_EMAIL_LOOKUPS_PER_RUN = 20


def fetch_profile_email(
    profile_url: str,
    session_cookie: str,
    *,
    context=None,
) -> str | None:
    """Fetch a LinkedIn profile's contact email. Returns None on failure."""
    if context is not None:
        return _extract_email_from_profile(context, profile_url)

    try:
        with create_browser_context(cookies=[{
            "name": "li_at",
            "value": session_cookie,
            "domain": ".linkedin.com",
            "path": "/",
        }]) as ctx:
            return _extract_email_from_profile(ctx, profile_url)
    except RuntimeError:
        logger.warning("playwright not installed, skipping email lookup.")
        return None


def _extract_email_from_profile(context, profile_url: str) -> str | None:
    """Load a profile page, click Contact info, and extract email."""
    page = context.new_page()
    try:
        page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        if _is_challenge_page(page):
            logger.warning("LinkedIn challenge/captcha detected for %s, skipping.", profile_url)
            return None

        contact_link = page.query_selector(
            "a[href*='contactInfo'], "
            "[id='top-card-text-details-contact-info'], "
            "a.link-without-visited-state[href*='overlay/contact-info']"
        )
        if not contact_link:
            logger.debug("No contact info link found for %s.", profile_url)
            return None

        contact_link.click()
        page.wait_for_timeout(1500)

        modal = page.query_selector(
            ".pv-contact-info, "
            ".artdeco-modal, "
            "[aria-label*='contact'], "
            "[aria-label*='Contact']"
        )
        if not modal:
            logger.debug("Contact info modal did not open for %s.", profile_url)
            return None

        email_section = modal.query_selector(
            "section.ci-email, "
            ".pv-contact-info__contact-type--email, "
            "[class*='email']"
        )
        if email_section:
            text = email_section.inner_text()
            match = _EMAIL_RE.search(text)
            if match:
                return match.group(0).lower()

        modal_text = modal.inner_text()
        match = _EMAIL_RE.search(modal_text)
        if match:
            return match.group(0).lower()

        return None
    except Exception:
        logger.debug("Email extraction failed for %s.", profile_url, exc_info=True)
        return None
    finally:
        page.close()


def _is_challenge_page(page) -> bool:
    """Detect LinkedIn captcha/challenge pages."""
    url = page.url
    if "checkpoint" in url or "challenge" in url or "authwall" in url:
        return True
    title = page.title().lower()
    if "security" in title or "verification" in title:
        return True
    return False
