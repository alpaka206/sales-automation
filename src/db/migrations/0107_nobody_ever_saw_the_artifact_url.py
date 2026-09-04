"""``customer_interactions.artifact_url`` 을 지웁니다 (2026-09-03 운영자 지시).

「관련 자료 URL」 칸입니다. **그리는 화면이 한 번도 없었습니다** — 채우는 곳은 히스토리
추가 폼 하나뿐이었고(그 칸은 같은 날 폼에서 빠졌습니다), 읽는 코드는 payload 에 실어
보내는 세 줄이 전부입니다(``customer_ops`` · ``messages`` · ``ui_api``). 브라우저까지
갔다가 아무도 안 그리고 버려졌습니다. 그 세 줄도 같이 나갑니다.

**같이 지우지 않는 두 칸이 있고, 이유가 다릅니다.**

``subject`` 는 폼에서는 빠졌지만 **자동으로 채워지고 화면에 뜹니다.** 채우는 곳이 다섯
군데입니다 — 허브스팟 메일 제목(``customer_ops``), 딜 이름, 「HubSpot 메모」, 지난 티켓에서
옮겨 담은 메일(``hubspot_reconcile``). 리드 히스토리에서는 굵은 제목을 끄기로 했지만
(2026-09-03 운영자 지시, ``hideSubject``) 그것으로 죽은 칸이 되지는 않습니다 — **요약이 빈
기록에서는 제목이 본문 자리를 대신하고**(``InteractionForm.tsx``), 수주 고객 상세의 타임라인이
그대로 찍으며(``WonCustomerDetail.tsx``), ``summaries.one_line`` 이 요약을 만들 때 읽습니다.

``context`` 도 같습니다. 한 줄 요약이고 세 곳이 자동으로 채웁니다 — 메일을 가져올 때
``_one_line`` 이 만든 요약, 지난 티켓의 고객 요청사항, 그리고 빈 행을 나중에 채우는
백필(``POST /contacts/history-digest``). 목록의 미리보기와 티켓 요약(``summaries``)이 읽습니다.

즉 **폼에서 빠진 것과 열이 죽은 것은 다른 이야기입니다.** 사람이 안 적을 뿐 코드가 계속
채우고 화면이 계속 읽는 칸이 둘, 아무도 안 채우고 아무도 안 읽던 칸이 하나였습니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "customer_interactions" not in set(inspector.get_table_names()):
        logger.info("0107: customer_interactions 없음, 건너뜁니다.")
        return
    columns = {c["name"] for c in inspector.get_columns("customer_interactions")}
    if "artifact_url" not in columns:
        return
    with engine.begin() as conn:
        try:
            conn.execute(text("ALTER TABLE customer_interactions DROP COLUMN artifact_url"))
            logger.info("0107: customer_interactions.artifact_url 지웠습니다.")
        except Exception:
            # 아주 오래된 SQLite 는 DROP COLUMN 이 없습니다. 개발용 파일 DB 에서만 나올 수
            # 있고, 칸이 남아 있어도 이제 아무도 안 읽습니다.
            logger.warning("0107: 열을 못 지웠습니다 (무시).", exc_info=True)
