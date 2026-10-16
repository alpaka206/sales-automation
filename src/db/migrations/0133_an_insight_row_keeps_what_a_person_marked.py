"""``insight_marks`` — 영업 인사이트 한 줄의 상태 · 메모 (2026-10-15).

영업 인사이트의 세 카드(업셀링 · 이탈 방지 · 추가 계약 기회)마다 줄을 「미컨택 → 컨택 완료 → 전환 / 제외」처럼
옮기고 메모를 남깁니다. 팀이 같은 상태를 봐야 같은 곳에 두 번 연락하지 않으므로 서버에 둡니다 — 각자 브라우저에
두면 팀원끼리 안 보입니다.

**업셀링 줄의 번호(space_seq)가 서버에 남는 것은 「사용 데이터는 서버로 안 간다」(2026-09-15)의 세 번째 예외입니다.**
2026-10-15 운영자가 개발자에게 물어 정했습니다(A안): 사람이 상태 버튼 · 메모 저장을 누를 때만, 번호 · 상태 · 메모 ·
누가 언제만 남고, 크레딧 같은 사용량 숫자는 칸이 없습니다.

행이 없으면 「미컨택 · 미조치」입니다 — 씨앗도 옮길 값도 없습니다. 바꾼 기록은 0132 의 ``insight_log`` 에 남습니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    # 새 DB 는 0001 의 create_all 이 지금 모델로 이미 만들었습니다.
    if "insight_marks" in set(inspect(engine).get_table_names()):
        return
    serial = "INTEGER PRIMARY KEY AUTOINCREMENT" if engine.dialect.name == "sqlite" else "SERIAL PRIMARY KEY"
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE insight_marks (
                id {serial},
                tab VARCHAR(16) NOT NULL,
                space_seq INTEGER,
                contract_id INTEGER,
                status VARCHAR(16) NOT NULL DEFAULT 'todo',
                memo TEXT,
                updated_by VARCHAR(255),
                updated_at TIMESTAMP NOT NULL
            )
        """))
        conn.execute(text("CREATE UNIQUE INDEX ux_insight_marks_space ON insight_marks (tab, space_seq)"))
        conn.execute(text("CREATE UNIQUE INDEX ux_insight_marks_contract ON insight_marks (tab, contract_id)"))
    logger.info("0133: insight_marks 를 만들었습니다.")
