"""``insight_drafts`` — 영업 인사이트 메일 초안의 기본 문구 (2026-10-16).

메일 초안 문구가 화면 코드(``insights/mail.ts``)에 박혀 있어 고치려면 배포가 필요했습니다. 운영자 지시로 「메일 초안」
창의 「기본 문구 수정」에서 고치게 바꾸고, 고친 칸을 이 표에 둡니다 — 카드 × 언어 × 부분(제목 · 공통 문단 · 근거별
문단)마다 한 행, **고친 칸만** 행이 있고 없으면 ``db/insight_drafts.DEFAULTS`` 입니다. 그래서 씨앗도 옮길 값도 없습니다.

**이메일 템플릿 표와 따로 둡니다** — 인바운드 답장 AI 의 문서 정리기가 ``email_templates`` 의 행을 전부 읽기 때문입니다
(「기존 이메일 템플릿 표와 분리해서 인바운드 답장 AI가 영업 초안을 읽지 않게」). 바꾼 기록은 0132 의 ``insight_log`` 에
바꾸기 전 글과 함께 남습니다. 외부 공개(Supabase REST) 차단은 ``run_migrations()`` 끝의 RLS 가 새 표에도 겁니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    # 새 DB 는 0001 의 create_all 이 지금 모델로 이미 만들었습니다.
    if "insight_drafts" in set(inspect(engine).get_table_names()):
        return
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE insight_drafts (
                tab VARCHAR(16) NOT NULL,
                lang VARCHAR(8) NOT NULL,
                part VARCHAR(32) NOT NULL,
                content TEXT NOT NULL,
                updated_by VARCHAR(255),
                updated_at TIMESTAMP NOT NULL,
                PRIMARY KEY (tab, lang, part)
            )
        """))
    logger.info("0134: insight_drafts 를 만들었습니다.")
