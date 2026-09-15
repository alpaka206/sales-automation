"""``contract_payments.note`` — 분납 회차의 비고 (2026-09-15).

**왜 생겼나.** 스냅샷의 국내 카드 결제 기록으로 분납 회차를 **자동으로** 완료 처리하기
시작했다(운영자 결정: 「결제가 된 게 들어왔으면 바뀌는 건 상관없지, 완전 자동으로」,
`frontend/src/screens/won/reconcile.ts`). 지급 회차는 ``granted_by='스냅샷 자동'`` 과 메모로
「누가 채웠나」가 남는데, 결제 회차에는 적을 칸이 없었다 — 자동으로 닫힌 회차와 사람이
입금을 확인하고 닫은 회차가 화면에서 같아 보였다. 「스냅샷이 채웠다」를 비고에 적어 달라는
것이 운영자 요청이다.

**칸 하나로 둘 다 한다.** 자동 대조가 「스냅샷 결제 확인 <날짜> (스냅샷 <날짜>)」를 적고,
사람도 같은 칸에 적는다. 자동이 적을 때 사람이 적어 둔 글은 지우지 않고 앞에 붙인다.

**이 이관은 값을 안 옮긴다.** 이미 완료된 회차가 누구 손으로 닫혔는지는 알 수 없다 — 빈 채로
둔다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "contract_payments" not in set(inspector.get_table_names()):
        logger.info("0120: contract_payments 없음, 건너뜁니다.")
        return
    if "note" in {c["name"] for c in inspector.get_columns("contract_payments")}:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE contract_payments ADD COLUMN note VARCHAR(255)"))
    logger.info("0120: contract_payments.note 를 만들었습니다.")
