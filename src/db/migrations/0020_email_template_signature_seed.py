"""Align the seeded email signature templates with the real outgoing signature.

0019 seeded generic placeholder signatures. The live system signs with the
team member's block (injected into company_rules via {{__signature__}}), so we
update the seeded bodies to match — but only when they still hold the original
0019 placeholder, never clobbering an edit made from the web console.

Idempotent and works on both SQLite and Postgres.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

# (key, original 0019 seed body, new body)
_UPDATES = [
    (
        "signature_ko",
        "감사합니다.\nPERSO AI 드림\nhttps://perso.ai",
        "김규원\nPERSO AI | Intern (Developer Relations)\ndevrel.365@gmail.com",
    ),
    (
        "signature_en",
        "Best regards,\nThe PERSO AI Team\nhttps://perso.ai",
        "Kyuwon Kim\nPERSO AI | Intern (Developer Relations)\ndevrel.365@gmail.com",
    ),
]


def up(engine: Engine) -> None:
    insp = inspect(engine)
    if "email_templates" not in set(insp.get_table_names()):
        return  # 0019 not applied yet — nothing to align

    with engine.begin() as conn:
        for key, old_body, new_body in _UPDATES:
            conn.execute(
                text(
                    "UPDATE email_templates SET body = :new_body "
                    "WHERE key = :key AND body = :old_body"
                ),
                {"key": key, "old_body": old_body, "new_body": new_body},
            )
    logger.info("0020: aligned signature template seeds with live signature.")
