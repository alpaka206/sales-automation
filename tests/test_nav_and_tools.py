"""The console's map, after the cutover.

The sidebar used to be a Jinja partial that pytest could fetch and read. It is
``frontend/src/Shell.tsx`` now, so these assert on that source — the same invariants
(which sections exist, in what order, which wording was retired) checked one layer up.

The other half is that every URL the old console handed out still works. Bookmarks, the
links in `/logs` entries and the ones in HubSpot all point at the Jinja paths, so each
one redirects to the screen that replaced it rather than 404ing.
"""

from __future__ import annotations

import pathlib
import re

from fastapi.testclient import TestClient

from src.api.main import app

SHELL = pathlib.Path("frontend/src/app/Shell.tsx").read_text(encoding="utf-8")
ROUTES = pathlib.Path("frontend/src/main.tsx").read_text(encoding="utf-8")


def test_policy_docs_is_a_category_not_a_sidebar_entry():
    """Reading policy is part of "what does the reply actually say?", so it sits with the
    signatures and templates. It stays read-only: the copy here is pulled from Notion and
    editing it would be undone by the next sync."""
    assert '"/policy-docs"' not in SHELL
    templates_screen = pathlib.Path("frontend/src/screens/EmailTemplates.tsx").read_text(
        encoding="utf-8"
    )
    assert 'kind === "policy"' in templates_screen
    policy = pathlib.Path("frontend/src/screens/PolicyDocs.tsx").read_text(encoding="utf-8")
    assert "읽기 전용" in policy
    assert "<textarea" not in policy and "<input" not in policy


def test_sidebar_sections_are_the_ones_the_operator_asked_for():
    for title in ("인바운드 리드", "고객 관리", "인사이트", "활용 툴", "시스템"):
        assert f'title: "{title}"' in SHELL, title
    # 파이프라인 연동관리 was a section with one entry; the board moved onto the dashboard.
    assert "파이프라인 연동관리" not in SHELL


def test_sections_are_in_the_order_the_operator_asked_for():
    """고객 관리 above 인사이트: a daily working screen outranks a periodic read."""
    order = [SHELL.index(f'title: "{title}"') for title in ("인바운드 리드", "고객 관리", "인사이트")]
    assert order == sorted(order)


def test_every_renamed_entry_lost_its_old_wording():
    """The sidebar is the console's only map, so a half-applied rename leaves two names
    for one screen. Each pair is (what it used to say, what it says now)."""
    for old, new in (
        ("인바운드 회신", "인바운드 리드"),
        ("답변 검토", "회신 및 검토"),
        ("답변 템플릿", "이메일 템플릿"),
        ("문의·국가 추이", "리드 추이"),
        ("업데이트 필요 고객", "고객 인사이트"),
        ("인바운드 고객 히스토리", "리드 히스토리"),
        ("아웃바운드 고객 히스토리", "수주 고객"),
    ):
        assert new in SHELL, new
        assert old not in SHELL, old
    for retired in ("인바운드 답장", "이메일 답변 설정", "고객 히스토리", "이메일 규칙"):
        assert retired not in SHELL, retired


def test_overview_sits_above_the_first_section():
    """전체 대시보드 is the whole-business view: first entry, outside every section.

    Compared in the JSX, not against the SECTIONS constant — that constant is declared at
    the top of the file and rendered below this link.
    """
    assert SHELL.index('to="/overview"') < SHELL.index("{SECTIONS.map(")


def test_customer_section_lists_negotiating_first():
    """협상중 고객 → 리드 히스토리 → 수주 고객, narrowest slice first."""
    assert SHELL.index("협상중 고객") < SHELL.index("리드 히스토리") < SHELL.index("수주 고객")
    assert 'to: "/customers?stage=negotiation"' in SHELL


def test_only_one_customer_entry_can_be_active():
    """협상중 고객 and 리드 히스토리 are the same path; only the query separates them, so
    the active test cannot be the router's path comparison alone."""
    assert "location.search" in SHELL


def test_every_screen_has_a_route():
    for path in (
        "messages", "messages/:id", "customers", "customers/:id", "email-templates",
        "operations", "companies/:domain", "settings/users", "logs",
        "tools/quote-calculator", "overview", "outbound-history",
    ):
        assert f'path="{path}"' in ROUTES, path


# ---- the old URLs still work -------------------------------------------------------


def test_the_old_page_urls_redirect_to_their_replacement():
    """Bookmarks, `/logs` entries and HubSpot links all carry the Jinja paths."""
    moved = {
        "/": "/app",
        "/messages": "/app/messages",
        "/messages/12": "/app/messages/12",
        "/customers": "/app/customers",
        "/customers/3": "/app/customers/3",
        "/companies/acme.com": "/app/companies/acme.com",
        "/email-templates": "/app/email-templates",
        # 정책 문서 is a category of the templates screen now, not its own entry.
        "/policy-docs": "/app/email-templates?kind=policy",
        "/operations": "/app/operations",
        "/logs": "/app/logs",
        "/settings/users": "/app/settings/users",
        "/overview": "/app/overview",
        "/outbound-history": "/app/outbound-history",
        "/tools/quote-calculator": "/app/tools/quote-calculator",
    }
    with TestClient(app) as client:
        for old, new in moved.items():
            response = client.get(old, follow_redirects=False)
            assert response.status_code == 302, old
            assert response.headers["location"] == new, old


def test_the_old_pipeline_url_still_lands():
    with TestClient(app) as client:
        response = client.get("/pipeline", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "/"


def test_the_console_bundle_exists_to_be_served():
    """Read this first when /app tests fail with 503.

    Everything below asserts the console is served, and the bundle is gitignored — it is
    produced by `npm --prefix frontend ci && npm --prefix frontend run build`, which CI
    does before pytest and a developer already has in their working tree. Without it
    seven tests fail as an unexplained Service Unavailable.
    """
    assert pathlib.Path("src/api/static/app/index.html").exists(), (
        "React 콘솔이 빌드되지 않았습니다: npm --prefix frontend ci "
        "&& npm --prefix frontend run build"
    )


def test_the_spa_serves_every_screen_route():
    with TestClient(app) as client:
        for path in ("/app", "/app/messages", "/app/customers/1", "/app/settings/users"):
            assert client.get(path).status_code == 200, path


def test_the_calculator_document_url_lands_on_the_screen_that_replaced_it():
    """It used to be an HTML document in an iframe; it is a React screen now."""
    with TestClient(app) as client:
        response = client.get("/tools/quote-calculator/app", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/app/tools/quote-calculator"


def test_the_quote_calculator_keeps_its_pricing_out_of_the_public_mount():
    """The tier policy carries internal margin data, so the screen fetches it from
    /api/ui behind the auth gate — never from /static, which is served to anyone."""
    from src.common.quote_tiers import policy_client

    assert not pathlib.Path("src/api/static/quote_calculator_app.html").exists()
    prices = {str(t["krw"]) for t in policy_client()["tiers"]}
    published = " ".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in pathlib.Path("src/api/static").rglob("*")
        if path.is_file() and path.suffix in {".js", ".html", ".css"}
    )
    assert not (prices & set(published.split())), "tier prices reached the public mount"


def test_no_html_template_is_left_to_render():
    """The cutover's actual deliverable: not one screen comes from a template.

    Sign-in was the last holdout — it renders before there is a session, so it serves
    the SPA document itself rather than a Jinja page.
    """
    assert not pathlib.Path("src/api/templates").exists()


def test_no_route_renders_a_screen_template_any_more():
    routes = pathlib.Path("src/api/routes")
    offenders = [
        str(path)
        for path in routes.glob("*.py")
        if re.search(r"TemplateResponse\(", path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
