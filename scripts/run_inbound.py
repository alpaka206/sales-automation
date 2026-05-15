"""
실제 운영용 인바운드 처리 스크립트.

이미 HubSpot 에 존재하는 contact 와 그 사람이 보낸 메시지를 받아서 InboundAgent
를 한 번 돌립니다. test_hubspot_inbound.py 와 달리 contact 를 만들거나 지우지
않습니다.

사용 시나리오 (수동 운영 모드):
1. 고객이 메일/폼/카톡 등 어떤 채널로 문의를 보냄
2. HubSpot UI 에서 그 사람을 contact 로 생성 (또는 이미 있으면 그 contact 사용)
3. 이 스크립트 실행:
       .venv\\Scripts\\python.exe scripts\\run_inbound.py 486411754228 --message "고객 메시지 전체"
4. Supabase Table Editor 에서 messages 테이블의 새 row(status=pending_approval) 확인
5. 초안 검토 후 직접 HubSpot 에서 답장

contact ID 는 HubSpot UI 에서 contact 페이지 URL 의 마지막 숫자입니다
(예: app.hubspot.com/contacts/246205524/record/0-1/486411754228 → 486411754228).
또는 이메일로도 가능: --by-email customer@example.com (이 경우 ID 인자 생략).

옵션:
    positional CONTACT_ID    HubSpot contact id (--by-email 사용 시 생략)
    --message TEXT           고객이 보낸 inbound 메시지 본문 (필수)
    --by-email EMAIL         contact id 대신 이메일로 조회
    --dry-llm                LLM 호출 없이 mock 으로 (빠른 동작 확인용)
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
    parser = argparse.ArgumentParser(
        description="기존 HubSpot contact 에 대해 인바운드 에이전트 1회 실행",
    )
    parser.add_argument(
        "contact_id",
        nargs="?",
        help="HubSpot contact id (예: 486411754228). --by-email 사용 시 생략.",
    )
    parser.add_argument("--message", required=True, help="고객이 보낸 inbound 메시지 본문")
    parser.add_argument("--by-email", default=None, help="contact id 대신 이메일로 조회")
    parser.add_argument("--dry-llm", action="store_true", help="LLM 모킹 (빠른 동작 확인용)")
    args = parser.parse_args()

    if not args.contact_id and not args.by_email:
        parser.error("contact_id 또는 --by-email 중 하나는 필수입니다.")

    if not settings.HUBSPOT_PRIVATE_APP_TOKEN:
        print("FAIL: HUBSPOT_PRIVATE_APP_TOKEN 이 .env 에 비어 있습니다.")
        return 2

    _print_step(1, "HubSpot contact 조회")
    try:
        hubspot = HubSpotClient()
    except HubSpotNotConfigured as e:
        print(f"FAIL: {e}")
        return 2

    lookup_key = args.by_email if args.by_email else args.contact_id
    try:
        contact = hubspot.get_contact_sync(lookup_key)
    except HubSpotAPIError as e:
        print(f"FAIL: {e}")
        return 3

    full_name = " ".join(filter(None, [contact.firstname, contact.lastname])) or "(이름 없음)"
    print(f"PASS: contact 발견 - id={contact.id}, name={full_name}, email={contact.email}")

    _print_step(2, f"InboundAgent 실행 (LLM = {'mock' if args.dry_llm else settings.LLM_PROVIDER})")
    llm = _build_mock_llm() if args.dry_llm else None
    agent = InboundAgent(llm=llm, hubspot=hubspot)

    event = {
        "event_type": "manual.trigger",
        "object_id": contact.id,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "email": contact.email or "",
        "full_name": full_name,
        "company": contact.company or "",
        "country": contact.country or "",
        "lifecycle_stage": contact.lifecyclestage or "",
        "last_message": args.message,
    }
    try:
        result = agent.handle(event)
    except Exception as e:
        print(f"FAIL: InboundAgent.handle 실패 - {type(e).__name__}: {e}")
        return 4

    if not result:
        print("FAIL: agent 가 결과 없음 (dedup 또는 내부 오류)")
        return 5

    _print_step(3, "DB 결과")
    session = SessionLocal()
    try:
        msg = session.query(Message).filter_by(id=result["message_id"]).first()
        if not msg:
            print("FAIL: 저장된 메시지를 찾을 수 없음")
            return 6
        print(json.dumps(
            {
                "message_id": msg.id,
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

    print("\n다음 행동:")
    print("- Supabase Table Editor 에서 messages 테이블 확인")
    print("- 초안이 마음에 들면 HubSpot 에서 직접 답장 발송")
    print("- 수정이 필요하면 위 본문을 참고해서 손봐서 보내거나, 같은 명령에 다른 --message 로 재시도")
    return 0


if __name__ == "__main__":
    sys.exit(main())
