"""Tests for LinkedIn profile email extraction with mocked Playwright."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.integrations.linkedin_profile import (
    _extract_email_from_profile,
    _is_challenge_page,
    fetch_profile_email,
)


def _make_mock_page(
    url: str = "https://www.linkedin.com/in/testuser/",
    title: str = "Test User | LinkedIn",
    has_contact_link: bool = True,
    email_text: str = "test@company.com",
    has_modal: bool = True,
    has_email_section: bool = True,
) -> MagicMock:
    """Build a mock Playwright page."""
    page = MagicMock()
    page.url = url
    page.title.return_value = title

    contact_link = MagicMock() if has_contact_link else None
    page.query_selector.side_effect = lambda sel: (
        contact_link if "contactInfo" in sel else None
    )

    if has_modal:
        email_section = MagicMock()
        email_section.inner_text.return_value = f"Email\n{email_text}"

        modal = MagicMock()
        if has_email_section:
            modal.query_selector.return_value = email_section
        else:
            modal.query_selector.return_value = None
            modal.inner_text.return_value = f"Name\n{email_text}\nPhone"

        def page_qs(sel):
            if "contactInfo" in sel:
                return contact_link
            if "artdeco-modal" in sel or "pv-contact-info" in sel:
                return modal
            return None

        page.query_selector.side_effect = page_qs

    return page


def _make_mock_context(page: MagicMock) -> MagicMock:
    ctx = MagicMock()
    ctx.new_page.return_value = page
    return ctx


def test_extract_email_from_profile_success() -> None:
    page = _make_mock_page(email_text="kim@startup.kr")
    ctx = _make_mock_context(page)

    email = _extract_email_from_profile(ctx, "https://www.linkedin.com/in/testuser/")

    assert email == "kim@startup.kr"
    page.goto.assert_called_once()
    page.close.assert_called_once()


def test_extract_email_no_contact_link() -> None:
    page = _make_mock_page(has_contact_link=False)
    page.query_selector.return_value = None
    ctx = _make_mock_context(page)

    email = _extract_email_from_profile(ctx, "https://www.linkedin.com/in/testuser/")

    assert email is None


def test_extract_email_from_modal_fallback() -> None:
    page = _make_mock_page(email_text="fallback@corp.io", has_email_section=False)
    ctx = _make_mock_context(page)

    email = _extract_email_from_profile(ctx, "https://www.linkedin.com/in/testuser/")

    assert email == "fallback@corp.io"


def test_challenge_page_detected() -> None:
    page = MagicMock()

    page.url = "https://www.linkedin.com/checkpoint/challenge/"
    page.title.return_value = "LinkedIn"
    assert _is_challenge_page(page) is True

    page.url = "https://www.linkedin.com/in/normal/"
    page.title.return_value = "Security Verification"
    assert _is_challenge_page(page) is True

    page.url = "https://www.linkedin.com/in/normal/"
    page.title.return_value = "Normal User | LinkedIn"
    assert _is_challenge_page(page) is False


def test_challenge_page_skips_extraction() -> None:
    page = _make_mock_page()
    page.url = "https://www.linkedin.com/authwall?..."
    ctx = _make_mock_context(page)

    email = _extract_email_from_profile(ctx, "https://www.linkedin.com/in/testuser/")

    assert email is None


def test_fetch_profile_email_with_context() -> None:
    page = _make_mock_page(email_text="direct@test.com")
    ctx = _make_mock_context(page)

    email = fetch_profile_email(
        "https://www.linkedin.com/in/testuser/",
        "fake-cookie",
        context=ctx,
    )

    assert email == "direct@test.com"


def test_no_modal_returns_none() -> None:
    page = MagicMock()
    page.url = "https://www.linkedin.com/in/testuser/"
    page.title.return_value = "Test | LinkedIn"

    contact_link = MagicMock()

    def qs(sel):
        if "contactInfo" in sel:
            return contact_link
        return None

    page.query_selector.side_effect = qs
    ctx = _make_mock_context(page)

    email = _extract_email_from_profile(ctx, "https://www.linkedin.com/in/testuser/")

    assert email is None


def test_page_exception_returns_none() -> None:
    page = MagicMock()
    page.goto.side_effect = Exception("Timeout")
    ctx = _make_mock_context(page)

    email = _extract_email_from_profile(ctx, "https://www.linkedin.com/in/testuser/")

    assert email is None
    page.close.assert_called_once()
