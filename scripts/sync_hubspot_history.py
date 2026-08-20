"""허브스팟에 있는 그 사람의 문의·답변을 우리 히스토리로 끌어옵니다 — 한 명씩, 쉬어 가며.

`/internal/customers/hubspot-history` 와 **같은 함수**(`_sync_hubspot`)를 부릅니다. 다른
것은 부르는 방식뿐입니다: 라우트는 HTTP 한 번에 몇 명이라 오래 걸리면 요청이 끊기고, 이건
콘솔에서 떨어져 돌기 때문에 몇백 명을 한 번에 끝낼 수 있습니다.

**끌어오기만 합니다 — 허브스팟에도 시트에도 아무것도 쓰지 않습니다.** `_sync_hubspot` 은
끝에 단계 반영(`_sync_ticket_stages`)을 부르는데, 그것이 워크북에 씁니다. 운영 DB 로
돌 때는 맞는 동작이지만 **로컬 스냅샷으로 돌면 남의 시트에 쓰는 셈**이라(2026-08-20 실측:
「Inbound DB Client ID 1340 was not found」) 이 스크립트는 그 길을 스스로 막습니다.
단계는 어차피 10분 폴러가 맞춥니다.

    .venv\Scripts\python.exe -m scripts.sync_hubspot_history [--all] [--sleep 1.5]

기본값은 **Concluded 만 남은 사람을 건너뜁니다**(운영자 지시). `--all` 은 전부 훑습니다.
같은 것을 두 번 넣지 않으므로(모든 행이 `external_id` 로 조회됩니다) 몇 번을 돌려도,
중간에 끊고 다시 돌려도 안전합니다.
"""

from __future__ import annotations

import argparse
import time

from sqlalchemy import select

import os

# 이 스크립트는 **가져오기만** 합니다. 아래 import 보다 먼저 꺼야 합니다 — 설정은
# 모듈을 읽을 때 한 번 굳습니다.
os.environ["LIVE_EXTERNAL_WRITES"] = "false"

from src.common.tls import use_os_trust_store

# 사내망은 TLS 를 가로챕니다. 이 한 줄이 없으면 허브스팟 호출이 전부
# CERTIFICATE_VERIFY_FAILED 로 떨어집니다 — 검증을 끄는 것이 아니라 브라우저가 읽는 것과
# 같은 저장소를 읽게 하는 것입니다.
use_os_trust_store()

from src.api.routes.customer_ops import _sync_hubspot  # noqa: E402
from src.db.models import Contact, Conversation, CustomerProfile  # noqa: E402
from src.db.session import SessionLocal  # noqa: E402


def _targets(include_closed: bool, redo: bool) -> list[int]:
    with SessionLocal() as session:
        stmt = (
            select(Contact.id)
            .outerjoin(CustomerProfile, CustomerProfile.contact_id == Contact.id)
            .where(Contact.hubspot_contact_id.isnot(None), Contact.hubspot_contact_id != "")
            .order_by(
                CustomerProfile.last_synced_at.is_(None).desc(),
                CustomerProfile.last_synced_at.asc(),
                Contact.id.asc(),
            )
        )
        if not include_closed:
            stmt = stmt.where(
                Contact.id.in_(
                    select(Conversation.contact_id)
                    .where(Conversation.stage != "closed")
                    .distinct()
                    .scalar_subquery()
                )
            )
        if not redo:
            stmt = stmt.where(CustomerProfile.last_synced_at.is_(None))
        return list(session.scalars(stmt).all())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="Concluded 만 남은 사람도 포함")
    ap.add_argument("--redo", action="store_true", help="이미 동기화한 사람도 다시")
    ap.add_argument("--per-type", type=int, default=100, help="종류마다 몇 개까지 훑을지")
    # 통신에 제약이 있을 수 있어 사람마다 쉬어 갑니다(운영자 지시). 한 명이 이미 왕복
    # 여러 번이라 여기서 더 몰아붙일 이유가 없습니다.
    ap.add_argument("--sleep", type=float, default=1.5, help="연락처 사이 대기(초)")
    args = ap.parse_args()

    targets = _targets(args.all, args.redo)
    print(f"대상 {len(targets)}명")
    inserted = total_failed = 0
    for n, contact_id in enumerate(targets, 1):
        try:
            got = _sync_hubspot(contact_id, args.per_type)
            inserted += got
            print(f"[{n}/{len(targets)}] contact {contact_id}: +{got}", flush=True)
        except Exception as exc:  # 지워진 연락처·권한 없는 레코드는 흔합니다.
            total_failed += 1
            print(f"[{n}/{len(targets)}] contact {contact_id}: 실패 {type(exc).__name__}: {exc}",
                  flush=True)
        if n < len(targets):
            time.sleep(args.sleep)
    print(f"끝. 기록 {inserted}건 추가, 실패 {total_failed}명")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
