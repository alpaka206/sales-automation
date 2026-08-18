"""「전체」라고 적혀 있던 세 행은 사실 **국문 행**입니다.

``reply_format`` · ``meeting_link`` · ``whatsapp_link`` 는 ``language='all'`` 로 심겼고,
콘솔의 언어 칸에 「전체」라고 떴습니다. 그런데 0069 가 각각에 ``_en`` 행을 붙인 뒤로 발송
경로는 영문 문의에서 ``_en`` 행을 **먼저** 봅니다 (``prompts.get_reply_format`` ·
``prompts.apply_editable_tokens``). 즉 「전체」라고 적힌 행은 영문 문의에 한 글자도 닿지
않습니다 — 국문(과 ``_en`` 행이 없는 나머지 언어)의 행입니다.

운영자가 답변 메일 형식을 고쳐도 영문 회신이 그대로였던 이유가 이것입니다: 고친 것은
「전체」라고 적힌 국문 행이었고, 영문 회신은 손대지 않은 ``reply_format_en`` 을 계속
읽었습니다. 화면에는 그럴 이유가 하나도 안 보였습니다.

값만 바꿉니다. ``get_email_template`` 의 ``language`` 인자는 **한 키 안에서** 행을 고르는
장치이고(0053), 이 세 키에는 행이 하나씩뿐이라 조회 결과는 그대로입니다. 바뀌는 것은 콘솔의
언어 칸 한 글자뿐이고, 그 한 글자가 지금까지 거짓말을 하고 있었습니다.

서명은 건드리지 않습니다 — 서명에 언어라는 것이 없고(0063), ``all`` 이 맞습니다.
``auto_ack_footer`` 도 그대로입니다: 로고 한 줄이라 정말로 언어가 없습니다.

**그리고 0059 를 두 행만큼 되돌립니다.** 0059 는 이름에서 언어를 뺐습니다 — 「목록은 언어가
같은 템플릿을 한 줄로 묶고 언어는 따로 보여주므로」. 그 묶음이 없어졌습니다. 목록이 행마다
한 줄이 된 지금 ``auto_ack`` 과 ``auto_ack_en`` 은 「자동 접수확인」 두 줄로, ``sender_name``
과 ``sender_name_en`` 은 「담당자 이름」 두 줄로 나란히 섭니다. 언어 칸이 갈라 주기는 하지만,
0069 가 심은 나머지 세 짝은 이름에 「(영문)」 을 달고 있어서 다섯 짝 중 셋만 이름으로
구분되는 목록이 됩니다. 0069 의 표기에 맞춥니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

# 영문 문의에서 `<key>_en` 이 우선하는 키들. 그래서 접미사 없는 쪽은 국문 행입니다.
_KOREAN_ROWS = ("reply_format", "meeting_link", "whatsapp_link")

# 0059 가 언어를 뗀 두 행. 0069 가 심은 세 행의 표기(「(영문)」)에 맞춥니다.
_ENGLISH_NAMES = {
    "auto_ack_en": "자동 접수확인 (영문)",
    "sender_name_en": "담당자 이름 (영문)",
}


def up(engine: Engine) -> None:
    if "email_templates" not in set(inspect(engine).get_table_names()):
        logger.info("0074: email_templates missing; skipping.")
        return
    moved = 0
    with engine.begin() as conn:
        for key in _KOREAN_ROWS:
            moved += conn.execute(
                text(
                    "UPDATE email_templates SET language = 'ko' "
                    "WHERE key = :key AND language = 'all'"
                ),
                {"key": key},
            ).rowcount
        for key, name in _ENGLISH_NAMES.items():
            # 0059 가 남긴 이름일 때만. 그 사이 운영자가 직접 지은 이름은 그 사람 것입니다.
            conn.execute(
                text(
                    "UPDATE email_templates SET name = :name "
                    "WHERE key = :key AND name = :stale"
                ),
                {"name": name, "key": key, "stale": name.removesuffix(" (영문)")},
            )
    logger.info("0074: %s rows relabelled 'all' -> 'ko'.", moved)
