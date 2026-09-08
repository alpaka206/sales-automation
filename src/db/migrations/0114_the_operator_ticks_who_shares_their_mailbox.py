"""``users.collect_mailbox`` — 접근 승인 화면의 체크 한 칸 (2026-09-07 운영자 지시).

「운영자가 접근 승인에서 체크해서 해당 계정의 토큰 받아오도록, **다음 로그인에** 그것도」.

**이 칸이 도메인 전체 위임을 대신합니다.** 위임 방식은 슈퍼관리자가 Admin console 에
서비스 계정 client ID 를 등록해야 하고, 그 서비스 계정은 **OAuth 클라이언트와 다른
프로젝트**에 있습니다(실측: 서비스 계정 `593919034294`, OAuth 클라이언트 `278799006078`).
그러면 Gmail API 를 켠 프로젝트와 호출하는 프로젝트가 갈려서 `SERVICE_DISABLED` 로
떨어집니다 — 원인이 엉뚱한 데로 보이기 딱 좋은 자리입니다.

이 방식은 그 둘을 다 비켜 갑니다: **콘솔에 로그인하는 그 클라이언트**가 그대로 씁니다.
관리자 결재도, 두 번째 프로젝트도 없습니다. 그리고 운영자 요구와도 맞습니다 —
「해당 사이트에 로그인은 하되 그 외적인 건 없이」.

**체크는 「다음 로그인에 한 번 더 물어본다」는 뜻입니다.** 그 사람이 평소처럼 콘솔에
로그인하면, 아직 토큰이 없을 때만 구글 동의 화면이 한 번 끼어들고 그 뒤로는 안 뜹니다.
체크를 풀면 다음부터 안 묻습니다 — 이미 받아 둔 토큰은 「메일함 연결」에서 해제합니다
(여기서 같이 지우면 체크를 실수로 눌렀다 되돌린 순간 연결이 조용히 끊깁니다).
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "users" not in set(inspector.get_table_names()):
        logger.info("0114: users 없음, 건너뜁니다.")
        return
    if "collect_mailbox" in {c["name"] for c in inspector.get_columns("users")}:
        return
    # 타입과 기본값을 **같이** 갈라야 합니다 — Postgres 는 `BOOLEAN ... DEFAULT 0` 을
    # 거부합니다. SQLite 는 불리언이 정수라 로컬에서는 통과합니다(0113 이 그것으로 운영
    # 배포에서 깨졌습니다).
    sqlite = engine.dialect.name == "sqlite"
    boolean = "INTEGER" if sqlite else "BOOLEAN"
    no = "0" if sqlite else "FALSE"
    with engine.begin() as conn:
        conn.execute(text(
            f"ALTER TABLE users ADD COLUMN collect_mailbox {boolean} NOT NULL DEFAULT {no}"
        ))
    logger.info("0114: users.collect_mailbox 를 더했습니다.")
