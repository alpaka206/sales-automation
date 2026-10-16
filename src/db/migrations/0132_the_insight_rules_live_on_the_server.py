"""``insight_rules`` · ``insight_log`` — 영업 인사이트의 판정 기준과 그 기록 (2026-10-15).

**왜 생겼나.** 영업 인사이트를 목업(업셀링 · 이탈 방지 · 추가 계약 기회)대로 바꾸면서, 판정 기준
(업셀링 30일 크레딧 · 잔여 예상 % · 무활동 일 · 계약 종료 일 · 부족 예상 % · 초과사용 %p)을
**서버에 두고 화면에서 고치고, 누가 언제 바꿨는지 남기라**는 지시가 왔습니다. 그중 잔여 예상 ·
부족 예상 · 초과사용은 수주 고객 「사용 진단」과 같은 값이라, 코드에 박힌 상수(``won/usage.ts``)가
아니라 여기 한 곳에서 두 화면이 같이 읽어야 합니다.

**설정값을 담을 표가 지금 없어서 새로 만듭니다.** 이메일 템플릿 표에 넣으면 「정책 · 템플릿」
화면에 섞이고, Gemini 의 문서 정리기가 그 표를 읽습니다.

- ``insight_rules`` — 사람이 고친 기준만 행이 있습니다. 행이 없으면 기본값(``db/insights.RULES``)
  입니다. 그래서 **값을 옮기지 않고 씨앗도 안 심습니다** — 지금 코드의 값이 그대로 기본값입니다.
- ``insight_log`` — 덧붙이기만 하는 기록. 지금은 기준 변경이 적히고, 상태 · 메모 · 메일 초안 복사가
  뒤따릅니다.

외부 공개(Supabase REST) 차단은 이관이 아니라 ``run_migrations()`` 끝의 RLS 가 새 표에도 겁니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    # 새 DB 는 0001 의 create_all 이 지금 모델로 이미 만들었습니다.
    existing = set(inspect(engine).get_table_names())
    sqlite = engine.dialect.name == "sqlite"
    serial = "INTEGER PRIMARY KEY AUTOINCREMENT" if sqlite else "SERIAL PRIMARY KEY"
    made: list[str] = []
    with engine.begin() as conn:
        if "insight_rules" not in existing:
            conn.execute(text("""
                CREATE TABLE insight_rules (
                    key VARCHAR(48) PRIMARY KEY,
                    value INTEGER NOT NULL,
                    updated_by VARCHAR(255),
                    updated_at TIMESTAMP NOT NULL
                )
            """))
            made.append("insight_rules")
        if "insight_log" not in existing:
            conn.execute(text(f"""
                CREATE TABLE insight_log (
                    id {serial},
                    tab VARCHAR(16) NOT NULL,
                    space_seq INTEGER,
                    contract_id INTEGER,
                    action VARCHAR(16) NOT NULL,
                    detail TEXT,
                    actor VARCHAR(255),
                    created_at TIMESTAMP NOT NULL
                )
            """))
            conn.execute(text("CREATE INDEX ix_insight_log_space_seq ON insight_log (space_seq)"))
            conn.execute(text("CREATE INDEX ix_insight_log_contract_id ON insight_log (contract_id)"))
            made.append("insight_log")
    if made:
        logger.info("0132: %s 를 만들었습니다.", " · ".join(made))
