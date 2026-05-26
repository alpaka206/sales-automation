"""
HubSpot Ticket 기반 인바운드 수동 트리거.

사용 시나리오:
1. HubSpot UI 에서 운영팀이 새 Ticket 을 만든다 (Contact 와 연결되어 있어야 함).
2. Ticket URL 마지막 숫자(예: app.hubspot.com/contacts/.../record/0-5/12345678 → 12345678)
   를 복사.
3. 이 스크립트 실행:
       .venv\\Scripts\\python.exe scripts\\run_inbound_ticket.py 12345678
4. messages 테이블에 status=pending_approval row 가 생기고
   conversations.hubspot_ticket_id 가 채워진다.

옵션:
    positional TICKET_ID    HubSpot ticket id
    --dry-llm               LLM 호출 없이 mock 으로 (빠른 동작 확인용)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src.agents.inbound import (  # noqa: E402
    ClassifyResult,
    DraftResult,
    InboundAgent,
    ScoreAdjustResult,
)
from src.common.config import settings  # noqa: E402
from src.db.models import Message  # noqa: E402
from src.db.session import SessionLocal  # noqa: E402
from src.integrations.hubspot import HubSpotAPIError, HubSpotClient, HubSpotNotConfigured  # noqa: E402


def _print_step(n: int, title: str) -> None:
    print(f"\n[{n}] {title}")
    print("-" * (len(title) + 4))


def _build_mock_llm() -> MagicMock:
    llm = MagicMock()

    def side_effect(prompt_name, variables=None, schema=None, **kw):
        if "classify" in prompt_name:
            return ClassifyResult(category="purchase_inquiry", reasoning="dry-llm")
        if "score_adjust" in prompt_name:
            return ScoreAdjustResult(adjustment=10, reasoning="dry-llm")
        if "draft_reply" in prompt_name:
            return DraftResult(
                subject="Re: 문의 감사드립니다",
                body="(dry-llm) 답장 초안 자리.",
                language="ko",
            )
        return "ok"

    llm.complete = MagicMock(side_effect=side_effect)
    return llm


def main() -> int:
    parser = argparse.ArgumentParser(description="HubSpot ticket 기반 InboundAgent 1회 실행")
    parser.add_argument("ticket_id", help="HubSpot ticket id (예: 12345678)")
    parser.add_argument("--dry-llm", action="store_true", help="LLM 모킹 (빠른 동작 확인용)")
    args = parser.parse_args()

    if not settings.HUBSPOT_PRIVATE_APP_TOKEN:
        print("FAIL: HUBSPOT_PRIVATE_APP_TOKEN 이 .env 에 비어 있습니다.")
        return 2

    _print_step(1, "HubSpot ticket 조회")
    try:
        hubspot = HubSpotClient()
    except HubSpotNotConfigured as e:
        print(f"FAIL: {e}")
        return 2

    try:
        ticket = hubspot.get_ticket_sync(args.ticket_id)
    except HubSpotAPIError as e:
        print(f"FAIL: ticket fetch - {e}")
        return 3
    except Exception as e:
        print(f"FAIL: ticket fetch - {type(e).__name__}: {e}")
        return 3

    contact_id = hubspot.get_ticket_primary_contact_sync(args.ticket_id)
    if not contact_id:
        print("FAIL: ticket 에 연결된 Contact 가 없습니다. HubSpot 에서 contact 를 연결해 주세요.")
        return 4

    print(f"PASS: ticket={ticket.id} subject={ticket.subject!r} contact_id={contact_id}")

    _print_step(2, f"InboundAgent 실행 (LLM = {'mock' if args.dry_llm else settings.LLM_PROVIDER})")
    llm = _build_mock_llm() if args.dry_llm else None
    agent = InboundAgent(llm=llm, hubspot=hubspot)

    event = {
        "event_type": "ticket_created",
        "object_id": contact_id,
        "ticket_id": args.ticket_id,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        result = agent.handle(event)
    except Exception as e:
        print(f"FAIL: InboundAgent.handle - {type(e).__name__}: {e}")
        return 5

    if not result or not result.get("message_id"):
        print(f"FAIL/SKIP: agent returned {result}")
        return 6

    _print_step(3, "DB 결과")
    session = SessionLocal()
    try:
        msg = session.query(Message).filter_by(id=result["message_id"]).first()
        if not msg:
            print("FAIL: 저장된 메시지를 찾을 수 없음")
            return 7
        print(json.dumps(
            {
                "message_id": msg.id,
                "ticket_id": args.ticket_id,
                "category": result["category"],
                "score": result["score"],
                "channel": result["channel"],
                "status": msg.status,
                "subject": msg.subject,
                "body": msg.body,
                "language": msg.language,
            },
            ensure_ascii=False,
            indent=2,
        ))
    finally:
        session.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
