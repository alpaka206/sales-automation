"""(비어 있음) 정책 문서를 초안이 읽는 표로 밀어 넣던 곳.

    policy_sources (원본)  ──▶  knowledge_documents (라우터가 읽던 사본)

**그 사본이 없어졌습니다** (2026-08-27). 라우터가 ``policy_sources`` 를 직접 읽습니다 —
사본의 칸은 하나도 자기 것이 아니었고(slug 은 ``doc_key`` 에서, 요약은 ``usage_note``
에서, 메일 제목은 ``subject`` 를 태그에 실어서, ``scope``·``categories``·``author`` 는
행마다 같은 상수), 그래서 이 파일이 하던 일은 전부 「원본을 다시 계산해 옮겨 적기」
였습니다. 옮겨 적는 사이에 어긋날 수 있었고 실제로 어긋났습니다(0097).

이 파일이 없어지지 않는 이유는 ``knowledge_slug`` 하나입니다. 마이그레이션 0097 이
「어느 행이 사본인가」를 그 규칙으로 가렸고, 이미 적용된 DB 에서 다시 돌지는 않지만
파일이 사라지면 그 마이그레이션을 읽을 수 없게 됩니다.
"""

from __future__ import annotations

from ..db.models import PolicySource


def knowledge_slug(source: PolicySource) -> str:
    """사본이 살던 시절의 slug. **이제 만드는 곳은 없습니다** — 0097 이 그 표를 지웠습니다.

    남겨 두는 것은 그 마이그레이션이 이 규칙으로 「사본인 행」을 가렸기 때문입니다.
    ``notion-`` 접두사는 노션에서 받아오던 시절의 흔적이고, 아무것도 가리키지 않습니다.
    """
    return f"notion-{source.doc_key[:12]}"
