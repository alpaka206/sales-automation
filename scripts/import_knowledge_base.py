r"""Seed the knowledge_documents table from the bundled Markdown files.

The database is the source of truth for knowledge, not these files. They exist to give a
fresh install something to answer with; after the first load, documents are edited in the
console or refreshed from Notion (``scripts/sync_notion_local.py``).

So this INSERTS what is missing and leaves what is already there alone. It used to upsert
— re-running it overwrote every edit made since, which is exactly the failure mode of
keeping content in two places. ``--force`` restores a document from its file on purpose.

    .\.venv\Scripts\python.exe scripts/import_knowledge_base.py
    .\.venv\Scripts\python.exe scripts/import_knowledge_base.py --force
"""

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

# Seeds moved under src/ so they ship in the wheel; the repo-root folder is gone.
KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "src" / "db" / "seeds" / "knowledge"
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


def _as_list(value: object) -> list[str]:
    """Coerce a frontmatter value (list | scalar | None) to list[str]."""
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value:
        return [value]
    return []


def main(force: bool = False) -> None:
    """Insert any bundled document the database does not have yet."""
    if not KNOWLEDGE_DIR.exists():
        logger.warning("knowledge_base/ directory not found — nothing to import.")
        return

    md_files = sorted(KNOWLEDGE_DIR.glob("*.md"))
    inserted = 0
    kept = 0

    session = SessionLocal()
    try:
        for path in md_files:
            # Skip the README and any template/scratch file (leading underscore).
            if path.name.lower() in SKIP_FILES or path.name.startswith("_"):
                logger.info("Skipping %s", path.name)
                continue

            raw = path.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(raw)

            title = str(meta.get("title") or path.stem)
            slug = _slug_from_filename(path.name)
            categories = _as_list(meta.get("categories"))
            tags = _as_list(meta.get("tags"))
            summary = str(meta.get("summary") or "") or None
            author = str(meta.get("author") or "") or None
            scope = str(meta.get("scope") or "both") or "both"
            status = str(meta.get("status") or "active") or "active"

            existing = session.query(KnowledgeDocument).filter_by(slug=slug).first()
            if existing and not force:
                # Already in the database, which owns it now. Overwriting here is how an
                # operator's edit silently disappears on the next deploy.
                kept += 1
                continue
            if existing:
                existing.title = title
                existing.categories = categories
                existing.tags = tags
                existing.summary = summary
                existing.author = author
                existing.scope = scope
                existing.status = status
                existing.body = body.strip()
                logger.info("Restored from file (--force): %s", slug)
            else:
                doc = KnowledgeDocument(
                    title=title,
                    slug=slug,
                    categories=categories,
                    tags=tags,
                    summary=summary,
                    author=author,
                    scope=scope,
                    status=status,
                    body=body.strip(),
                )
                session.add(doc)
                logger.info("Inserted new doc: %s", slug)
            inserted += 1

        session.commit()
        logger.info(
            "Seed complete - %d inserted, %d already in the database (left untouched).",
            inserted,
            kept,
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    import sys

    main(force="--force" in sys.argv)
