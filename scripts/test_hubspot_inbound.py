"""
HubSpot 실연결 end-to-end 테스트.

이 스크립트는 다음을 순서대로 수행합니다:

1. `.env`의 `HUBSPOT_PRIVATE_APP_TOKEN` 으로 contacts API 호출이 되는지 확인.
2. HubSpot 테스트 계정에 임시 contact 생성 (이메일/이름/회사/국가 지정 가능).
3. 그 contact ID로 인바운드 웹훅 페이로드를 만들어 InboundAgent에 직접 주입.
4. DB에 저장된 message(`status=pending_approval`)와 카테고리·점수·초안 일부를 출력.
5. 옵션으로 생성한 테스트 contact를 삭제.

실행 예:

    .venv\\Scripts\\python.exe scripts\\test_hubspot_inbound.py
    .venv\\Scripts\\python.exe scripts\\test_hubspot_inbound.py --keep
    .venv\\Scripts\\python.exe scripts\\test_hubspot_inbound.py --message "월 사용료가 얼마인가요?"

스크립트는 LLM(Gemini on Vertex AI)을 실제로 호출하므로 LLM 설정이
완료되어 있어야 합니다. 호출이 부담스러우면 `--dry-llm` 으로 모킹할 수 있습니다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

# Force UTF-8 stdout/stderr so Korean messages render on Windows cp949 consoles.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Make `src` importable regardless of where the script is run from
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import httpx  # noqa: E402

from src.agents.inbound import (  # noqa: E402
    ClassifyResult,
    DraftResult,
    InboundAgent,
    ScoreAdjustResult,
)
from src.common.config import settings  # noqa: E402
from src.db.models import Message  # noqa: E402
from src.db.session import SessionLocal  # noqa: E402
from src.integrations.hubspot import (  # noqa: E402
    BASE_URL,
    HubSpotClient,
    HubSpotNotConfigured,
)


def _print_step(n: int, title: str) -> None:
    print(f"\n[{n}] {title}")
    print("-" * (len(title) + 4))


def _check_token() -> str:
    token = settings.HUBSPOT_PRIVATE_APP_TOKEN
    if not token:
        print("FAIL: HUBSPOT_PRIVATE_APP_TOKEN 이 .env 에 비어 있습니다.")
        print("      docs/배포.md 의 'HubSpot 개발자 계정 만들기' 섹션을 따라 토큰을 발급해주세요.")
        sys.exit(2)

    with httpx.Client(headers={"Authorization": f"Bearer {token}"}, timeout=10.0) as c:
        r = c.get(f"{BASE_URL}/crm/v3/objects/contacts", params={"limit": 1})
    if r.status_code == 401:
        print("FAIL: 401 Unauthorized - 토큰이 만료되었거나 잘못되었습니다.")
        sys.exit(2)
    if r.status_code == 403:
        print("FAIL: 403 Forbidden - Private App 의 scope 에 'crm.objects.contacts.read/write' 가 빠졌습니다.")
        print(f"      응답: {r.text[:300]}")
        sys.exit(2)
    if r.status_code >= 400:
        print(f"FAIL: contacts API 호출 실패 ({r.status_code}) - {r.text[:300]}")
        sys.exit(2)

    print(f"PASS: 토큰 유효, contacts API 응답 {r.status_code}")
    return token


def _create_test_contact(token: str, email: str, args: argparse.Namespace) -> str:
    payload = {
        "properties": {
            "email": email,
            "firstname": args.firstname,
            "lastname": args.lastname,
            "company": args.company,
            "country": args.country,
            "lifecyclestage": "lead",
        }
    }
    with httpx.Client(headers={"Authorization": f"Bearer {token}"}, timeout=15.0) as c:
        r = c.post(f"{BASE_URL}/crm/v3/objects/contacts", json=payload)
    if r.status_code == 409:
        # already exists - fetch by email
        with httpx.Client(headers={"Authorization": f"Bearer {token}"}, timeout=15.0) as c:
            r = c.get(
                f"{BASE_URL}/crm/v3/objects/contacts/{email}",
                params={"idProperty": "email"},
            )
            r.raise_for_status()
            contact_id = str(r.json()["id"])
            print(f"NOTE: contact 가 이미 존재 - 기존 ID 사용: {contact_id}")
            return contact_id
    r.raise_for_status()
    contact_id = str(r.json()["id"])
    print(f"PASS: 테스트 contact 생성됨 - id={contact_id}, email={email}")
    return contact_id


def _delete_contact(token: str, contact_id: str) -> None:
    with httpx.Client(headers={"Authorization": f"Bearer {token}"}, timeout=15.0) as c:
        r = c.delete(f"{BASE_URL}/crm/v3/objects/contacts/{contact_id}")
    if r.status_code in (200, 204):
        print(f"PASS: 테스트 contact 삭제됨 (id={contact_id})")
    else:
        print(f"WARN: contact 삭제 실패 ({r.status_code}) - 수동 정리 필요: {r.text[:200]}")


def _build_mock_llm() -> MagicMock:
    llm = MagicMock()

    def side_effect(prompt_name: str, variables: dict | None = None, schema=None, **kw):
        if "classify" in prompt_name:
            msg = (variables or {}).get("last_message", "")
            cat = "pricing_question" if any(k in msg for k in ("가격", "요금", "price", "cost")) else "purchase_inquiry"
            return ClassifyResult(category=cat, reasoning="dry-llm stub")
        if "score_adjust" in prompt_name:
            return ScoreAdjustResult(adjustment=10, reasoning="dry-llm stub")
        if "draft_reply" in prompt_name:
            return DraftResult(
                subject="Re: 문의 감사드립니다",
                body="안녕하세요, 문의 주신 내용 잘 받았습니다. 짧은 미팅으로 자세히 안내드리겠습니다.",
                language="ko",
                tone_notes="dry-llm stub",
            )
        return "ok"

    llm.complete = MagicMock(side_effect=side_effect)
    return llm


def main() -> int:
    parser = argparse.ArgumentParser(description="HubSpot 인바운드 end-to-end 테스트")
    parser.add_argument(
        "--message",
        default="안녕하세요, 솔루션 도입을 검토 중입니다. 간단한 데모를 받아볼 수 있을까요?",
        help="시뮬레이션할 inbound 메시지 본문",
    )
    parser.add_argument("--firstname", default="Test")
    parser.add_argument("--lastname", default="User-AutoCreated")
    parser.add_argument("--company", default="Sales Automation QA")
    parser.add_argument("--country", default="korea")
    parser.add_argument(
        "--email",
        default=None,
        help="contact 이메일. 생략하면 sales-test+<timestamp>@example.com 사용",
    )
    parser.add_argument("--keep", action="store_true", help="테스트 contact 를 삭제하지 않고 유지")
    parser.add_argument(
        "--dry-llm",
        action="store_true",
        help="LLM 을 실제로 호출하지 않고 mock 으로 대체 (토큰 검증 + DB 플로우만 확인)",
    )
    args = parser.parse_args()

    email = args.email or f"sales-test+{int(time.time())}@example.com"

    _print_step(1, "HubSpot 토큰 검증")
    token = _check_token()

    _print_step(2, "테스트 contact 생성")
    contact_id = _create_test_contact(token, email, args)

    _print_step(3, f"InboundAgent 실행 (LLM = {'mock' if args.dry_llm else settings.LLM_PROVIDER})")
    llm = _build_mock_llm() if args.dry_llm else None
    try:
        hubspot = HubSpotClient(token=token)
    except HubSpotNotConfigured:
        hubspot = None

    agent = InboundAgent(llm=llm, hubspot=hubspot)
    event: dict[str, Any] = {
        "event_type": "form.submission",
        "object_id": contact_id,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "email": email,
        "full_name": f"{args.firstname} {args.lastname}".strip(),
        "company": args.company,
        "country": args.country,
        "last_message": args.message,
    }
    try:
        result = agent.handle(event)
    except Exception as e:
        print(f"FAIL: InboundAgent.handle 실패 - {type(e).__name__}: {e}")
        if not args.keep:
            _delete_contact(token, contact_id)
        return 3

    if not result:
        print("FAIL: agent 가 결과를 반환하지 않음 (dedup 이거나 내부 오류)")
        return 4

    _print_step(4, "DB 결과")
    session = SessionLocal()
    try:
        msg = session.query(Message).filter_by(id=result["message_id"]).first()
        if not msg:
            print("FAIL: 저장된 메시지를 찾을 수 없음")
            return 5
        print(json.dumps(
            {
                "message_id": msg.id,
                "category": result["category"],
                "score": result["score"],
                "channel": result["channel"],
                "status": msg.status,
                "subject": msg.subject,
                "body_preview": (msg.body or "")[:300],
                "language": msg.language,
            },
            ensure_ascii=False,
            indent=2,
        ))
    finally:
        session.close()

    if args.keep:
        print(f"\nNOTE: --keep 옵션으로 인해 contact (id={contact_id}, email={email}) 유지함.")
    else:
        _print_step(5, "테스트 contact 정리")
        _delete_contact(token, contact_id)

    print("\n[DONE] 인바운드 end-to-end 테스트 통과.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
