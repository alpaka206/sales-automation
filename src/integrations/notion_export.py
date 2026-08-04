"""Read policy pages out of a Notion **Markdown export**, with no API at all.

Why this exists: ``integrations/notion.py`` needs an internal integration token, and on a
company workspace nobody but the workspace owner can create one — so on that workspace the
official path is not "misconfigured", it is unavailable. Notion's own export is the way
out: it needs nothing but a logged-in browser, it is a supported feature rather than a
scrape, and it renders the tables that carry the actual numbers.

One parser serves both local routes, because both end in the same zip:

  manual   페이지 ··· → Export → Markdown & CSV  → the file the browser downloads
  session  ``notion_session.export_page_zip`` asks Notion for that same export

Matching to a registered page is by **id, never by title**: an export filename ends with
the page's 32-hex id ("B2B 가격 정책 3a2f11f6ee6380ab815afed3cbb42d77.md"), and a title an
owner renames in Notion must still land on the row that was registered with that URL.

Read-only in the strictest sense — this module opens a zip and a directory and nothing
else. It cannot reach the network.

zip 을 파싱하는 이유: docs/정책문서-동기화-설계.md
"""

from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from pathlib import Path

from .notion import NotionError, NotionPage, page_id_from_url

logger = logging.getLogger(__name__)

# The id Notion appends to every exported filename, with or without dashes.
_ID_IN_NAME = re.compile(
    r"(?<![0-9a-f])([0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12})(?![0-9a-f])",
    re.I,
)


class NotionExportError(NotionError):
    """The export could not be read, or holds no page we were asked for."""


def _page_id_in_filename(name: str) -> str | None:
    """The 32-hex page id an export filename ends with, or None."""
    stem = Path(name).stem
    found = _ID_IN_NAME.findall(stem)
    # Last match: a title can itself contain hex-looking text, the id is always last.
    return found[-1].replace("-", "").lower() if found else None


def _csv_as_table(text: str) -> str:
    """A database export -> a pipe table.

    Notion exports an inline database as a separate CSV and leaves a link behind in the
    Markdown. Dropping it loses exactly the rows that matter (tier prices, credit rates),
    and a table read as prose loses the row/column pairing that makes it answerable — the
    same reason ``notion.py`` renders tables as pipe tables.
    """
    rows = [row for row in csv.reader(io.StringIO(text)) if any(cell.strip() for cell in row)]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    header, *body = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |" for row in body]
    return "\n".join(lines)


def _split_title(markdown: str) -> tuple[str, str]:
    """Notion writes the page title as the first H1. Take it out of the body.

    The title is stored in its own column, and leaving the H1 in place made every synced
    document start by repeating its own name to the model.
    """
    lines = markdown.replace("\r\n", "\n").split("\n")
    title = ""
    start = 0
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if line.startswith("# "):
            title = line[2:].strip()
            start = index + 1
        break
    body = "\n".join(lines[start:]).strip()
    return title, body


def _decode(raw: bytes) -> str:
    """Notion exports UTF-8; a BOM turns up on the Windows download often enough."""
    return raw.decode("utf-8-sig", errors="replace")


def _members(source: Path | bytes) -> list[tuple[str, bytes]]:
    """(name, content) for every file in a zip, a folder, or downloaded zip bytes."""
    if isinstance(source, bytes):
        with zipfile.ZipFile(io.BytesIO(source)) as archive:
            return [(item.filename, archive.read(item)) for item in archive.infolist()
                    if not item.is_dir()]
    path = Path(source)
    if path.is_dir():
        return [
            (str(child.relative_to(path)), child.read_bytes())
            for child in sorted(path.rglob("*"))
            if child.is_file()
        ]
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            return [(item.filename, archive.read(item)) for item in archive.infolist()
                    if not item.is_dir()]
    if path.suffix.lower() in {".md", ".markdown", ".txt"}:
        return [(path.name, path.read_bytes())]
    raise NotionExportError(
        f"읽을 수 없는 경로입니다: {path} — 노션 내보내기 zip 파일이나 압축을 푼 폴더를 지정해 주세요."
    )


def read_export(source: Path | str | bytes) -> dict[str, NotionPage]:
    """Parse an export into ``{page_id: NotionPage}``.

    Accepts the downloaded zip, an unzipped folder (a whole-workspace export works —
    pages nobody registered are simply never looked up), or raw zip bytes.

    CSVs are appended to the page they were exported alongside. In a per-page export that
    is unambiguous; in a workspace export a database sits in the folder named after its
    parent page, which is the same rule.
    """
    pages: dict[str, NotionPage] = {}
    csvs: list[tuple[str, str, str]] = []  # (folder, display name, rendered table)

    for name, raw in _members(source):
        suffix = Path(name).suffix.lower()
        if suffix in {".md", ".markdown"}:
            page_id = _page_id_in_filename(name)
            if not page_id:
                continue
            title, body = _split_title(_decode(raw))
            pages[page_id] = NotionPage(
                page_id=page_id,
                title=title or Path(name).stem,
                markdown=body,
                blocks=len([line for line in body.split("\n") if line.strip()]),
            )
        elif suffix == ".csv":
            # "…_all.csv" is Notion's duplicate of a view export; skip it so a table is
            # not appended to the page twice.
            if Path(name).stem.endswith("_all"):
                continue
            table = _csv_as_table(_decode(raw))
            if table:
                display = _ID_IN_NAME.sub("", Path(name).stem).strip(" -_")
                csvs.append((str(Path(name).parent), display, table))

    for folder, display, table in csvs:
        owner = _owner_of(folder, pages)
        if owner is None:
            continue
        heading = f"\n\n## {display}\n\n" if display else "\n\n"
        owner.markdown = (owner.markdown + heading + table).strip()

    if not pages:
        raise NotionExportError(
            "내보내기에서 노션 페이지(.md)를 찾지 못했습니다. "
            "Markdown & CSV 형식으로 내보냈는지 확인해 주세요."
        )
    return pages


def _owner_of(folder: str, pages: dict[str, NotionPage]) -> NotionPage | None:
    """Which page a CSV belongs to: the one whose id names its folder, else the only one."""
    for part in reversed(Path(folder).parts):
        page_id = _page_id_in_filename(part)
        if page_id and page_id in pages:
            return pages[page_id]
    return next(iter(pages.values())) if len(pages) == 1 else None


def fetcher_from_export(source: Path | str | bytes):
    """A ``sync_policy_sources`` fetcher backed by one export.

    Raises for a registered page the export does not contain, which the sync turns into
    "this row kept its previous copy" plus a readable ``last_error`` — the operator then
    knows exactly which page they forgot to include.
    """
    pages = read_export(source)

    def fetch(url_or_id: str) -> NotionPage:
        page_id = page_id_from_url(url_or_id)
        page = pages.get(page_id)
        if page is None:
            raise NotionExportError(
                f"내보내기에 이 페이지가 없습니다 (id {page_id[:8]}…). "
                "해당 노션 페이지에서 ··· → Export → Markdown & CSV 로 다시 내보내 주세요."
            )
        return page

    return fetch
