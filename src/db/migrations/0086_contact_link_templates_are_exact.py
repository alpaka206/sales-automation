"""연락 링크 템플릿을 주소만 보존한 고정 Markdown 표기로 정리합니다.

본문 문장은 모델이 만들지만 링크 템플릿은 각각 정확히 ``[Calendly](...)``와
``[WhatsApp](...)`` 한 줄이어야 합니다. 운영자가 바꾼 URL은 그대로 두며, 바꾸기 전 값은
revision에 남깁니다. 최종 두 줄 배치는 발송 코드가 한 번 더 보장합니다.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)
_URL_RE = re.compile(r"https?://[^\s)>\]]+")
_KEYS = ("meeting_link", "meeting_link_en", "whatsapp_link", "whatsapp_link_en")


def _url(body: str | None) -> str:
    found = _URL_RE.search(body or "")
    return found.group(0).rstrip(".,;:") if found else ""


def up(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    if "email_templates" not in tables:
        logger.info("0086: email_templates 없음, 건너뜁니다")
        return
    has_revisions = "email_template_revisions" in tables
    now = "CURRENT_TIMESTAMP" if engine.dialect.name == "sqlite" else "NOW()"

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT id, key, name, language, channel, body, description, status "
                "FROM email_templates WHERE key IN "
                "('meeting_link', 'meeting_link_en', 'whatsapp_link', 'whatsapp_link_en')"
            )
        ).fetchall()
        changed = 0
        for row in rows:
            url = _url(row.body)
            if not url:
                logger.warning("0086: %s 템플릿에 URL이 없어 그대로 둡니다", row.key)
                continue
            label = "Calendly" if row.key.startswith("meeting_link") else "WhatsApp"
            desired = f"[{label}]({url})"
            if (row.body or "").strip() == desired:
                continue
            if has_revisions:
                conn.execute(
                    text(
                        "INSERT INTO email_template_revisions "
                        "(template_id, key, name, language, channel, body, description, "
                        "status, change_note, edited_by, created_at) VALUES "
                        "(:id, :key, :name, :language, :channel, :body, :description, "
                        f":status, :note, '0086', {now})"
                    ),
                    {
                        "id": row.id,
                        "key": row.key,
                        "name": row.name,
                        "language": row.language or "all",
                        "channel": row.channel or "email",
                        "body": row.body or "",
                        "description": row.description,
                        "status": row.status or "active",
                        "note": "0086: 연락 링크를 고정 Markdown 표기로 정리",
                    },
                )
            conn.execute(
                text(
                    "UPDATE email_templates SET body=:body, version=version+1, "
                    f"updated_at={now} WHERE id=:id"
                ),
                {"body": desired, "id": row.id},
            )
            changed += 1
        logger.info("0086: 연락 링크 템플릿 %d개를 정리했습니다", changed)
