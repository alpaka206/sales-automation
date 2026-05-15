"""Import existing knowledge_base/*.md files into the knowledge_documents table."""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.logging import setup_logging  # noqa: E402
from src.db.session import SessionLocal  # noqa: E402
from src.db.models import KnowledgeDocument  # noqa: E402

setup_logging()
logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "knowledge_base"
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
_LIST_INLINE_RE = re.compile(r"\[(.*)\]")

SKIP_FILES = {"readme.md"}


def _parse_frontmatter(raw: str) -> tuple[dict[str, object], str]:
    """Return (metadata, body). Tiny YAML-subset parser."""
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


def _slug_from_filename(name: str) -> str:
    """Derive a URL-friendly slug from a filename (without extension)."""
    return Path(name).stem.lower().replace(" ", "-")


def main() -> None:
    """Read knowledge_base/*.md and upsert into knowledge_documents."""
    if not KNOWLEDGE_DIR.exists():
        logger.warning("knowledge_base/ directory not found — nothing to import.")
        return

    md_files = sorted(KNOWLEDGE_DIR.glob("*.md"))
    imported = 0

    session = SessionLocal()
    try:
        for path in md_files:
            if path.name.lower() in SKIP_FILES:
                logger.info("Skipping %s", path.name)
                continue

            raw = path.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(raw)

            title = str(meta.get("title") or path.stem)
            slug = _slug_from_filename(path.name)

            categories_raw = meta.get("categories")
            if isinstance(categories_raw, list):
                categories = [str(c) for c in categories_raw]
            elif isinstance(categories_raw, str) and categories_raw:
                categories = [categories_raw]
            else:
                categories = []

            existing = session.query(KnowledgeDocument).filter_by(slug=slug).first()
            if existing:
                existing.title = title
                existing.categories = categories
                existing.body = body.strip()
                logger.info("Updated existing doc: %s", slug)
            else:
                doc = KnowledgeDocument(
                    title=title,
                    slug=slug,
                    categories=categories,
                    scope="both",
                    body=body.strip(),
                )
                session.add(doc)
                logger.info("Inserted new doc: %s", slug)
            imported += 1

        session.commit()
        logger.info("Import complete - %d document(s) processed.", imported)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
