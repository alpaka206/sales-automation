"""중지된 정책 문서를 되살립니다 — 중지 기능이 없어졌기 때문입니다.

``중지`` 는 노션이 원본이던 시절의 기능이었습니다: 등록과 동기화된 사본은 남기고 답변에만
쓰지 않는 상태. 이 콘솔이 원본이 된 지금은 안 쓸 문서를 남겨 둘 이유가 없고 — 안 쓰면
지웁니다 — 버튼도 없앴습니다.

버튼만 없애면 이미 중지된 행이 **되살릴 방법 없이** 남습니다. 화면에는 다른 문서와 똑같이
보이는데 초안은 읽지 않는, 눈으로는 구분할 수 없는 상태입니다. 그래서 전부 active 로
되돌립니다. 정말 안 쓸 문서라면 이제 지우면 됩니다.

``status`` 열 자체는 남깁니다: 읽는 쪽(``_rules_from_db``, ``_upsert_knowledge``)이 여전히
active 를 확인하고, 그 확인은 이 열이 다시 쓰일 일이 생겨도 그대로 맞습니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    if "policy_sources" not in set(inspect(engine).get_table_names()):
        logger.info("0054: policy_sources missing; skipping.")
        return
    with engine.begin() as conn:
        revived = conn.execute(
            text("UPDATE policy_sources SET status = 'active' WHERE status != 'active'")
        ).rowcount
    if revived:
        logger.info("0054: revived %d paused policy document(s).", revived)
