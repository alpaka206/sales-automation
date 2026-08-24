"""Remove the immediate inbound acknowledgement feature.

Past messages that were actually sent remain in the conversation audit trail. Any
unsent acknowledgement is retired so a deploy cannot release an old queued email,
and the templates used only by this feature are removed from the operator console.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    with engine.begin() as conn:
        if "messages" in tables:
            result = conn.execute(
                text(
                    "UPDATE messages SET status='superseded', scheduled_at=NULL, "
                    "send_claimed_at=NULL WHERE prompt_variant='auto_ack' "
                    "AND status NOT IN ('sent', 'test_sent', 'delivery_unknown')"
                )
            )
            conn.execute(text("DROP INDEX IF EXISTS ux_messages_one_auto_ack_per_conversation"))
            logger.info("0087: retired %d unsent automatic acknowledgements", result.rowcount or 0)

        if "email_templates" in tables:
            result = conn.execute(text("DELETE FROM email_templates WHERE key LIKE 'auto_ack%'"))
            logger.info("0087: removed %d automatic acknowledgement templates", result.rowcount or 0)
