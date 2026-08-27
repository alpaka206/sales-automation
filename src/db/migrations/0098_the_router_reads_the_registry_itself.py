"""사본 표를 지웁니다 — 라우터가 ``policy_sources`` 를 직접 읽습니다 (2026-08-27).

``knowledge_documents`` 의 칸은 **하나도 자기 것이 아니었습니다.** 전부 ``policy_sources``
에서 계산해 옮겨 적은 값입니다:

===================  ==========================================================
사본의 칸            어디서 왔나
===================  ==========================================================
``slug``             ``f"notion-{doc_key[:12]}"``
``title``            ``title or label``
``body``             그대로 복사
``summary``          ``usage_note`` 있으면 그것, 없으면 본문 앞 400자
``tags``             ``["notion", f"subject:{subject}"]``
``status``           원본을 지울 때 같이 재움(``_set_knowledge_status``)
``scope``            행마다 ``"inbound"`` 상수
``categories``       행마다 ``["all"]`` 상수
``author``           행마다 ``"notion-sync"`` 상수
``version``          자기 카운터 — **읽는 곳이 로그 한 줄뿐**
===================  ==========================================================

파생물이라 **어긋날 수 있었고, 어긋났습니다.** 상태를 따로 재워야 했고, 저장 직후 사본을
따로 밀어야 했고(``refresh_knowledge_copy``), 재우다 만 행 하나가 콘솔에 안 보이는 채로
초안에 인용될 뻔했습니다(0097 의 ``perso_refund_policy``). 운영자의 규칙 —「다 사이트에서
추가하는 걸로만, 필요하면 사이트에서도 뜨게」— 을 지키려면 표가 하나여야 합니다.

**모델이 읽는 문서 본문은 한 글자도 안 바뀝니다.** 운영 데이터로 바꾸기 전과 후를 돌려
대조했습니다: 문서 8편, 본문 9534자 바이트 단위 동일. 라우터 인덱스만 2083자 → 1633자로
줄었는데, 빠진 것이 행마다 똑같던 ``categories: all`` 과 ``tags: notion`` 두 줄입니다.
메일 제목도 이제 인덱스에 안 실립니다 — 코드가 ``policy_sources.subject`` 를 직접 읽습니다.

**되살릴 것은 없습니다.** 이 표에 있던 내용은 전부 ``policy_sources`` 에 그대로 있습니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    if "knowledge_documents" not in set(inspect(engine).get_table_names()):
        logger.info("0098: knowledge_documents 이미 없습니다.")
        return
    with engine.begin() as conn:
        left = conn.execute(text("SELECT COUNT(*) FROM knowledge_documents")).scalar()
        conn.execute(text("DROP TABLE knowledge_documents"))
    logger.info("0098: knowledge_documents 를 지웠습니다 (%s행). 원본은 policy_sources 입니다.", left)
