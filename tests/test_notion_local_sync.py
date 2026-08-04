"""Reading policy out of Notion from the operator's own machine, with no API.

A company workspace where nobody can create an internal integration cannot use
``NOTION_TOKEN`` at all — so the official path is unavailable there, not misconfigured.
Notion's own Markdown export is the way through, and both local routes (a manually
downloaded zip, and one this app asks Notion to produce) end in the same file, so the
parser here is the only thing either of them depends on.

The contract from ``policy_sync`` still holds whichever reader ran: a page that cannot be
read keeps its previous copy, because an answer built on no policy is worse than one built
on yesterday's.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.agents.policy_sync import sync_policy_sources
from src.db.base import Base
from src.db.models import KnowledgeDocument, PolicySource
from src.integrations.notion_export import (
    NotionExportError,
    fetcher_from_export,
    read_export,
)

PAGE_ID = "3a2f11f6ee6380ab815afed3cbb42d77"
PAGE_URL = f"https://www.notion.so/estsoft/B2B-Pricing-{PAGE_ID}"
MD_NAME = f"B2B 가격 정책 {PAGE_ID}.md"
MARKDOWN = """# B2B 가격 정책

엔터프라이즈 제안 기본가는 분당 $2 입니다.

- 하한선은 분당 $1.7
- 500 크레딧 이상 구매 시 할인
"""


def _zip(members: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in members.items():
            archive.writestr(name, text)
    return buffer.getvalue()


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    from src.agents import policy_sync

    monkeypatch.setattr(policy_sync, "SessionLocal", factory)
    with factory() as session:
        session.add(
            PolicySource(
                label="B2B 가격 정책",
                notion_url=PAGE_URL,
                notion_page_id=PAGE_ID,
                mode="knowledge",
                body="어제 사본",
                title="어제 제목",
            )
        )
        session.commit()
    return factory


# ---- reading the export ------------------------------------------------------------


def test_a_page_is_matched_by_id_not_by_title():
    """An owner renaming the page in Notion must still update the row that was
    registered with that URL — the id in the filename is what ties them together."""
    pages = read_export(_zip({"완전히 다른 이름 " + PAGE_ID + ".md": MARKDOWN}))
    assert set(pages) == {PAGE_ID}
    assert pages[PAGE_ID].title == "B2B 가격 정책"


def test_the_title_h1_is_lifted_out_of_the_body():
    """It is stored in its own column; leaving it in made every document open by
    repeating its own name to the model."""
    page = read_export(_zip({MD_NAME: MARKDOWN}))[PAGE_ID]
    assert not page.markdown.startswith("#")
    assert page.markdown.startswith("엔터프라이즈")
    assert "$1.7" in page.markdown


def test_a_database_export_comes_back_as_a_table():
    """Notion exports an inline database as a separate CSV and leaves a link behind.
    Dropping it loses exactly the rows that carry the numbers."""
    csv_text = "플랜,분당가,최소 크레딧\nBusiness,$2,500\nEnterprise,$1.7,2500\n"
    pages = read_export(_zip({MD_NAME: MARKDOWN, f"요금표 {PAGE_ID}.csv": csv_text}))
    body = pages[PAGE_ID].markdown
    assert "| 플랜 | 분당가 | 최소 크레딧 |" in body
    assert "| Enterprise | $1.7 | 2500 |" in body


def test_a_view_duplicate_csv_is_not_appended_twice():
    """Notion ships "<name>_all.csv" beside the view export of the same database."""
    csv_text = "플랜,분당가\nBusiness,$2\n"
    pages = read_export(
        _zip({MD_NAME: MARKDOWN, "요금표.csv": csv_text, "요금표_all.csv": csv_text})
    )
    assert pages[PAGE_ID].markdown.count("| Business | $2 |") == 1


def test_a_workspace_export_keeps_only_what_is_asked_for():
    """A whole-workspace export holds hundreds of pages; the ones nobody registered are
    simply never looked up."""
    other = "b1b2c3d4e5f60718293a4b5c6d7e8f90"
    fetch = fetcher_from_export(
        _zip({MD_NAME: MARKDOWN, f"사내 위키 {other}.md": "# 위키\n무관한 문서"})
    )
    assert fetch(PAGE_URL).title == "B2B 가격 정책"


def test_a_page_missing_from_the_export_says_which_one():
    fetch = fetcher_from_export(_zip({MD_NAME: MARKDOWN}))
    with pytest.raises(NotionExportError) as exc:
        fetch("https://www.notion.so/estsoft/Other-b1b2c3d4e5f60718293a4b5c6d7e8f90")
    assert "b1b2c3d4" in str(exc.value)
    assert "Export" in str(exc.value)


def test_an_export_with_no_pages_is_refused():
    with pytest.raises(NotionExportError):
        read_export(_zip({"readme.txt": "nothing here"}))


def test_a_folder_export_reads_the_same_as_a_zip(tmp_path):
    """The operator may unzip it first; both are the same export."""
    (tmp_path / "Export").mkdir()
    (tmp_path / "Export" / MD_NAME).write_text(MARKDOWN, encoding="utf-8")
    assert read_export(tmp_path / "Export")[PAGE_ID].title == "B2B 가격 정책"


# ---- what reaches the database -----------------------------------------------------


def test_an_export_sync_updates_the_copy_and_the_knowledge_document(db):
    result = sync_policy_sources(fetcher=fetcher_from_export(_zip({MD_NAME: MARKDOWN})))
    assert result == {"synced": 1, "failed": 0, "skipped": 0}
    with db() as session:
        source = session.query(PolicySource).one()
        assert source.title == "B2B 가격 정책"
        assert "$1.7" in source.body
        assert source.last_synced_at is not None
        assert source.last_error is None
        # mode='knowledge' rows also reach the router's table — registering a page is all
        # it takes for the drafting prompt to quote it.
        doc = session.query(KnowledgeDocument).one()
        assert doc.slug == f"notion-{PAGE_ID[:12]}"
        assert "$1.7" in doc.body


def test_a_page_missing_from_the_export_keeps_yesterdays_copy(db):
    """The whole point of storing a copy: a bad read degrades to stale policy, never to
    no policy."""
    result = sync_policy_sources(fetcher=fetcher_from_export(_zip({"기타 페이지 " + "b" * 32 + ".md": "# 기타\n본문"})))
    assert result["failed"] == 1
    with db() as session:
        source = session.query(PolicySource).one()
        assert source.body == "어제 사본"
        assert "Export" in source.last_error


def test_the_local_route_needs_no_notion_token(db, monkeypatch):
    """The reason this exists: on a company workspace NOTION_TOKEN cannot be obtained,
    and the API path skips everything when it is blank."""
    from src.common.config import settings

    monkeypatch.setattr(settings, "NOTION_TOKEN", "")
    assert sync_policy_sources() == {"synced": 0, "failed": 0, "skipped": 0}
    assert sync_policy_sources(fetcher=fetcher_from_export(_zip({MD_NAME: MARKDOWN})))["synced"] == 1


# ---- the session cookie stays on the operator's machine ----------------------------


def test_no_server_code_imports_the_browser_session_module():
    """``token_v2`` is a personal login cookie, not a service credential. Only the local
    script may reach it — a route that imported this module would put a person's browser
    session on the server's critical path."""
    import pathlib
    import re

    # Import statements only — notion_export.py names the module in its docstring to say
    # where the other half lives, and that is documentation, not a dependency.
    imports = re.compile(r"^\s*(?:from|import)\b.*\bnotion_session\b", re.M)
    offenders = [
        str(path)
        for path in pathlib.Path("src").rglob("*.py")
        if path.name != "notion_session.py" and imports.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_the_session_module_is_read_only():
    """It enqueues one task type. Nothing here can edit a Notion page."""
    import pathlib

    source = pathlib.Path("src/integrations/notion_session.py").read_text(encoding="utf-8")
    assert '"exportBlock"' in source
    for write_task in ("submitTransaction", "saveTransactions", "setPageProperty"):
        assert write_task not in source
