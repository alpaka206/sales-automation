"""``messages.review_note`` — why this particular draft deserves a closer look.

Every detailed reply already waits for a human; that is structural and not in question.
What the operator could not see was WHICH of the waiting drafts is the risky one. The
policy (§2-4) asks for exactly that: "판단이 애매하거나 사람 검토가 필요한 건은 자동으로
결론내지 말고 초안에 검토 필요로 표시한다."

Two things make a draft ambiguous, and both are known at drafting time:

  * no policy document backed it — the model answered from nothing, which is the one
    situation the guardrails call out ("모르는 건 지어내지 않는다")
  * the lead is not a sales lead at all — CS, spam, recruiting. §1 says these are
    qualified out, and a sales-shaped reply to one is the wrong reply

Null means nothing was flagged, which is the normal case.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "messages" not in set(inspector.get_table_names()):
        logger.info("0047: messages missing; skipping.")
        return
    if "review_note" in {column["name"] for column in inspector.get_columns("messages")}:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE messages ADD COLUMN review_note TEXT"))
    logger.info("0047: messages.review_note added.")
