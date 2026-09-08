"""``mailbox_accounts`` — 사람마다 연결한 Gmail 사서함 (2026-09-07 운영자 지시).

「`perso.ai@estsoft.com`, `untae@estsoft.com` 이런 식으로 여러 개 넣을 거고 추가될 수도
있다」가 요구사항입니다. 그래서 **한 줄에 한 사서함**입니다.

**`integration_credentials` 를 안 쓰는 이유가 둘입니다.** 그 표는 `provider` **하나가
기본키**라 provider 당 한 줄뿐이고(시트 연결이 그 한 줄을 쓰고 있습니다), 기본키를 바꾸는
이관은 SQLite 에서 표를 다시 세우는 일이라 **지금 잘 돌고 있는 시트 연결을 건드립니다.**
그리고 이쪽은 저쪽에 없는 칸이 필요합니다 — 켜고 끄기, 마지막 수집 시각, 끊긴 이유.

**주소가 기본키입니다.** 같은 사서함을 두 번 연결하면 새 토큰이 옛 줄을 덮어써야 합니다 —
줄이 둘 생기면 어느 토큰이 살아 있는지 화면만 봐서는 모르고, 구글도 계정당 클라이언트당
refresh token 을 100개까지만 살려 둡니다(넘으면 오래된 것부터 **경고 없이** 무효).

**그 주소는 사람이 적는 값이 아닙니다.** 동의가 끝나면 구글의 userinfo 가 「실제로 로그인한
계정」을 알려주고, 그 값을 그대로 씁니다. 관리자가 폼에 「이건 untae 거」라고 적는 구조였다면
라벨과 실제가 어긋날 수 있고, 그 어긋남은 남의 사서함을 읽는다는 뜻입니다.

**`collect_from` 은 「여기서부터 본다」입니다** (2026-09-07 운영자 지시: 「동의한 이후 메일을
가져오도록」). 토큰 자체에는 시점 제한이 없어서 몇 년 전 메일까지 다 읽힙니다 — 어디부터
가져올지는 구글이 아니라 **우리가** 정해야 하고, 그 값이 이 칸입니다. 동의가 끝난 순간을
찍습니다.

**토큰은 암호화해서 담습니다** — `google_oauth._encrypt` 와 같은 열쇠(`GOOGLE_TOKEN_
ENCRYPTION_KEY`)입니다. DB 를 열어 본 사람이 그 사람의 메일을 읽을 수 있으면 안 됩니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    if "mailbox_accounts" in set(inspect(engine).get_table_names()):
        return
    # `enabled` 의 기본값은 참입니다 — 방금 연결한 사서함이 꺼져 있으면 「연결했는데 아무
    # 일도 안 일어난다」가 됩니다.
    #
    # **타입과 기본값을 같이 갈라야 합니다.** 타입만 갈랐다가 배포가 깨졌습니다 —
    # Postgres 는 `BOOLEAN ... DEFAULT 1` 을 거부합니다(`column "enabled" is of type
    # boolean but default expression is of type integer`). SQLite 는 불리언이 정수라
    # 로컬에서는 통과했고, 그래서 **운영 첫 배포에서야 드러났습니다.**
    sqlite = engine.dialect.name == "sqlite"
    boolean = "INTEGER" if sqlite else "BOOLEAN"
    yes = "1" if sqlite else "TRUE"
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE mailbox_accounts (
                email VARCHAR(320) PRIMARY KEY,
                encrypted_payload TEXT NOT NULL,
                enabled {boolean} NOT NULL DEFAULT {yes},
                connected_by VARCHAR(320),
                last_error TEXT,
                collect_from TIMESTAMP,
                last_polled_at TIMESTAMP,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
        """))
    logger.info("0113: mailbox_accounts 를 만들었습니다.")
