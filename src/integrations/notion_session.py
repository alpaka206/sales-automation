"""Ask Notion for a page export using the operator's own browser session.

This is the automatic half of the local sync. It exists for one situation: a company
workspace where nobody can create an internal integration, so ``NOTION_TOKEN`` — and with
it ``integrations/notion.py`` — is simply not available.

What it does is what the operator does by hand: it triggers **Notion's own export** for a
page and downloads the resulting zip. The parsing then happens in ``notion_export.py``, the
same code that reads a manually downloaded export, so the fragile part of this file is two
requests and nothing else. If Notion changes those endpoints, a manual export still works
and only this module needs fixing.

**Local only, on purpose.** It authenticates with ``token_v2``, the cookie of a logged-in
Notion session — a personal credential, not a service credential. Nothing the server
imports may reach this module, and ``tests/test_notion_local_sync.py`` asserts that.

Getting the cookie (once; it lasts months):
    notion.so → F12 → Application → Cookies → https://www.notion.so → ``token_v2``
    put it in ``.env`` as ``NOTION_TOKEN_V2=v02%3Auser_token_or…``

Read-only: the only task it enqueues is ``exportBlock``. It cannot edit a page.
"""

from __future__ import annotations

import logging
import time

import httpx

from ..common.config import settings
from .notion import NotionError, page_id_from_url

logger = logging.getLogger(__name__)

BASE = "https://www.notion.so/api/v3"
# Notion answers a bare client with a challenge page rather than JSON.
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
_POLL_SECONDS = 2.0
_POLL_LIMIT = 60  # 2 minutes: a policy page exports in seconds, a huge one takes longer.


class NotionSessionError(NotionError):
    """The browser-session route failed. The message says which half to fix."""


def is_configured() -> bool:
    return bool(settings.NOTION_TOKEN_V2.strip())


def dashed(page_id: str) -> str:
    """32-hex -> 8-4-4-4-12. The v3 endpoints reject the undashed form."""
    raw = page_id.replace("-", "")
    return f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"


def _client() -> httpx.Client:
    token = settings.NOTION_TOKEN_V2.strip()
    if not token:
        raise NotionSessionError(
            "NOTION_TOKEN_V2가 설정되지 않았습니다. notion.so 로그인 상태에서 "
            "F12 → Application → Cookies → token_v2 값을 .env에 넣어 주세요."
        )
    headers = {
        "Cookie": f"token_v2={token}",
        "Content-Type": "application/json",
        "User-Agent": _UA,
        "Notion-Client-Version": "23.13.0",
    }
    # Only needed when one browser session holds several Notion accounts; Notion then
    # rejects an ambiguous request instead of guessing which workspace to read.
    active_user = settings.NOTION_ACTIVE_USER_ID.strip()
    if active_user:
        headers["x-notion-active-user-header"] = active_user
    return httpx.Client(base_url=BASE, headers=headers, timeout=60.0)


def _post(client: httpx.Client, path: str, payload: dict) -> dict:
    response = client.post(path, json=payload)
    if response.status_code in (401, 403):
        raise NotionSessionError(
            "노션이 세션을 거부했습니다(401/403). token_v2 쿠키가 만료됐을 가능성이 큽니다 — "
            "브라우저에서 다시 복사해 .env의 NOTION_TOKEN_V2를 갱신해 주세요."
        )
    if response.status_code >= 400:
        raise NotionSessionError(
            f"노션 요청이 실패했습니다({response.status_code}). {response.text[:200]}"
        )
    try:
        return response.json()
    except ValueError as exc:  # an HTML challenge page, not JSON
        raise NotionSessionError(
            "노션이 JSON 대신 HTML을 반환했습니다. 쿠키가 만료됐거나 회사 네트워크가 "
            "요청을 가로채고 있을 수 있습니다."
        ) from exc


def export_page_zip(url_or_id: str, *, time_zone: str = "Asia/Seoul") -> bytes:
    """Trigger Notion's Markdown export for one page and return the zip bytes.

    Three steps, exactly what the browser does: enqueue an ``exportBlock`` task, poll it,
    then download the signed URL Notion hands back (that download carries no cookie — the
    URL is the credential).
    """
    page_id = dashed(page_id_from_url(url_or_id))
    with _client() as client:
        task = _post(
            client,
            "/enqueueTask",
            {
                "task": {
                    "eventName": "exportBlock",
                    "request": {
                        "block": {"id": page_id},
                        "exportOptions": {
                            "exportType": "markdown",
                            "timeZone": time_zone,
                            "locale": "en",
                            "collectionViewExportType": "currentView",
                        },
                        # False on purpose: a policy page's subpages are separate
                        # documents, the same boundary integrations/notion.py draws.
                        "recursive": False,
                    },
                }
            },
        )
        task_id = task.get("taskId")
        if not task_id:
            raise NotionSessionError(f"내보내기 작업을 시작하지 못했습니다: {task}")

        export_url = ""
        for _ in range(_POLL_LIMIT):
            time.sleep(_POLL_SECONDS)
            results = _post(client, "/getTasks", {"taskIds": [task_id]}).get("results") or []
            if not results:
                continue
            state = results[0].get("state")
            if state == "success":
                export_url = (results[0].get("status") or {}).get("exportURL", "")
                break
            if state == "failure":
                reason = (results[0].get("error") or results[0].get("status") or "")
                raise NotionSessionError(f"노션 내보내기가 실패했습니다: {reason}")
        if not export_url:
            raise NotionSessionError(
                "노션 내보내기가 제한 시간 안에 끝나지 않았습니다. 페이지가 매우 크면 "
                "브라우저에서 직접 내보낸 뒤 --export 로 넣어 주세요."
            )

        # Signed S3 URL — a separate host, and the cookie must not be sent to it.
        with httpx.Client(timeout=120.0, follow_redirects=True) as plain:
            download = plain.get(export_url, headers={"User-Agent": _UA})
            download.raise_for_status()
            logger.info("Notion export downloaded for %s (%d bytes)", page_id, len(download.content))
            return download.content


def fetch_page(url_or_id: str):
    """A ``sync_policy_sources`` fetcher: export this page, then parse it.

    Same signature and same return type as ``notion.fetch_page``, so everything
    downstream — what is stored, what fails safe — is unchanged.
    """
    from .notion_export import NotionExportError, read_export

    pages = read_export(export_page_zip(url_or_id))
    page_id = page_id_from_url(url_or_id)
    page = pages.get(page_id)
    if page is None:
        # A single-page export holds exactly one page; take it rather than failing on an
        # id Notion normalized differently.
        if len(pages) == 1:
            return next(iter(pages.values()))
        raise NotionExportError(f"내보낸 파일에서 페이지를 찾지 못했습니다 (id {page_id[:8]}…).")
    return page
