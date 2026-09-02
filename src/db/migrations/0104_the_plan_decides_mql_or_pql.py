"""``customer_profiles.qualification`` 을 지웁니다 (2026-09-02 운영자 지시).

MQL / PQL 은 **구독 플랜이 정하는 계산값**이지 저장하는 값이 아닙니다. 콘솔은
``sheet_values.qualification_for_plan`` 으로, 워크북은 그 목록으로 만든 Pipeline **수식**
으로 같은 답을 냅니다. 둘 다 플랜 칸 하나를 보므로 어긋날 자리가 없습니다.

**이 열은 어긋나는 것으로 그치지 않고 시트를 망가뜨렸습니다.** 채우는 곳은 워크북 전체
동기화 하나뿐이었는데, 그것이 넣는 값은 **시트 수식의 계산 결과를 글자로 베껴 온 것**
이었습니다(``sheet_sync``). 그리고 단계 동기화가 그 사본을 시트로 돌려보내
(``update_inbound_stage(pipeline=...)``) 그 행의 수식을 죽은 글자로 덮었습니다 — 그 뒤로는
구독 플랜을 아무리 고쳐도 그 행만 옛 값을 들고 있고, 수식이 없다는 것은 시트를 봐도 안
보입니다. 그 인자는 이미 없앴고, 이제 그 인자가 읽던 칸을 지웁니다.

같이 나가는 것들 — 전부 이 칸을 읽거나 쓰던 코드입니다:

- ``sheet_sync`` 의 되받아 적기 (이 칸의 유일한 writer).
- ``inbound`` · ``sheet_sync`` 가 워크북 행에 싣던 ``pipeline`` 값. **어차피 시트에 남은
  적이 없습니다**: 행을 쓴 직후 ``_write_pipeline_formula`` 가 그 칸을 수식으로 덮습니다.
  값이 없으면 채우던 대체 규칙(라이프사이클로 MQL/PQL 을 짐작하던 것)도 같이 나갑니다 —
  그건 플랜을 안 보는 세 번째 규칙이었습니다.
- ``POST /customers/{id}/profile`` 의 ``qualification`` 폼 칸. 그 값을 보내는 화면은
  없었습니다(React 포팅 때 사라진 폼의 유물).

되살릴 일이 생기면 **먼저 「어느 화면이 저장된 값을 읽는가」를 정하십시오.** 그게 없어서
이렇게 됐습니다: 아무도 안 읽는 칸이 조용히 시트의 수식을 지우고 있었습니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "customer_profiles" not in set(inspector.get_table_names()):
        logger.info("0104: customer_profiles 없음, 건너뜁니다.")
        return
    if "qualification" not in {c["name"] for c in inspector.get_columns("customer_profiles")}:
        return
    with engine.begin() as conn:
        try:
            conn.execute(text("ALTER TABLE customer_profiles DROP COLUMN qualification"))
            logger.info("0104: customer_profiles.qualification 지웠습니다.")
        except Exception:
            # 아주 오래된 SQLite 는 DROP COLUMN 이 없습니다. 개발용 파일 DB 에서만 나올 수
            # 있고, 칸이 남아 있어도 이제 아무도 안 읽습니다.
            logger.warning("0104: 열을 못 지웠습니다 (무시).", exc_info=True)
