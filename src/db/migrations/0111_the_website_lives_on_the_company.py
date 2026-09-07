"""``contacts.website`` — 연결된 **회사**의 Website URL (2026-09-07 운영자 지시).

운영자가 허브스팟에서 본 그 칸(「Company Record」의 `Website URL`)은 **연락처의 값이
아닙니다.** 포털을 읽어 세어 봤습니다: 연락처의 `website` 속성이 채워진 것은 100건 중
**0건**, 회사의 `website` 는 100건 중 **9건**. 그래서 지금 우리가 읽어 오는 연락처 속성에
이름 하나를 더하는 것으로는 영원히 빈 칸입니다 — 연락처→회사 연결을 한 번 더 읽어야
합니다.

**그 조회를 화면이 하지 않습니다.** 이 값을 그리는 자리는 티켓 세부 내역의 카드인데,
그 카드는 0094 가 **일부러 네트워크를 끊은** 자리입니다(허브스팟이 느린 날 티켓이 늦게
열렸습니다). 그래서 저쪽이 아니라 이 열을 읽고, 채우는 것은 연락처 스윕입니다 —
`contact_sync.fill_missing_websites`.

**빈 문자열은 「물어봤고 없었다」입니다.** NULL 이 「아직 안 물어봤다」이고 그것이 곧 스윕의
대기열이라, 회사가 없거나 회사에 주소가 없는 연락처를 NULL 로 두면 그 사람들만 2분마다
영원히 다시 조회됩니다. 화면에서는 둘 다 그냥 안 보입니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "contacts" not in set(inspector.get_table_names()):
        logger.info("0111: contacts 없음, 건너뜁니다.")
        return
    if "website" in {c["name"] for c in inspector.get_columns("contacts")}:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE contacts ADD COLUMN website VARCHAR(512)"))
    logger.info("0111: contacts.website 를 더했습니다.")
