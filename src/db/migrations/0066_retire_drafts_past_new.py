"""New 를 벗어난 티켓에 남아 있는 초안을 종료합니다.

초안은 New 티켓에만 만들어집니다. 그 티켓이 다음 단계(미팅 링크 발송, 협상, 수주, 종료)에
가 있다는 것은 답이 이미 다른 경로로 나갔다는 뜻입니다 — HubSpot 에서 답장했거나, 통화
뒤에 카드를 옮겼거나, 미팅 링크를 보냈거나. 그런 초안을 발송 대기에 두면 운영자에게 고객이
이미 받은 답을 한 번 더 보내라고 청하는 셈입니다.

지금부터는 단계를 옮기는 모든 곳이 ``stage_sync._retire_superseded_drafts`` 를 지납니다.
이 이관은 **그 전에 이미 갇힌 행**을 한 번 정리합니다: 10분 폴러의 stage reconcile 은 최근에
바뀐 티켓만 훑기 때문에, 오래 전에 옮겨진 티켓의 초안에는 아무도 다시 오지 않습니다.

``drafting`` 은 건드리지 않습니다 — 그 행은 워커가 쓰는 중이고, 워커가 끝내면서 같은 판정을
합니다. ``delivery_unknown`` 도 아닙니다: 발송됐는지 아닌지는 사람이 확인할 일입니다.
접수확인(``prompt_variant='auto_ack'``)도 아닙니다: 초안이 아니라 자동 응답입니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

# stage_sync._SUPERSEDABLE / _PAST_NEW 와 같은 집합입니다. 이관은 그때의 스키마에 고정돼야
# 하므로 import 하지 않고 적습니다 — 나중에 단계 이름이 바뀌어도 이 이관이 한 일은 그대로입니다.
_SQL = text(
    """
    UPDATE messages SET status = 'superseded'
     WHERE direction = 'outgoing'
       AND status IN ('pending_approval', 'approved', 'draft_failed', 'send_failed')
       AND (prompt_variant IS NULL OR prompt_variant <> 'auto_ack')
       AND conversation_id IN (
             SELECT id FROM conversations
              WHERE stage IN ('meeting_link_sent', 'negotiation', 'reminder_sent',
                              'won', 'closed_lost', 'closed')
           )
    """
)


def up(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    if not {"messages", "conversations"} <= tables:
        logger.info("0066: messages/conversations missing; skipping.")
        return
    with engine.begin() as conn:
        retired = conn.execute(_SQL).rowcount
    logger.info("0066: retired %s draft(s) on tickets that had already moved past New.", retired)
