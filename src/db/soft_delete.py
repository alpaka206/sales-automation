"""지운 것은 화면에서 **바로** 사라지고, DB 에는 **영원히** 남습니다.

2026-08-27 운영자 지시: 「삭제는 모달 띄우고 삭제한다고 하면 바로 삭제하고, 내가 db 에서
볼 수 있게 영원히 지우지는 않도록.」

그 전에는 7일짜리 휴지통이었습니다 — 지운 행이 목록에 흐리게 남아 「N일 후 완전 삭제」와
되돌리기 버튼을 달고 있다가, 7일 뒤 ``purge_expired`` 가 본문과 판본 이력을 같이 지웠습니다.
그 설계는 두 가지를 맞바꾼 것이었습니다: 실수를 되돌릴 창을 주는 대신, 일부러 흘려보낸 것이
정말 사라진다는 보장. 지금 규칙은 그 맞바꿈을 안 합니다 — **화면에서는 즉시 사라지고, DB
에서는 안 사라집니다.**

지우는 것은 여전히 행을 지우지 않습니다: ``status='deleted'`` 로 바꾸고 ``deleted_at`` 에
시각을 박습니다. 달라진 것은 둘입니다.

- **목록이 지운 행을 안 싣습니다.** 「N일 후 완전 삭제」도 되돌리기 버튼도 없습니다.
- **청소가 없습니다.** ``purge_expired`` 는 사라졌습니다. 행도, 판본 이력도 계속 남습니다.

**Gemini 는 지운 것을 절대 안 봅니다.** 읽는 쪽 셋이 전부 이미 ``status='active'`` 만 봅니다
— 서명·링크 조회(``db/email_templates``), 항상 적용 규칙(``llm/prompts._rules_from_db``),
문의별 참고 문서(``llm/knowledge._is_active``). 정책 문서를 지우면 초안이 읽는 **사본**도
같이 재웁니다(``policy_docs._set_knowledge_status``). 그리고 판본 이력
(``document_revisions``)은 ``src/llm`` · ``src/agents`` 어디에서도 읽지 않습니다 — 그 표를
만지는 코드는 콘솔 라우트뿐입니다. ``tests/test_safe_mode.py`` 가 고정합니다.
"""

from __future__ import annotations

from datetime import datetime, timezone

DELETED = "deleted"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
