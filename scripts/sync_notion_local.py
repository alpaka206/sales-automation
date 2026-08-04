"""로컬에서 노션을 읽어 정책 문서 사본을 갱신합니다 (노션 API 없이).

왜 필요한가
    회사 노션이라 내부 통합(Integration) 토큰을 만들 수 없으면 서버는 노션을 아예
    읽지 못합니다. 이 스크립트는 담당자 PC에서, 담당자 본인 계정으로 노션을 읽어
    DB의 정책 문서(policy_sources)와 지식 문서(knowledge_documents)를 갱신합니다.
    콘솔 화면에는 아무것도 추가되지 않습니다 — 등록/조회는 기존 '정책 문서' 화면 그대로고,
    이 파일은 그 화면의 '동기화' 버튼이 못 하는 일만 대신합니다.

쓰는 법 (PowerShell, 프로젝트 폴더에서)

  1) 자동 — 한 번만 설정하면 명령 한 줄
     .env 에 NOTION_TOKEN_V2 를 넣고(값 얻는 법은 .env.example 참고):
       .\.venv\Scripts\python.exe scripts/sync_notion_local.py

  2) 수동 — 쿠키도 쓰기 싫거나 1)이 막힐 때. 항상 됩니다.
     노션 페이지에서 ··· → Export → Markdown & CSV 로 받은 zip 을 그대로 지정:
       .\.venv\Scripts\python.exe scripts/sync_notion_local.py --export "C:\...\Export.zip"
     워크스페이스 전체를 내보낸 zip/폴더도 됩니다 (등록 안 된 페이지는 무시).

  그 밖에
     --only 3        정책 문서 화면의 특정 행(id)만 갱신
     --dry-run       DB에 쓰지 않고 무엇이 갱신될지만 출력
     --check         노션에 닿는지만 확인하고 끝

먼저 '정책 문서' 화면에서 페이지를 등록해 두어야 합니다. 이 스크립트는 등록된 URL만 읽고,
읽기 전용입니다 — 노션에 아무것도 쓰지 않습니다.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agents.policy_sync import sync_policy_sources  # noqa: E402
from src.common.logging import setup_logging  # noqa: E402
from src.db.models import PolicySource  # noqa: E402
from src.db.session import SessionLocal  # noqa: E402

logger = logging.getLogger(__name__)


def _registered(only_id: int | None) -> list[tuple[int, str, str, str]]:
    """(id, label, url, mode) for the rows this run would touch."""
    with SessionLocal() as session:
        query = session.query(PolicySource).filter(PolicySource.status == "active")
        if only_id is not None:
            query = query.filter(PolicySource.id == only_id)
        return [
            (row.id, row.label, row.notion_url or "", row.mode)
            for row in query.order_by(PolicySource.order_index, PolicySource.id).all()
        ]


def _pick_fetcher(args):
    """(fetcher, how) — the reader this run should use, and what to print about it.

    Order is deliberate: an explicit --export wins, then the browser session, then the
    official token. Someone who passes a file means that file.
    """
    if args.export:
        from src.integrations.notion_export import fetcher_from_export

        return fetcher_from_export(args.export), f"내보내기 파일 ({args.export})"

    from src.integrations import notion, notion_session

    if notion_session.is_configured():
        return notion_session.fetch_page, "브라우저 세션 (NOTION_TOKEN_V2)"
    if notion.is_configured():
        # The server's own path. Works here too, and needs no cookie.
        return notion.fetch_page, "노션 통합 토큰 (NOTION_TOKEN)"
    return None, ""


def _no_route_message() -> str:
    return (
        "노션을 읽을 방법이 없습니다. 둘 중 하나를 선택하세요:\n"
        "  1) .env 에 NOTION_TOKEN_V2 를 넣기 (notion.so → F12 → Application → Cookies → token_v2)\n"
        '  2) 노션에서 ··· → Export → Markdown & CSV 로 받은 zip 을 --export "경로" 로 지정'
    )


def _run_via_server(args, fetcher) -> int:
    """서버에서 목록을 받아 노션을 읽고, 읽은 것을 서버로 되돌려 줍니다.

    로컬은 DB를 전혀 건드리지 않습니다. 이 PC가 DB에 닿지 못하는 것이 이 경로가 존재하는
    이유이고, 여기서 DB를 열려고 하면 30초 타임아웃 말고는 아무 일도 일어나지 않습니다.
    """
    from src.common.config import settings
    from src.integrations.policy_push import PolicyPushError, PolicyServer

    base = args.server or settings.PUBLIC_BASE_URL
    try:
        server = PolicyServer(base, settings.INTERNAL_API_TOKEN)
        sources = server.sources()
    except PolicyPushError as exc:
        print(f"[실패] {exc}")
        return 2

    print(f"서버: {base}")
    if not sources:
        print("서버에 등록된 노션 문서가 없습니다.")
        print("콘솔 [이메일 템플릿 → 정책 문서 → 노션 문서 추가] 에서 먼저 등록해 주세요.")
        return 1
    if args.only is not None:
        sources = [s for s in sources if s["id"] == args.only]
    print(f"대상 {len(sources)}건")

    pages, failed = [], 0
    for source in sources:
        try:
            page = fetcher(source["notion_url"])
        except Exception as exc:
            failed += 1
            print(f"  ✗ [{source['id']}] {source['label']}: {exc}")
            continue
        print(f"  ✓ [{source['id']}] {source['label']} → '{page.title}' {len(page.markdown):,}자")
        pages.append(
            {"notion_url": source["notion_url"], "title": page.title, "markdown": page.markdown}
        )

    if args.check or args.dry_run:
        print("서버에는 아무것도 올리지 않았습니다.")
        return 1 if failed else 0
    if not pages:
        print("올릴 내용이 없습니다.")
        return 1

    try:
        result = server.push(pages)
    except PolicyPushError as exc:
        print(f"[실패] {exc}")
        return 2

    print()
    print(f"완료: 갱신 {result['synced']} · 실패 {result['failed']} · 건너뜀 {result['skipped']}")
    print("콘솔의 '정책 문서' 화면에서 마지막 동기화 시각으로 확인할 수 있습니다.")
    return 1 if (failed or result["failed"]) else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="노션 정책 문서를 로컬에서 읽어 DB 사본을 갱신합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--export", metavar="PATH", help="노션 Markdown & CSV 내보내기 zip 또는 폴더")
    parser.add_argument("--only", type=int, metavar="ID", help="정책 문서 화면의 특정 행만 갱신")
    parser.add_argument("--dry-run", action="store_true", help="DB에 쓰지 않고 결과만 출력")
    parser.add_argument("--check", action="store_true", help="노션에 닿는지만 확인")
    parser.add_argument(
        "--server",
        metavar="URL",
        help="DB 대신 이 서버로 올립니다 (기본: .env 의 PUBLIC_BASE_URL). "
        "사내망에서는 DB 포트가 막혀 있어 이 방법만 됩니다.",
    )
    parser.add_argument("--local-db", action="store_true", help="서버 대신 DB에 직접 씁니다")
    args = parser.parse_args()

    setup_logging()
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        fetcher, how = _pick_fetcher(args)
    except Exception as exc:  # a bad --export path, mainly
        print(f"[실패] {exc}")
        return 2
    if fetcher is None:
        print(_no_route_message())
        return 2
    print(f"읽는 방법: {how}")

    # 서버로 올리는 것이 기본입니다. 사내망에서 DB 포트가 막혀 있어 --local-db 는 여기서
    # 타임아웃으로 끝나고, 그 사실을 먼저 알려 주는 편이 낫습니다.
    if not args.local_db:
        return _run_via_server(args, fetcher)

    rows = _registered(args.only)
    if not rows:
        print("갱신할 정책 문서가 없습니다. 콘솔의 '정책 문서' 화면에서 먼저 등록해 주세요.")
        return 1
    print(f"대상 {len(rows)}건")

    if args.check or args.dry_run:
        # Read every page but never write. --check stops at the first page: it answers
        # "does this route work at all", and exporting the rest would just be slow.
        failed = 0
        for source_id, label, url, _mode in rows:
            if not url.strip():
                print(f"  - [{source_id}] {label}: 노션 URL 없음 (파일에서 가져온 행) — 건너뜀")
                continue
            try:
                page = fetcher(url)
            except Exception as exc:
                failed += 1
                print(f"  ✗ [{source_id}] {label}: {exc}")
            else:
                print(f"  ✓ [{source_id}] {label} → '{page.title}' {len(page.markdown):,}자")
            if args.check:
                break
        print("DB에는 아무것도 쓰지 않았습니다.")
        return 1 if failed else 0

    # A page that cannot be read is a handled outcome here — the row keeps its previous
    # copy — so its stack trace would be the loudest thing on screen for a case the
    # summary below already reports, per row, in Korean.
    logging.getLogger("src.agents.policy_sync").setLevel(logging.ERROR)
    result = sync_policy_sources(only_id=args.only, fetcher=fetcher)

    with SessionLocal() as session:
        query = session.query(PolicySource).filter(PolicySource.status == "active")
        if args.only is not None:
            query = query.filter(PolicySource.id == args.only)
        for row in query.order_by(PolicySource.order_index, PolicySource.id).all():
            if not (row.notion_url or "").strip():
                print(f"  - [{row.id}] {row.label}: 노션 URL 없음 (파일에서 가져온 행) — 건너뜀")
            elif row.last_error:
                print(f"  ✗ [{row.id}] {row.label}: {row.last_error}")
            else:
                print(f"  ✓ [{row.id}] {row.label} → '{row.title}' {len(row.body or ''):,}자")

    print(
        f"완료: 갱신 {result['synced']}건 · 실패 {result['failed']}건 "
        f"· 건너뜀 {result['skipped']}건"
    )
    if result["failed"]:
        # Failure never drops policy — the row keeps its previous copy — so this is a
        # warning, not a broken state. The same reason shows on the 정책 문서 screen.
        print("실패한 문서는 이전 사본을 그대로 사용합니다.")
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
