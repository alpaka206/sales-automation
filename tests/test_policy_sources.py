"""Policy lives in Notion; this database holds the copy that answers are written from.

The rule these tests exist to protect: a Notion problem must never strip policy out of a
reply. Losing policy silently is worse than not syncing — it puts invented numbers in
front of a customer — so every failure path keeps the last good copy.
"""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest
import respx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.base import Base
from src.db.models import KnowledgeDocument, PolicySource
from src.integrations import notion


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    from src.agents import policy_sync

    monkeypatch.setattr(policy_sync, "SessionLocal", factory)
    # prompts._rules_from_db imports SessionLocal at call time, so patching the module
    # attribute is what redirects it.
    monkeypatch.setattr("src.db.session.SessionLocal", factory)
    return factory


@pytest.fixture()
def token(monkeypatch):
    from src.common.config import settings

    monkeypatch.setattr(settings, "NOTION_TOKEN", "ntn_test")


# ---- URL parsing -------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://app.notion.com/p/estsoft/Old-B2B-3a2f11f6ee6380ab815afed3cbb42d77",
        "https://www.notion.so/3a2f11f6ee6380ab815afed3cbb42d77",
        "3a2f11f6-ee63-80ab-815a-fed3cbb42d77",
        "https://www.notion.so/estsoft/Title-3a2f11f6ee6380ab815afed3cbb42d77?pvs=4",
    ],
)
def test_every_notion_url_shape_yields_the_same_page_id(url):
    """Operators paste whatever the browser gave them; all of it must work."""
    assert notion.page_id_from_url(url) == "3a2f11f6ee6380ab815afed3cbb42d77"


def test_a_url_with_no_id_is_rejected_with_a_readable_message():
    with pytest.raises(notion.NotionError):
        notion.page_id_from_url("https://example.com/not-notion")


# ---- Rendering ----------------------------------------------------------------------


def _blocks(*results, has_more=False):
    return {"results": list(results), "has_more": has_more}


@respx.mock
def test_a_policy_table_survives_as_a_table(token):
    """The B2B pages are mostly tables — prices per tier, credits per feature. Flattened
    to prose the row/column pairing is gone and the model answers with the wrong number."""
    page = "3a2f11f6ee6380ab815afed3cbb42d77"
    respx.get(f"{notion.API}/pages/{page}").mock(
        return_value=httpx.Response(
            200, json={"properties": {"title": {"type": "title", "title": [{"plain_text": "정책"}]}}}
        )
    )
    respx.get(f"{notion.API}/blocks/{page}/children").mock(
        return_value=httpx.Response(
            200,
            json=_blocks(
                {
                    "id": "h",
                    "type": "heading_2",
                    "heading_2": {"rich_text": [{"plain_text": "가격"}]},
                },
                {"id": "t", "type": "table", "table": {}, "has_children": True},
            ),
        )
    )
    respx.get(f"{notion.API}/blocks/t/children").mock(
        return_value=httpx.Response(
            200,
            json=_blocks(
                {
                    "type": "table_row",
                    "table_row": {
                        "cells": [[{"plain_text": "Tier"}], [{"plain_text": "가격"}]]
                    },
                },
                {
                    "type": "table_row",
                    "table_row": {
                        "cells": [[{"plain_text": "Tier 1"}], [{"plain_text": "$1,000"}]]
                    },
                },
            ),
        )
    )

    result = notion.fetch_page(f"https://www.notion.so/{page}")
    assert result.title == "정책"
    assert "## 가격" in result.markdown
    assert "| Tier | 가격 |" in result.markdown
    assert "| Tier 1 | $1,000 |" in result.markdown


@respx.mock
def test_an_unshared_page_says_what_to_do(token):
    """404 with a valid token means the page was never shared with the integration —
    the single most common setup mistake, so the message has to name the fix."""
    page = "3a2f11f6ee6380ab815afed3cbb42d77"
    respx.get(f"{notion.API}/pages/{page}").mock(return_value=httpx.Response(404, json={}))
    with pytest.raises(notion.NotionError) as exc:
        notion.fetch_page(page)
    assert "연결" in str(exc.value)


# ---- Sync ---------------------------------------------------------------------------


def _register(db, mode="knowledge", url="https://www.notion.so/" + "a" * 32):
    with db() as session:
        source = PolicySource(
            label="Business 플랜 정책",
            notion_url=url,
            notion_page_id=notion.page_id_from_url(url),
            mode=mode,
        )
        session.add(source)
        session.commit()
        return source.id


def _mock_page(text: str, title: str = "정책"):
    page = "a" * 32
    respx.get(f"{notion.API}/pages/{page}").mock(
        return_value=httpx.Response(
            200, json={"properties": {"t": {"type": "title", "title": [{"plain_text": title}]}}}
        )
    )
    respx.get(f"{notion.API}/blocks/{page}/children").mock(
        return_value=httpx.Response(
            200,
            json=_blocks(
                {"id": "p", "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": text}]}}
            ),
        )
    )


@respx.mock
def test_a_knowledge_page_becomes_a_document_the_router_can_pick(db, token):
    from src.agents.policy_sync import sync_policy_sources

    source_id = _register(db)
    _mock_page("Business는 3·6·12개월 약정형입니다.")

    assert sync_policy_sources() == {"synced": 1, "failed": 0, "skipped": 0}

    with db() as session:
        doc = session.query(KnowledgeDocument).one()
        assert "3·6·12개월" in doc.body
        assert doc.scope == "inbound" and doc.status == "active"
        assert session.get(PolicySource, source_id).last_error is None


@respx.mock
def test_a_failed_sync_keeps_the_previous_copy(db, token):
    """The rule this whole feature rests on. Yesterday's policy beats no policy."""
    from src.agents.policy_sync import sync_policy_sources

    source_id = _register(db)
    _mock_page("좋은 사본")
    sync_policy_sources()

    respx.get(f"{notion.API}/pages/{'a' * 32}").mock(return_value=httpx.Response(500, json={}))
    assert sync_policy_sources()["failed"] == 1

    with db() as session:
        source = session.get(PolicySource, source_id)
        assert source.body == "좋은 사본"          # not blanked
        assert source.last_error                   # but the failure is visible
        assert session.query(KnowledgeDocument).one().body == "좋은 사본"


@respx.mock
def test_an_empty_render_is_treated_as_a_failure(db, token):
    """An empty page is far likelier to be a parse problem than a deleted policy."""
    from src.agents.policy_sync import sync_policy_sources

    source_id = _register(db)
    _mock_page("실제 내용")
    sync_policy_sources()

    _mock_page("")
    assert sync_policy_sources()["failed"] == 1
    with db() as session:
        assert session.get(PolicySource, source_id).body == "실제 내용"


def test_sync_is_skipped_without_a_token(db, monkeypatch):
    from src.agents.policy_sync import sync_policy_sources
    from src.common.config import settings

    monkeypatch.setattr(settings, "NOTION_TOKEN", "")
    _register(db)
    assert sync_policy_sources() == {"synced": 0, "failed": 0, "skipped": 0}


@respx.mock
def test_renaming_a_document_updates_it_instead_of_duplicating_it(db, token):
    """The knowledge slug comes from the page id, so a relabel cannot orphan the old row
    and leave the router citing the same policy twice."""
    from src.agents.policy_sync import sync_policy_sources

    source_id = _register(db)
    _mock_page("v1")
    sync_policy_sources()
    with db() as session:
        session.get(PolicySource, source_id).label = "새 이름"
        session.commit()
    _mock_page("v2")
    sync_policy_sources()

    with db() as session:
        docs = session.query(KnowledgeDocument).all()
        assert len(docs) == 1
        assert docs[0].body == "v2"


# ---- Always-applied rules ------------------------------------------------------------


def test_rules_rows_become_the_system_instruction(db):
    from src.llm.prompts import get_company_rules

    with db() as session:
        session.add_all(
            [
                PolicySource(
                    label="톤", notion_url="", notion_page_id="file:01_tone.md", mode="rules",
                    order_index=10, body="항상 존댓말.",
                ),
                PolicySource(
                    label="CS", notion_url="", notion_page_id="file:04_cs.md", mode="rules",
                    order_index=20, body="사과는 한 번만.",
                ),
            ]
        )
        session.commit()

    rules = get_company_rules()
    assert "Company rules (must follow)" in rules
    assert rules.index("항상 존댓말.") < rules.index("사과는 한 번만.")  # order_index honoured


def test_a_paused_rule_leaves_the_prompt(db):
    from src.llm.prompts import get_company_rules

    with db() as session:
        session.add(
            PolicySource(
                label="톤", notion_url="", notion_page_id="file:x.md", mode="rules",
                body="적용되면 안 됨", status="paused",
            )
        )
        session.commit()
    assert "적용되면 안 됨" not in get_company_rules()


def test_knowledge_rows_are_not_in_the_system_instruction(db):
    """Per-inquiry documents go through the router; putting them in every prompt would
    blow the context and apply pricing policy to a report narration."""
    from src.llm.prompts import get_company_rules

    with db() as session:
        session.add(
            PolicySource(
                label="가격", notion_url="", notion_page_id="p" * 32, mode="knowledge",
                body="Tier 1 $1,000", last_synced_at=datetime(2026, 7, 30),
            )
        )
        session.commit()
    assert "Tier 1" not in get_company_rules()
