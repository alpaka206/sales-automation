"""``policy_sections`` — 콘솔 문서를 나눈 구간과 그 꼬리표 (2026-10-06).

**왜 생겼나.** 운영의 정책 문서 일곱 편이 전부 「항상 적용」이라 초안마다 39~46천 자가
「Company rules (must follow)」 한 덩어리로 실렸습니다 — 템플릿도, 완성 예시도, 무응답
리마인드 골격도, 원가·이익률이 든 내부 단가표도 「반드시 따를 규칙」으로요(2026-10-06 평가).
운영자가 문서를 어떻게 쓰든 시스템이 스스로 나누고 종류별로 묶습니다(상황으로 거르지는 않습니다 —
기밀 구간만 빠지고, 문서가 한 회신으로 한정한 구간에는 「…에만 적용」 표시가 붙습니다).
나누는 것은 코드(``llm/organizer.split_sections``), 꼬리표는 flash 한 번입니다.

**표를 따로 두는 이유.** ``policy_sources`` 에 칸을 더하면 그 칸이 초안 지문
(``PolicyDocument``)에 들어가서, 정리가 끝날 때마다 대기 중인 초안이 전부 「정책이
변경되었습니다」로 막힙니다. 지도는 본문 해시와 정리기 판(``organizer_version``)에 묶여
따로 삽니다 — 본문이 바뀌면 낡은 지도가 되고, 그동안 그 문서는 예전처럼 통째로 갑니다.

**이 이관은 표만 만듭니다 — 모델을 부르지 않습니다.** 이관은 Render 빌드 중에 옛 코드가
아직 돌고 있을 때 돌아서, 여기서 모델을 부르면 배포 성공이 Vertex 사정에 묶입니다. 채우는
것은 10분 폴러(``organize_pending``, 회차마다 한 편)와 콘솔에서 저장한 직후의 정리
(``policy_docs.schedule_reorganize``)입니다.

**값은 안 옮깁니다.** 새 표이고, 채워지기 전에는 모든 문서가 예전처럼 본문 통째로 갑니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    # 새 DB 는 0001 의 create_all 이 지금 모델로 이미 만들었습니다.
    if "policy_sections" in set(inspect(engine).get_table_names()):
        return
    sqlite = engine.dialect.name == "sqlite"
    serial = "INTEGER PRIMARY KEY AUTOINCREMENT" if sqlite else "SERIAL PRIMARY KEY"
    # SQLite 에는 JSON 타입이 없고 TEXT 로 삽니다 — SQLAlchemy 의 JSON 이 양쪽을 같은 값으로 돌려줍니다.
    json_type = "TEXT" if sqlite else "JSON"
    # BOOLEAN 의 거짓 리터럴만 엔진마다 다릅니다(0003 · 0071 과 같은 처리).
    false_literal = "0" if sqlite else "false"
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE policy_sections (
                id {serial},
                source_kind VARCHAR(16) NOT NULL,
                source_id INTEGER NOT NULL,
                body_sha256 VARCHAR(64) NOT NULL,
                organizer_version INTEGER NOT NULL,
                idx INTEGER NOT NULL,
                start_offset INTEGER NOT NULL,
                end_offset INTEGER NOT NULL,
                heading TEXT,
                kind VARCHAR(32) NOT NULL,
                applies_to {json_type},
                topics {json_type},
                confidential BOOLEAN NOT NULL DEFAULT {false_literal},
                terms {json_type},
                created_at TIMESTAMP NOT NULL
            )
        """))
        conn.execute(text(
            "CREATE UNIQUE INDEX ux_policy_sections_source_idx "
            "ON policy_sections (source_kind, source_id, idx)"
        ))
    logger.info("0130: policy_sections 를 만들었습니다.")
