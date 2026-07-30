"""Sidebar structure and the 활용 툴 pages.

The sidebar is the only map of this console, so its shape is worth pinning: three of
the four UI changes edited nav.html on adjacent lines, and a stale entry there points
an operator at a page that no longer exists.
"""

from __future__ import annotations

import pathlib
import re

from fastapi.testclient import TestClient

from src.api.main import app


def _nav_html() -> str:
    with TestClient(app) as client:
        return client.get("/messages").text


def test_sidebar_sections_are_the_ones_the_operator_asked_for():
    html = _nav_html()
    for title in ("인바운드 회신", "인사이트", "고객 관리", "활용 툴"):
        assert title in html, title
    # 파이프라인 연동관리 was a section with one entry; the board moved onto /.
    assert "파이프라인 연동관리" not in html


def test_every_renamed_entry_lost_its_old_wording():
    """The sidebar is the console's only map, so a half-applied rename leaves two names
    for one screen. Each pair is (what it used to say, what it says now)."""
    html = _nav_html()
    for old, new in (
        ("인바운드 답장", "인바운드 회신"),
        ("이메일 답변 설정", "답변 템플릿"),
        ("문의·국가 추이", "리드 추이"),
        ("업데이트 필요 고객", "고객 인사이트"),
        ("고객 히스토리", "고객 관리"),
        ("인바운드 고객 히스토리", "리드 히스토리"),
        ("아웃바운드 고객 히스토리", "수주 고객"),
    ):
        assert new in html, new
        assert old not in html, old
    assert "이메일 규칙" not in html


def test_overview_sits_above_the_first_section_and_renders():
    """전체 대시보드 is the whole-business view: first entry, outside every section.

    The page is a placeholder for now. /overview also has to be in
    security.WEB_UI_PREFIXES — without it the auth middleware reads the path as a JSON
    API route and answers 401 instead of rendering.
    """
    html = _nav_html()
    assert html.index('href="/overview"') < html.index("인바운드 회신")
    with TestClient(app) as client:
        response = client.get("/overview")
    assert response.status_code == 200
    assert "전체 대시보드" in response.text
    assert "준비 중" in response.text


def test_tool_entries_open_in_a_new_tab():
    """They are references consulted while writing a reply, not destinations."""
    html = _nav_html()
    for href in ("/tools/quote-calculator", "/tools/quotation", "/tools/contract"):
        anchor = re.search(rf'<a href="{re.escape(href)}"[^>]*>', html)
        assert anchor, href
        assert 'target="_blank"' in anchor.group(0), href
        # Without rel=noopener the opened page gets a handle on window.opener.
        assert 'rel="noopener"' in anchor.group(0), href


def test_customer_section_links_both_directions():
    html = _nav_html()
    assert "리드 히스토리" in html
    assert "수주 고객" in html


def test_placeholder_tools_render_a_coming_soon_page():
    with TestClient(app) as client:
        for path, title in (
            ("/tools/quotation", "견적서"),
            ("/tools/contract", "계약서"),
            ("/outbound-history", "수주 고객"),
            ("/overview", "전체 대시보드"),
        ):
            response = client.get(path)
            assert response.status_code == 200, path
            assert title in response.text
            assert "준비 중" in response.text


def test_old_pipeline_url_redirects_to_the_dashboard():
    """The board is on / now; bookmarks and the POST redirects must still land."""
    with TestClient(app) as client:
        response = client.get("/pipeline", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "/"


def test_quote_calculator_is_no_longer_embedded_in_the_reply_screen():
    """It loaded a second full page inside the reply screen; it lives in the sidebar.

    Asserted on the template source: the embed was markup plus a toggle function, and
    both had to go together — leaving the function behind would throw on click.
    """
    source = pathlib.Path("src/api/web/templates/message_detail.html").read_text(
        encoding="utf-8"
    )
    assert "calc-frame" not in source
    assert "calc-panel" not in source
    assert "toggleCalc" not in source
