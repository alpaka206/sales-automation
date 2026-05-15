"""Tests for the knowledge_base loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.llm import knowledge


@pytest.fixture()
def kb_dir(tmp_path, monkeypatch):
    """Point the knowledge loader at a temp dir and clear its cache."""
    monkeypatch.setattr(knowledge, "KNOWLEDGE_DIR", tmp_path)
    knowledge.reset_cache()
    yield tmp_path
    knowledge.reset_cache()


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_frontmatter_parsing_extracts_categories(kb_dir: Path) -> None:
    _write(
        kb_dir / "pricing.md",
        "---\ntitle: 2026 Pricing\ncategories: [pricing_question, purchase_inquiry]\n---\n\nBody text here.\n",
    )

    out = knowledge.load_relevant_docs("pricing_question")
    assert "2026 Pricing" in out
    assert "Body text here." in out


def test_no_match_returns_empty(kb_dir: Path) -> None:
    _write(
        kb_dir / "pricing.md",
        "---\ncategories: [pricing_question]\n---\n\nPricing info.\n",
    )

    assert knowledge.load_relevant_docs("recruiting") == ""


def test_all_keyword_matches_every_category(kb_dir: Path) -> None:
    _write(
        kb_dir / "company_overview.md",
        "---\ntitle: About Us\ncategories: [all]\n---\n\nWe are a company.\n",
    )

    for cat in ("purchase_inquiry", "partnership", "support", "recruiting", "other"):
        out = knowledge.load_relevant_docs(cat)
        assert "About Us" in out, f"missing for category={cat}"


def test_missing_categories_field_defaults_to_all(kb_dir: Path) -> None:
    _write(
        kb_dir / "general.md",
        "---\ntitle: General Info\n---\n\nApplies to everything.\n",
    )

    assert "General Info" in knowledge.load_relevant_docs("partnership")
    assert "General Info" in knowledge.load_relevant_docs("support")


def test_no_frontmatter_at_all_defaults_to_all(kb_dir: Path) -> None:
    _write(kb_dir / "loose.md", "Just plain markdown.\n")

    out = knowledge.load_relevant_docs("purchase_inquiry")
    assert "Just plain markdown." in out


def test_spam_category_returns_empty(kb_dir: Path) -> None:
    _write(
        kb_dir / "general.md",
        "---\ncategories: [all]\n---\n\nApplies to everything.\n",
    )

    assert knowledge.load_relevant_docs("spam") == ""


def test_readme_is_excluded(kb_dir: Path) -> None:
    _write(
        kb_dir / "README.md",
        "---\ncategories: [all]\n---\n\nThis README should NOT appear in prompts.\n",
    )
    _write(
        kb_dir / "real_doc.md",
        "---\ncategories: [all]\n---\n\nActual content.\n",
    )

    out = knowledge.load_relevant_docs("purchase_inquiry")
    assert "Actual content." in out
    assert "should NOT appear" not in out


def test_missing_directory_returns_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(knowledge, "KNOWLEDGE_DIR", tmp_path / "does_not_exist")
    knowledge.reset_cache()
    try:
        assert knowledge.load_relevant_docs("purchase_inquiry") == ""
    finally:
        knowledge.reset_cache()


def test_multiple_matches_are_separated(kb_dir: Path) -> None:
    _write(
        kb_dir / "a_pricing.md",
        "---\ntitle: Pricing A\ncategories: [pricing_question]\n---\n\nA body.\n",
    )
    _write(
        kb_dir / "b_plans.md",
        "---\ntitle: Plans B\ncategories: [pricing_question]\n---\n\nB body.\n",
    )

    out = knowledge.load_relevant_docs("pricing_question")
    assert "Pricing A" in out
    assert "Plans B" in out
    assert "---" in out  # separator between docs
