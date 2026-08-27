"""(비어 있음) ``llm_usage`` 표를 만들던 마이그레이션.

**0095 가 그 표를 지웠고 모델도 없어졌습니다** (2026-08-27). 여기서 만들어 봐야 몇 단계
뒤에 다시 지워지므로, 새 DB 에서는 아예 만들지 않습니다. 파일을 지우지 않는 이유는
마이그레이션 이름이 곧 적용 기록이기 때문입니다 — 이미 돌아간 DB 들의 ``schema_migrations``
에 이 이름이 있고, 파일이 사라지면 그 기록이 무엇을 가리키는지 알 수 없게 됩니다.

원래 하던 일: ``Base.metadata.tables["llm_usage"]`` 를 만들고 ``created_at`` 에 인덱스를
걸었습니다. 모델이 없어졌으므로 그 줄은 새 DB 에서 ``KeyError`` 였습니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    logger.info("0002: llm_usage is retired (see 0095); nothing to do.")
