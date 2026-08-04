"""Which signature a new draft gets becomes a row, not a line of Python.

``email_templates.is_default``. Signatures have been editable in the console since 0019 —
their content, their name, their language — but WHICH one every draft and every
acknowledgement is stamped with was the literal string "signature_html_hyeram", written
twice into src/agents/inbound.py. Changing the person who signs the company's mail meant
editing Python and redeploying, which is not a thing an operator can do.

The existing signature keeps the flag so nothing changes today. From here it moves in the
console, and a partial unique index keeps "the default" meaning one row.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_LEGACY_DEFAULT = "signature_html_hyeram"


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "email_templates" not in set(inspector.get_table_names()):
        logger.info("0046: email_templates missing; skipping.")
        return
    columns = {column["name"] for column in inspector.get_columns("email_templates")}

    with engine.begin() as conn:
        if "is_default" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE email_templates "
                    "ADD COLUMN is_default BOOLEAN NOT NULL DEFAULT FALSE"
                    if engine.dialect.name == "postgresql"
                    else "ALTER TABLE email_templates "
                    "ADD COLUMN is_default BOOLEAN NOT NULL DEFAULT 0"
                )
            )

        # Carry the current behaviour over: whatever inbound.py was hardcoded to.
        already = conn.execute(
            text("SELECT COUNT(*) FROM email_templates WHERE is_default = :yes"),
            {"yes": True},
        ).scalar()
        if not already:
            marked = conn.execute(
                text("UPDATE email_templates SET is_default = :yes WHERE key = :key"),
                {"yes": True, "key": _LEGACY_DEFAULT},
            ).rowcount
            if not marked:
                # That row is gone on this deployment — fall back to any active signature
                # so the send path always has one rather than silently signing nothing.
                conn.execute(
                    text(
                        "UPDATE email_templates SET is_default = :yes WHERE key = ("
                        "  SELECT key FROM email_templates"
                        "  WHERE key LIKE 'signature\\_html\\_%' ESCAPE '\\'"
                        "    AND status = 'active'"
                        "  ORDER BY language, name LIMIT 1)"
                    ),
                    {"yes": True},
                )

        # "The default" has to mean one row. A partial index says so in the database
        # rather than relying on every write path to remember.
        if engine.dialect.name == "postgresql":
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_email_templates_one_default "
                    "ON email_templates ((is_default)) WHERE is_default"
                )
            )
        else:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_email_templates_one_default "
                    "ON email_templates (is_default) WHERE is_default = 1"
                )
            )
    logger.info("0046: email_templates.is_default added and seeded.")
