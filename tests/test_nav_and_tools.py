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
    signatures and templates."""
    assert '"/policy-docs"' not in SHELL
    templates_screen = pathlib.Path("frontend/src/screens/EmailTemplates.tsx").read_text(
        encoding="utf-8"
    )
    assert 'kind === "policy"' in templates_screen


def test_a_document_arrives_with_its_text_and_can_be_edited_here():
    """A document arrives WITH its body, and there is exactly one way in.

    Two ways in were tried before and both are gone, for the same reason: they registered
    a page that nothing could ever fill.

    - A bare Notion URL. No API token exists on this workspace, so the row sat empty
      forever — and it really did, in production.
    - A Notion Export zip. The export carries the page you exported and its DESCENDANTS.
      This policy page references its documents as links to pages elsewhere in the
      workspace, so the real export downloaded on 2026-08-05 held exactly one .md — the
      parent — while the console reported a successful upload. See
      docs/정책문서-동기화-설계.md §2⑤.

    Paste carries the text, so what the screen shows is what the draft reads.
    """
    policy = pathlib.Path("frontend/src/screens/PolicyDocs.tsx").read_text(encoding="utf-8")
    assert "직접 추가" in policy and "<textarea" in policy
    assert "dropzone" not in policy and "upload-export" not in policy
    assert "/toggle" in policy and "/delete" in policy
    assert not pathlib.Path("src/integrations/notion_export.py").exists()


TEMPLATES = pathlib.Path("frontend/src/screens/EmailTemplates.tsx").read_text(encoding="utf-8")


def test_opening_something_pushes_history_so_the_browser_back_button_returns_to_the_list():
    """Chrome's back button, not the in-app chip.

    Drilling in used to call ``setParams(…, {replace: true})``, which OVERWRITES the
    list's history entry. Back from a document then skipped the list and landed wherever
    the operator had been before the screen — reported as "뒤로가기를 누르면 아예 다른
    곳으로 갑니다".

    Replace is still right for a FILTER (twenty chip clicks should not be twenty entries
    to back out of) and for the back buttons themselves. It is wrong for going INTO
    something, which is the one case that has to be undoable.
    """
    for entering in (
        "setParams({ kind: entry.key })",
        'setParams({ kind, edit: "new" })',
        "setParams({ kind, edit: String(item.id) })",
    ):
        assert entering in TEMPLATES, entering
    policy = pathlib.Path("frontend/src/screens/PolicyDocs.tsx").read_text(encoding="utf-8")
    assert 'setParams({ kind: "policy", doc: String(row.id) })' in policy


def test_the_way_back_out_of_the_editor_is_a_left_chip_like_every_other_screen():
    """It was a button on the RIGHT of the header, where every other screen puts an
    action — so the one control that leaves the page looked like one that changes it."""
    assert 'className="btn btn--subtle" onClick={onDone}>목록으로' not in TEMPLATES
    assert 'className="chip" onClick={onDone}' in TEMPLATES


def test_a_row_that_holds_only_one_value_is_edited_as_one_field():
    """미팅 예약 링크 / WhatsApp 링크 / 담당자 이름 hold one value each. The full editor
    asked for a language, offered an HTML preview and gave a 240px textarea — three
    questions that have no answer for any of them. 언어 stays only where it means
    something: a signature is the one kind that exists once per language."""
    assert "ONE_LINE_FIELDS" in TEMPLATES
    for key in ("meeting_link:", "whatsapp_link:", "sender_name:"):
        assert key in TEMPLATES, key
    assert 'id="et-link"' in TEMPLATES and "type={oneLine.type}" in TEMPLATES
    assert "{isSignature && (" in TEMPLATES

    # The screen can only tell them apart if the API sends the key.
    ui_api = pathlib.Path("src/api/routes/ui_api.py").read_text(encoding="utf-8")
    assert '"key": row.key' in ui_api


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


def test_health_fails_when_the_console_cannot_be_served():
    """The release that broke production reported /healthz ok while every screen — and
    sign-in, which serves the same document — answered 503. The platform saw a healthy
    deploy and cut traffic over to a console nobody could open.

    A console that cannot be opened is not a healthy release. Saying so is what makes the
    platform keep the previous one serving instead.
    """
    import shutil

    from src.api.main import _SPA_INDEX

    bundle = _SPA_INDEX.parent
    assert bundle.exists(), "run: npm --prefix frontend ci && npm --prefix frontend run build"

    with TestClient(app) as client:
        healthy = client.get("/healthz")
    assert healthy.status_code == 200
    assert healthy.json()["console"] is True

    moved = bundle.with_suffix(".missing")
    shutil.move(str(bundle), str(moved))
    try:
        with TestClient(app) as client:
            broken = client.get("/healthz")
    finally:
        shutil.move(str(moved), str(bundle))
    assert broken.status_code == 503
    assert broken.json() == {"ok": False, "database": True, "console": False}
