"""Read policy documents out of Notion.

Notion is the editing surface for policy: the people who own pricing, plan limits and
CS rules maintain them there, and this console must reflect an edit without a deploy and
without anyone re-typing it here. So this module is READ ONLY — it fetches a page and
renders it to Markdown, and nothing in it can write to Notion.

Why Markdown and not the raw blocks: the drafting prompt takes text, and the policy pages
are mostly tables (the B2B tier prices, the credit rates, the ticket-status flow). A table
flattened to prose loses the row/column pairing that makes it answerable, so tables are
rendered as pipe tables and everything else as plain Markdown.

Auth is a Notion INTERNAL integration token (``NOTION_TOKEN``) and each page must be
shared with that integration in Notion — without the share the API answers 404 even
though the token is valid, which is the single most common setup mistake, so
:func:`fetch_page` says so in the error instead of surfacing a bare 404.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import httpx

from ..common.config import settings

logger = logging.getLogger(__name__)

API = "https://api.notion.com/v1"
# Pinned: Notion breaks block shapes between versions, and this module parses them.
NOTION_VERSION = "2022-06-28"

_DASHED_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)
_BARE_ID = re.compile(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", re.I)


class NotionError(RuntimeError):
    """A Notion read failed. The message is shown to the operator in the console."""


class NotionNotConfigured(NotionError):
    """No NOTION_TOKEN. Distinct so the sync can skip quietly instead of alarming."""


def is_configured() -> bool:
    return bool(settings.NOTION_TOKEN.strip())


def page_id_from_url(url: str) -> str:
    """The 32-hex id out of any Notion URL (or a bare id).

    Accepts every shape Notion hands out: notion.so/Title-<id>, app.notion.com/p/<id>,
    a dashed uuid, or the id on its own. Query strings and view ids are dropped first.

    Works on the URL with its dashes intact. Stripping them globally first looks tidier
    and is wrong: "Old-B2B-3a2f…" collapses to "OldB2B3a2f…", and since b, B and 2 are
    hex digits the 32-character window can start inside the title and return an id that
    is off by one character.
    """
    raw = (url or "").split("?")[0].split("#")[0].rstrip("/")
    segment = raw.split("/")[-1]

    dashed = _DASHED_UUID.search(segment)
    if dashed:
        return dashed.group(0).replace("-", "").lower()

    # Notion appends the id last: "Title-Slug-<32hex>" or just "<32hex>".
    tail = segment.rsplit("-", 1)[-1]
    if len(tail) == 32 and _BARE_ID.fullmatch(tail):
        return tail.lower()

    found = _BARE_ID.search(segment)
    if found:
        return found.group(0).lower()
    raise NotionError(f"노션 페이지 ID를 찾을 수 없습니다: {url!r}")


@dataclass
class NotionPage:
    """A page rendered for prompt use."""

    page_id: str
    title: str
    markdown: str
    blocks: int = 0
    warnings: list[str] = field(default_factory=list)


def _client() -> httpx.Client:
    token = settings.NOTION_TOKEN.strip()
    if not token:
        raise NotionNotConfigured("NOTION_TOKEN이 설정되지 않았습니다.")
    return httpx.Client(
        base_url=API,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
        },
        timeout=30.0,
    )


def _rich_text(items: list[dict] | None) -> str:
    """Notion rich text -> plain text. Formatting is dropped on purpose.

    The consumer is an LLM prompt: bold/italic markers are noise there, and a stray
    ``**`` in policy text has repeatedly turned into ``**`` in a customer reply.
    """
    if not items:
        return ""
    out: list[str] = []
    for item in items:
        text = item.get("plain_text")
        if text is None:
            text = (item.get("text") or {}).get("content", "")
        out.append(text or "")
    return "".join(out).strip()


def _table_rows(client: httpx.Client, table_id: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for child in _children(client, table_id):
        if child.get("type") != "table_row":
            continue
        cells = child["table_row"].get("cells") or []
        rows.append([_rich_text(cell) for cell in cells])
    return rows


def _children(client: httpx.Client, block_id: str) -> list[dict]:
    """Every child block, following Notion's cursor pagination."""
    blocks: list[dict] = []
    cursor: str | None = None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        response = client.get(f"/blocks/{block_id}/children", params=params)
        if response.status_code == 404:
            raise NotionError(
                "노션이 404를 반환했습니다. 토큰은 유효하지만 이 페이지가 통합(Integration)에 "
                "공유되지 않았을 가능성이 큽니다 — 노션 페이지 우측 상단 ··· → 연결(Connections)에서 "
                "통합을 추가해 주세요."
            )
        if response.status_code == 401:
            raise NotionError("노션 토큰이 거부되었습니다(401). NOTION_TOKEN을 확인해 주세요.")
        response.raise_for_status()
        payload = response.json()
        blocks.extend(payload.get("results", []))
        if not payload.get("has_more"):
            break
        cursor = payload.get("next_cursor")
        if not cursor:
            break
    return blocks


_HEADING_PREFIX = {"heading_1": "# ", "heading_2": "## ", "heading_3": "### "}


def _render(client: httpx.Client, blocks: list[dict], depth: int = 0) -> tuple[list[str], int]:
    """Blocks -> Markdown lines. Returns the lines and how many blocks were seen."""
    lines: list[str] = []
    seen = 0
    indent = "  " * depth
    for block in blocks:
        seen += 1
        kind = block.get("type") or ""
        data = block.get(kind) or {}
        text = _rich_text(data.get("rich_text"))

        if kind in _HEADING_PREFIX:
            lines.append(f"\n{_HEADING_PREFIX[kind]}{text}\n")
        elif kind == "paragraph":
            lines.append(f"{indent}{text}" if text else "")
        elif kind == "bulleted_list_item":
            lines.append(f"{indent}- {text}")
        elif kind == "numbered_list_item":
            lines.append(f"{indent}1. {text}")
        elif kind == "to_do":
            mark = "x" if data.get("checked") else " "
            lines.append(f"{indent}- [{mark}] {text}")
        elif kind == "quote":
            lines.append(f"{indent}> {text}")
        elif kind == "callout":
            lines.append(f"{indent}> {text}")
        elif kind == "code":
            lines.append(f"```\n{text}\n```")
        elif kind == "divider":
            lines.append("\n---\n")
        elif kind == "table":
            rows = _table_rows(client, block["id"])
            if rows:
                width = max(len(r) for r in rows)
                header, *body = rows
                header = header + [""] * (width - len(header))
                lines.append("| " + " | ".join(header) + " |")
                lines.append("|" + "---|" * width)
                for row in body:
                    row = row + [""] * (width - len(row))
                    lines.append("| " + " | ".join(row) + " |")
                lines.append("")
            continue  # table_row children are already consumed
        elif kind in ("child_page", "child_database"):
            # Not followed: a policy page's children are separate documents, and
            # walking them silently would pull in unrelated pages.
            lines.append(f"{indent}- (하위 페이지 생략: {data.get('title') or block.get('id')})")
            continue
        elif text:
            lines.append(f"{indent}{text}")

        if block.get("has_children") and kind not in ("table", "child_page", "child_database"):
            child_lines, child_seen = _render(client, _children(client, block["id"]), depth + 1)
            lines.extend(child_lines)
            seen += child_seen
    return lines, seen


def _page_title(client: httpx.Client, page_id: str) -> str:
    response = client.get(f"/pages/{page_id}")
    if response.status_code == 404:
        raise NotionError(
            "노션이 404를 반환했습니다. 페이지를 통합에 공유했는지 확인해 주세요 "
            "(페이지 ··· → 연결)."
        )
    response.raise_for_status()
    for prop in (response.json().get("properties") or {}).values():
        if prop.get("type") == "title":
            title = _rich_text(prop.get("title"))
            if title:
                return title
    return "(제목 없음)"


def fetch_page(url_or_id: str) -> NotionPage:
    """Read one Notion page and render it to Markdown. Read-only."""
    page_id = page_id_from_url(url_or_id)
    with _client() as client:
        title = _page_title(client, page_id)
        lines, seen = _render(client, _children(client, page_id))
    markdown = "\n".join(lines).strip()
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    logger.info("Notion page %s read (%d blocks, %d chars)", page_id, seen, len(markdown))
    return NotionPage(page_id=page_id, title=title, markdown=markdown, blocks=seen)
