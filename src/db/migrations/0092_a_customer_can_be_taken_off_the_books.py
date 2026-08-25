"""계약이 없는 고객을 **지우지 않고 내리는** 칸을 만듭니다.

Won 에 잘못 올라갔다가 다른 단계로 옮겨진 문의가 「세팅중」 고객으로 목록과 워크북에 남아
활성 고객 수를 부풀립니다. 지우면 될 것 같지만 그러면 **Client ID 가 같이 사라집니다** —
그 번호는 문의·연락처가 들고 있고, 워크북의 계약·회차 탭과 Inbound DB 가 그 행을 조회해
회사명을 가져옵니다. 한 건이 Won 에서 물러났다고 그 연결을 끊을 이유가 없습니다.

그래서 상태를 하나 둡니다. ``clients.retired_on`` 이 차 있으면 「장부에서 내림」이고,
목록에서 숨고 활성 고객 수에서 빠집니다. 행도 번호도 그대로입니다. 되돌릴 수 있고,
계약이 들어오면 저절로 되돌아옵니다(``_add_contract`` 가 이 칸을 비웁니다).

**플랜 상태 열이 아닙니다.** 그 값은 계약 기간에서 나오는 파생값이고(이관 0067) 여기 다시
저장하면 둘이 어긋납니다. 이 칸은 「사람이 내렸다」는 사실 하나만 들고 있고, 화면에 뭐라고
보일지는 ``won.plan_status`` 가 정합니다. 워크북에는 **빈칸**이 나갑니다 — 「내림」은 콘솔
안의 말이라 시트의 「플랜 상태」 드롭다운에 없고, 없는 말을 쓰면 그 행이 영업팀의 어느
필터에도 안 걸립니다.

이미 쌓인 것도 여기서 내립니다: 계약이 하나도 없는데 수주 전환 대기가 ``dismissed`` 인
고객 — Won 을 벗어난 그 건입니다. 계약이 있는 고객은 건드리지 않습니다.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "clients" not in tables:
        return

    if "retired_on" not in {c["name"] for c in inspector.get_columns("clients")}:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE clients ADD COLUMN retired_on VARCHAR(10)"))

    if "pending_won" not in tables:
        return

    with engine.begin() as conn:
        retired = conn.execute(
            text(
                "UPDATE clients SET retired_on = :today "
                "WHERE retired_on IS NULL "
                "AND client_id NOT IN (SELECT client_id FROM client_contracts) "
                "AND client_id IN ("
                "  SELECT client_id FROM pending_won "
                "  WHERE client_id IS NOT NULL AND status = 'dismissed'"
                ")"
            ),
            {"today": date.today().isoformat()},
        ).rowcount
    logger.info("0092: 계약이 없는 수주 고객 %d건을 장부에서 내렸습니다", retired or 0)
