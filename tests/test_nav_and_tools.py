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
    assert "새로 만들기" in policy and "<textarea" in policy
    assert "dropzone" not in policy and "upload-export" not in policy

    # 만드는 폼과 고치는 폼은 같은 화면입니다. 따로 두면 같은 것을 두 모양으로 묻게 되고,
    # 칸을 하나 더할 때 고칠 곳이 둘이 됩니다 — 실제로 메일 제목 칸이 그랬습니다.
    # (본문 칸이 하나라는 것으로 셉니다. 폼 안의 textarea 는 그 뒤로 늘었습니다.)
    assert policy.count('id="pd-body"') == 1
    # 라우터가 이 문서를 고를 때 읽는 한 줄. 본문 맨 위에 적어 두면 노션에서 다시 붙여넣을
    # 때마다 날아갑니다.
    assert 'id="pd-usage"' in policy
    assert 'doc: "new"' in policy
    assert not pathlib.Path("src/integrations/notion_export.py").exists()

    # 중지 is gone with the upstream it belonged to. It meant "keep the registration and
    # the synced copy but stop using it" — a state that only made sense while a Notion
    # page was the original. Nothing is kept for anything now: a document you will not use
    # gets deleted. Leaving the button would leave a row that looks live and is not.
    assert "/delete" in policy
    assert "/toggle" not in policy
    assert not pathlib.Path("src/api/routes/policy_docs.py").read_text(
        encoding="utf-8"
    ).count("policy-docs/{source_id}/toggle")


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
    policy = pathlib.Path("frontend/src/screens/PolicyDocs.tsx").read_text(encoding="utf-8")
    # Every call that OPENS something, in both screens: none of them may replace.
    opens = [
        line.strip()
        for source in (TEMPLATES, policy)
        for line in source.split("\n")
        if ("onRowClick" in line or "edit: " in line or "kind: entry.key" in line)
        and "setParams(" in line
    ]
    assert len(opens) >= 4, opens
    for line in opens:
        # 언어를 바꿔 보는 것만 예외입니다: 같은 화면을 다르게 보는 것이지 들어간 것이
        # 아니라, 뒤로가기가 언어를 되짚는 대신 목록으로 나가야 합니다.
        if "onOpen" in line:
            continue
        assert "replace: true" not in line, line


def test_the_way_back_out_of_the_editor_is_a_left_chip_like_every_other_screen():
    """It was a button on the RIGHT of the header, where every other screen puts an
    action — so the one control that leaves the page looked like one that changes it."""
    assert 'className="btn btn--subtle" onClick={onDone}>목록으로' not in TEMPLATES
    assert 'className="chip" onClick={onDone}' in TEMPLATES


def test_a_row_that_holds_only_one_value_is_edited_as_one_field():
    """미팅 예약 링크 / WhatsApp 링크 / 담당자 이름 hold one value each. The full editor
    asked for a language, offered an HTML preview and gave a 240px textarea — three
    questions that have no answer for any of them.

    언어 is gone from the editor entirely now (0061): the only kind that had one was the
    signature, and nothing matches a signature to a language — the operator picks one on
    the draft."""
    assert "ONE_LINE_FIELDS" in TEMPLATES
    # 국문·영문 행 여섯 개 전부. `_en` 행이 빠져 있으면 그 줄만 240px 텍스트영역으로 열립니다.
    for key in ("meeting_link:", "meeting_link_en:", "whatsapp_link:", "whatsapp_link_en:",
                "sender_name:", "sender_name_en:"):
        assert key in TEMPLATES, key
    assert 'id="et-link"' in TEMPLATES
    # type="url" 이 아닙니다: 0069 이후 이 값은 주소가 아니라 `[글자](주소)` 이고,
    # 브라우저가 그것을 잘못된 값으로 표시했습니다.
    assert "type={oneLine.type}" not in TEMPLATES
    assert 'id="et-language"' not in TEMPLATES

    # The screen can only tell them apart if the API sends the key.
    ui_api = pathlib.Path("src/api/routes/ui_api.py").read_text(encoding="utf-8")
    assert '"key": row.key' in ui_api


def test_sidebar_sections_are_the_ones_the_operator_asked_for():
    # 「활용 툴」(견적 계산기·견적서·계약서)은 통째로 지웠습니다 — 앞으로 안 씁니다.
    for title in ("인바운드 리드", "고객 관리", "인사이트", "시스템"):
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
        ("업데이트 필요 고객", "고객 인사이트"),
        ("인바운드 고객 히스토리", "리드 히스토리"),
        ("아웃바운드 고객 히스토리", "수주 고객"),
    ):
        assert new in SHELL, new
        assert old not in SHELL, old
    # 완전히 없어진 것들 — 다시 들어오면 이 테스트가 잡습니다(운영자 지시로 지웠습니다).
    for retired in (
        "인바운드 답장", "이메일 답변 설정", "고객 히스토리", "이메일 규칙",
        "리드 추이", "전체 대시보드", "활용 툴", "견적 계산기", "견적서", "계약서",
    ):
        assert retired not in SHELL, retired


def test_the_sidebar_is_only_sections_now():
    """섹션 밖에 「전체 대시보드」 링크가 하나 더 있었습니다. 각 화면의 숫자를 모아 보여
    주기만 하는 자리라 안 보게 되어 지웠고(운영자 지시), 이제 사이드바는 섹션뿐입니다."""
    assert 'to="/overview"' not in SHELL
    assert "{SECTIONS.map(" in SHELL


def test_customer_section_lists_negotiating_first():
    """협상중 고객 → 수주 고객 → 리드 히스토리 — 손이 가는 순서입니다.

    리드 히스토리가 맨 아래인 이유: 지나간 리드 전부를 담은 목록이라 매일 여는 화면이
    아닙니다. 위 둘은 오늘 일이 있는 고객입니다.
    """
    assert SHELL.index("협상중 고객") < SHELL.index("수주 고객") < SHELL.index("리드 히스토리")
    assert 'to: "/customers?stage=negotiation"' in SHELL


def test_only_one_customer_entry_can_be_active():
    """협상중 고객 and 리드 히스토리 are the same path; only the query separates them, so
    the active test cannot be the router's path comparison alone."""
    assert "location.search" in SHELL


def test_every_screen_has_a_route():
    for path in (
        "messages", "messages/:id", "customers", "customers/:id", "email-templates",
        "operations", "companies/:domain", "settings/users", "logs",
        "outbound-history", "tickets/:conversationId",
    ):
        assert f'path="{path}"' in ROUTES, path


def test_the_retired_screens_have_no_route_left():
    """전체 대시보드·활용 툴 셋은 화면·라우트·리다이렉트까지 지웠습니다(운영자 지시).

    라우트만 남기면 옛 북마크가 빈 화면을 열고, 그게 "고장났다" 로 보입니다 — 없는 주소는
    없다고 하는 편이 낫습니다.
    """
    for path in ("overview", "tools/quote-calculator", "tools/quotation", "tools/contract"):
        assert f'path="{path}"' not in ROUTES, path


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
        "/outbound-history": "/app/outbound-history",
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


def test_opening_a_template_costs_no_request_at_all():
    """미팅 예약 링크 / 담당자 이름 opened as a 템플릿 이름 + 본문 editor and became a
    single field a moment later.

    Which form a row gets is decided by its KEY, and the key arrived with a SECOND request
    — so for as long as that took, the screen drew the wrong form. The wait was the whole
    problem, and it was never the database: /healthz touches Postgres and takes the same
    as a static file, ~200-370 ms from Seoul, which is the distance to the service. So the
    fix is not a faster query, it is not asking twice. A handful of rows whose largest body
    is 1.1 KB ride along with the list, and the editor renders from what is already there.

    The skeleton stays for the one case that has no list yet: opening the URL directly.
    """
    assert '"body": row.body or ""' in pathlib.Path("src/api/routes/ui_api.py").read_text(
        encoding="utf-8"
    )
    # No per-template fetch left in the screen — the list is the only read.
    assert "api/ui/email-templates/${" not in TEMPLATES
    assert TEMPLATES.count("useQuery({") == 1
    assert 'if (id !== "new" && !data) {' in TEMPLATES and "LoadingBlock" in TEMPLATES
    # And the shape is still derived from the key — the skeleton is the wait, not a guess.
    assert "ONE_LINE_FIELDS[data.key]" in TEMPLATES


def test_a_one_line_field_is_not_trimmed_while_it_is_being_typed():
    """담당자 이름 (영문) 에 스페이스를 칠 수 없었습니다.

    onChange 에서 .trim() 을 걸어 두면 "Untae Bae" 를 치는 도중 "Untae " 가 "Untae" 로
    잘려, 다음 글자가 붙어 버립니다. 값에 공백이 없는 URL 두 개에서는 티가 안 났고 이름에서
    드러났습니다. 앞뒤 공백을 떼는 것은 맞지만, 다 치고 난 **저장할 때** 할 일입니다.
    """
    assert "onChange={(e) => setBody(e.target.value.trim())}" not in TEMPLATES
    assert "const value = oneLine ? body.trim() : body;" in TEMPLATES
    assert "body: value" in TEMPLATES


def test_the_sidebar_stays_lit_on_a_detail_page():
    """상세로 들어가도 왼쪽 nav 는 그 화면을 가리켜야 합니다.

    정확 일치만 보면 `/won-customers/2102` 에서 사이드바가 아무 데도 강조하지 않아,
    내가 어느 화면에 있는지가 사라집니다. 그렇다고 접두사로만 보면 `/` 가 모든 경로의
    앞부분이라 문의 대시보드가 항상 켜집니다.

    규칙은 "정확히 같거나, `path + '/'` 로 시작" 하나입니다. 루트는 `"//"` 가 되어 어디에도
    안 걸리고, 형제 경로도 안전합니다 — `/won-customers` 는 `/customers/` 로 시작하지
    않습니다. 그 두 성질이 이 한 줄에 같이 걸려 있어서 여기서 고정합니다.
    """
    source = SHELL

    assert 'location.pathname.startsWith(path + "/")' in source
    assert "location.pathname === path ||" in source

    def on_path(path: str, pathname: str) -> bool:
        return pathname == path or pathname.startswith(path + "/")

    assert on_path("/won-customers", "/won-customers")
    assert on_path("/won-customers", "/won-customers/2102")
    assert on_path("/won-customers", "/won-customers/2102/contracts/new")
    assert on_path("/customers", "/customers/17")
    # 루트는 정확 일치일 때만.
    assert on_path("/", "/")
    assert not on_path("/", "/won-customers")
    # 형제 경로가 서로를 켜면 안 됩니다.
    assert not on_path("/customers", "/won-customers")
    assert not on_path("/won-customers", "/customers")
