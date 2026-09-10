"""``policy_sources.model_access`` — 이 문서를 모델에게 보내도 되는가 (2026-09-10).

**왜 생겼나.** 운영 문서 `Perso Dubbing Enterprise 판매 단가`(16,831자)가 「항상 적용」
(``mode='rules'``)이라 ``prompts._rules_from_db`` 가 그것을 **모든 Gemini 호출의 시스템
프롬프트**에 통째로 실었습니다. 초안뿐 아니라 분류·라우팅·요약·번역·회사 분석까지요.
그 안에 있던 것:

- Enterprise 분당 단가 · 립싱크 단가 · Business 단가 · **승인 하한**
- 서비스 등급별 **최소 연 약정액**
- **원가(분당)** 와 **이익률**
- 경쟁사 실명 요율, **공급사 실명과 공급 관계**, 내부 파일 경로

**같은 세트의 다른 문서가 그것을 전부 대외 금지로 못박습니다** — `00_README` 기밀 등급표의
⛔ 열 세 줄이 정확히 이 문서이고, `02_금지어` §A·§D 가 같은 말을 하며, 그 단가 문서 §9
스스로 「단가표·최소 약정액·Business 단가는 전부 비공개」라고 적습니다.

그리고 **금액 가드는 첫 회신에만 돕니다**(``senders.enforce_first_reply_no_price`` 가
``if prior_sent: return``). 후속 초안은 이 숫자들을 쥔 채 아무 가드 없이 쓰였습니다.

**왜 `scope` 에 값을 더하지 않았나.** ``scope`` 는 「어느 회신 단계에」이고 이 칸은
「보내도 되나」라서 축이 다릅니다. 그리고 실무적으로 더 중요한 이유가 있습니다 —
``knowledge.router_docs`` 가 **모르는 stage 를 받으면 scope 필터를 통째로 생략**합니다.
``scope='never'`` 같은 값으로 막으면 그 경로에서 조용히 샙니다. 별도 칸은 **쿼리마다
직접** 걸 수 있습니다.

**거는 자리는 넷이고 전부 쿼리입니다**: ``prompts._rules_from_db`` ·
``knowledge.router_docs`` · 저장 시 「언제 쓰는가」 생성 · 문서 점검(예정).
코드 흐름으로 지키면 다음 호출자가 그 앞을 안 지납니다.

**기본값은 `customer_context` 입니다.** 새 문서를 `human_only` 로 두자는 제안이 있었지만,
운영자가 문서를 넣는 이유가 초안이 그것을 읽게 하려는 것이라 기본이 「안 읽음」이면
넣어 두고 왜 반영이 안 되는지 찾게 됩니다 — 그리고 그 상태는 화면에 「저장됨」으로
보입니다. 이 저장소가 그 형태의 사고를 이미 겪었습니다(0050 의 「URL 만 등록하면 본문이
영원히 빈 행」, 0097 의 「재우다 만 행이 콘솔에 안 보이는 채로」).

**이 이관은 값을 안 옮깁니다.** 어느 문서를 사람용으로 둘지는 운영자가 콘솔에서 고릅니다 —
문서 내용을 아는 사람이 정할 일이고, 코드가 제목으로 짐작할 일이 아닙니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "policy_sources" not in set(inspector.get_table_names()):
        logger.info("0119: policy_sources 없음, 건너뜁니다.")
        return
    if "model_access" in {c["name"] for c in inspector.get_columns("policy_sources")}:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE policy_sources ADD COLUMN model_access VARCHAR(16) "
                "NOT NULL DEFAULT 'customer_context'"
            )
        )
    logger.info("0119: policy_sources.model_access 를 만들었습니다 (전부 customer_context).")
