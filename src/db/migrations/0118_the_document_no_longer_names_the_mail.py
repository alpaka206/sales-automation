"""``policy_sources.subject`` 를 지웁니다 (2026-09-10 운영자 지시).

「메일 제목은 아예 db 자체에도 없어도 될 것 같고」가 그 지시입니다.

**그 칸이 하던 일**: 이 문서를 근거로 회신할 때의 고정 제목(견적·소개처럼 제목이 정해진
회신). `knowledge.subject_from_docs` 가 읽어 `draft.subject` 를 정했습니다.

**두 번 사고를 냈습니다.**

1. 제목을 든 문서가 둘이면 **가나다순으로 앞선 쪽**이 이겼는데, 그것이 「메일 템플릿」이라는
   보장이 없었습니다 — 2026-08-26 에 「B2B 플랜 비교표」(참고 문서)가 「견적 및 맞춤형 플랜
   안내」(실제 회신 서식)를 제쳤습니다. 코드는 이긴 쪽을 지목할 수 없어서 경고만 남겼습니다.
2. 문서 제목은 **운영자가 쓴 고정 문장**이라 문의 언어와 무관하게 그 문서의 언어로
   나갔습니다 — 한국어 문의에 `[Perso Dubbing] Next steps on your customizable plan`
   이 나간 것이 그것입니다(msg 62). `_subject_in_inquiry_language` 가 그것을 번역으로
   덧대고 있었고, 그 함수도 같이 나갔습니다.

**지금 제목은 `common.subjects.reply_subject` 하나가 정합니다** — 「RE: <고객이 쓴 제목>」,
RE: 가 쌓이지 않고, 고객의 말을 그대로 쓰므로 언제나 고객의 언어입니다. 제목 없는 문의에는
그 언어의 일반 문구로 떨어집니다. **CODE GUARD 3(제목을 모델에게 묻지 않는다)은
그대로입니다**: 없어진 것은 문서가 제목을 덮어쓰는 길이고, 모델이 제목을 쓰는 길이 열린
것이 아닙니다.

되살리기 전에 위의 두 사고를 먼저 읽어라 — 고정 제목이 필요하면 「어느 문서가 제목을
정하는가」를 코드가 알 수 있는 방법부터 정해야 합니다. 그게 없어서 이렇게 됐습니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "policy_sources" not in set(inspector.get_table_names()):
        logger.info("0118: policy_sources 없음, 건너뜁니다.")
        return
    if "subject" not in {c["name"] for c in inspector.get_columns("policy_sources")}:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE policy_sources DROP COLUMN subject"))
    logger.info("0118: policy_sources.subject 를 지웠습니다.")
