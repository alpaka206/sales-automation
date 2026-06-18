"""Tests for the plain-text → HTML email renderer."""

from __future__ import annotations

from src.integrations.email_html import to_html_email


def test_plaintext_becomes_html_document_with_paragraphs():
    html = to_html_email("첫 문단입니다.\n\n둘째 문단입니다.")
    assert "<html" in html and "</html>" in html
    assert html.count("<p ") == 2  # one per blank-line-separated paragraph


def test_single_newlines_become_br():
    html = to_html_email("줄1\n줄2")
    assert "<br>" in html


def test_urls_are_linkified():
    html = to_html_email("자세히: https://perso.ai/pricing 참고")
    assert '<a href="https://perso.ai/pricing"' in html


def test_html_special_chars_escaped_in_plaintext():
    html = to_html_email("a < b & c > d")
    assert "&lt; b &amp; c &gt;" in html


def test_existing_html_passed_through():
    html = to_html_email("<p>이미 <strong>HTML</strong> 본문</p>")
    assert "<strong>HTML</strong>" in html
    # not double-escaped
    assert "&lt;strong&gt;" not in html


def test_empty_body_is_valid_document():
    html = to_html_email("")
    assert "<html" in html and "<body" in html


def test_dash_lines_become_indented_list():
    html = to_html_email("플랜이 잘 맞습니다:\n- 월 200분 지원\n- 음성 복제 지원")
    assert "<ul" in html and "<li" in html
    assert "월 200분 지원" in html
    assert "음성 복제 지원" in html
    # The lead line is still a paragraph; the bullets are a single list.
    assert html.count("<p ") == 1
    assert html.count("<ul") == 1


def test_mixed_paragraph_and_bullets():
    html = to_html_email("소개합니다.\n- 항목 A\n그리고 마무리합니다.")
    # text → bullets → text yields two <p> blocks and one <ul>.
    assert html.count("<ul") == 1
    assert html.count("<p ") == 2
