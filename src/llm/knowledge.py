"""
Knowledge base loader.

Reads markdown files under `knowledge_base/` and selects the ones relevant to
an inbound category (matched via the optional `categories:` frontmatter list).

Each file may declare frontmatter at the top:

    ---
    title: 2026 Pricing
    categories: [pricing_question, purchase_inquiry]
    ---

    Body...

Rules:
- `categories` may include the literal value `all`, in which case the file is
  used for every inbound category (except `spam`).
- If a file omits `categories` entirely, it is treated as `[all]`.
- Files without a frontmatter block are also treated as `[all]`.

The loader returns a single formatted string ready to embed in a prompt
template, or an empty string if no docs match.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR = REPO_ROOT / "knowledge_base"

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
_LIST_INLINE_RE = re.compile(r"\[(.*)\]")


def _parse_frontmatter(raw: str) -> tuple[dict[str, object], str]:
    """Return (metadata, body). Tiny YAML-subset parser; no pyyaml dependency."""
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw
    header, body = match.group(1), match.group(2)
    meta: dict[str, object] = {}
    for line in header.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        list_match = _LIST_INLINE_RE.match(value)
        if list_match:
            inner = list_match.group(1)
            items = [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
            meta[key] = items
        else:
            meta[key] = value.strip("'\"")
    return meta, body


def _matches(category: str, file_categories: list[str]) -> bool:
    if not file_categories:
        return True
    normalized = {c.lower() for c in file_categories}
    if "all" in normalized:
        return True
    return category.lower() in normalized


@lru_cache
def _scan() -> tuple[tuple[str, list[str], str], ...]:
    """Read every .md file once and return (title, categories, body) tuples."""
    if not KNOWLEDGE_DIR.exists():
        return ()
    out: list[tuple[str, list[str], str]] = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        raw = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(raw)
        title = str(meta.get("title") or path.stem)
        categories_raw = meta.get("categories")
        if isinstance(categories_raw, list):
            categories = [str(c) for c in categories_raw]
        elif isinstance(categories_raw, str) and categories_raw:
            categories = [categories_raw]
        else:
            categories = []
        out.append((title, categories, body.strip()))
    return tuple(out)


def reset_cache() -> None:
    """Invalidate the scan cache. Used by tests and by the BE on reload."""
    _scan.cache_clear()


def load_relevant_docs(category: str) -> str:
    """
    Return a single formatted string of all knowledge_base docs matching `category`.

    Empty string if `knowledge_base/` is missing, empty, or has no matches.
    Spam category always returns empty (no need for sales material).
    """
    if not category or category.lower() == "spam":
        return ""
    matched: list[str] = []
    for title, categories, body in _scan():
        if _matches(category, categories):
            matched.append(f"### {title}\n{body}")
    if not matched:
        return ""
    return "## Relevant knowledge base documents\n\n" + "\n\n---\n\n".join(matched)
