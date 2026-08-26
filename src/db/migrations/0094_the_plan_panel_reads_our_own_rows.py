"""플랜 패널이 읽을 자리를 우리 쪽에 만든다.

티켓 세부 내역의 「플랜 정보」는 지금까지 **티켓을 열 때마다 허브스팟 연락처를 한 번씩
읽어** 그렸다. 그래서 화면 값이 언제나 지금 허브스팟 값이었지만, 답을 읽는 일이 매번 외부
왕복을 기다렸고 허브스팟이 느린 날에는 그 패널 때문에 티켓이 늦게 열렸다. 운영자 지시
(2026-08-26): **화면은 우리 DB 를 보고, 저쪽이 바뀌면 그때 이쪽으로 들어오게 한다.**

들어오는 문은 이미 셋이다(`agents/contact_sync`): 웹훅 · 10분 스윕 · 고객 상세의 수동
동기화. 이 이관은 그 셋이 채울 **자리**만 만든다.

**전에는 「없는 칸을 만들지 않는다」였다.** 그 규칙의 이유는 「읽는 화면이 없는 열은 다음
사람에게 '왜 비어 있지'만 남긴다」였고, 이제 읽는 화면이 생겨서 이유가 사라졌다. 다섯 칸이
한 카드에 서는데 둘만 우리 DB 에 있고 셋은 없으면, 그 카드는 두 곳에서 값을 모아야 한다.

``contacts.ip_country`` 가 같이 오는 이유: 그 패널의 「국가」 줄이다. ``contacts.country`` 와
다른 값이다 — 저쪽은 사람이 폼에 적은 값이고(대개 비어 있다) 이쪽은 허브스팟이 접속 IP 로
뽑은 값이라, 워크북의 IP Country 열이 뜻하는 것은 이쪽이다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_PROFILE_COLUMNS = {
    "plan_tier": "VARCHAR(64)",
    "space_seq": "VARCHAR(128)",
    "plan_seq": "VARCHAR(128)",
}


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    added: list[str] = []

    with engine.begin() as conn:
        if "customer_profiles" in tables:
            existing = {column["name"] for column in inspector.get_columns("customer_profiles")}
            for name, sql_type in _PROFILE_COLUMNS.items():
                if name not in existing:
                    conn.execute(
                        text(f"ALTER TABLE customer_profiles ADD COLUMN {name} {sql_type}")
                    )
                    added.append(f"customer_profiles.{name}")
        if "contacts" in tables:
            existing = {column["name"] for column in inspector.get_columns("contacts")}
            if "ip_country" not in existing:
                conn.execute(text("ALTER TABLE contacts ADD COLUMN ip_country VARCHAR(64)"))
                added.append("contacts.ip_country")

    if added:
        logger.info("0094: added %s", ", ".join(added))
    else:
        logger.info("0094: nothing to add.")
