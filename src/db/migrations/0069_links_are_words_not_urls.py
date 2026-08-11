"""링크는 주소가 아니라 **글자**로 나갑니다 — 그리고 영문 회신은 제 서식을 갖습니다.

0042 는 링크 두 개를 맨 URL 로 심었고, 서식(``reply_format``)은 한 벌만 두었습니다. 실제로
나간 국문 초안이 이렇게 끝났습니다::

    미팅 예약: https://calendar.google.com/calendar/u/0/appointments/schedules/AcZssZ3w…
    WhatsApp: https://wa.me/821054802261

두 가지가 틀렸습니다.

1. **120자 base64 예약 주소가 본문 한복판에 그대로 보입니다.** 국문은 「미팅 링크」 라는
   글자에, 영문은 ``Calendly`` · ``WhatsApp`` 각각에 걸려야 합니다. 렌더러가 이미
   ``[글자](주소)`` 를 앵커로 만드므로(``integrations/email_html``), 행을 그 형태로 바꿔
   두면 됩니다 — 주소는 그대로 두고 표기만 바뀝니다.
2. **국문 회신에 WhatsApp 안내가 붙었습니다.** 언어 판별이 틀린 것이 아닙니다 —
   ``get_reply_format`` 은 국문 문의에 국문 행을 제대로 줬고, 그 **국문 행 자체**에
   ``WhatsApp: {{WHATSAPP}}`` 이 적혀 있었습니다. 국내 고객에게 WhatsApp 을 안내할 일은
   없으므로 그 줄을 국문 서식에서 뺍니다.

**왜 콘솔이 아니라 마이그레이션인가.** 이메일 템플릿 화면의 「추가」는 ``signature_`` 접두사가
붙은 행만 만듭니다(``_generate_key``) — 코드가 이름으로 찾는 행은 만들 수 없습니다. 그래서
``reply_format_en`` · ``meeting_link_en`` · ``whatsapp_link_en`` 세 행은 운영자가 콘솔에서
만들 방법이 아예 없고, 없으면 영문 문의가 국문 행으로 떨어집니다. ``sender_name_en`` 을
0055 로 심은 것과 같은 이유입니다. 심은 뒤에는 전부 콘솔에서 고칠 수 있습니다.

**고쳐 쓰는 두 행은 revision 을 먼저 남깁니다.** 운영자가 쓴 글을 마이그레이션이 말없이
바꾸면 어디서 바뀌었는지 찾을 길이 없습니다 — 이력 화면에 예전 본문이 그대로 남습니다.
줄 단위로만 손대므로 그 사이 다른 곳을 고쳐 두었다면 그 편집은 살아남습니다.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://\S+")


def _url_of(body: str | None) -> str:
    """행에서 주소만 꺼냅니다 — 맨 URL 이든 이미 ``[글자](주소)`` 든."""
    found = _URL_RE.search(body or "")
    return found.group(0).rstrip(")").strip() if found else ""


def _labelled(body: str | None, label: str) -> str | None:
    """맨 URL 이면 ``[label](url)`` 로. 이미 표기가 붙어 있으면 건드리지 않습니다."""
    url = _url_of(body)
    if not url or "](" in (body or ""):
        return None
    return f"[{label}]({url})"


def _korean_format(body: str) -> str:
    """국문 서식에서 WhatsApp 을 빼고, 미팅 링크 줄을 토큰만 남깁니다.

    줄 단위로 지우고 바꿉니다. 통째로 덮어쓰면 운영자가 그 사이 고친 문장이 조용히
    사라지고, 그건 이 마이그레이션이 고치려는 문제와 같은 종류의 문제입니다.
    """
    out = []
    for line in body.splitlines():
        # 지우는 것은 **안내 줄** 하나입니다. 토큰을 그대로 출력하라는 주의 문장에도
        # {{WHATSAPP}} 가 나오는데, 그 줄까지 통째로 지우면 120자 예약 주소를 모델이
        # "정리" 하지 못하게 막던 지시가 같이 사라집니다.
        if "{{WHATSAPP}}" in line and "{{MEETING_LINK}}" not in line:
            continue
        line = line.replace(", {{WHATSAPP}}", "").replace("아래 두 줄을", "아래 한 줄을")
        stripped = line.strip()
        # "미팅 예약: {{MEETING_LINK}}" → "{{MEETING_LINK}}". 치환값이 이제 「미팅 링크」라
        # 그대로 두면 "미팅 예약: 미팅 링크" 가 됩니다.
        if stripped.endswith("{{MEETING_LINK}}") and stripped != "{{MEETING_LINK}}":
            line = "{{MEETING_LINK}}"
        out.append(line)
    return "\n".join(out)


def _snapshot(conn, row, note: str, ts_default: str) -> None:
    conn.execute(
        text(
            "INSERT INTO email_template_revisions (template_id, key, name, language, "
            "channel, body, description, status, change_note, edited_by, created_at) "
            "VALUES (:id, :key, :name, :language, :channel, :body, :description, "
            f":status, :note, '0069', {ts_default})"
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
            "note": note,
        },
    )


def up(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    if "email_templates" not in tables:
        logger.info("0069: email_templates missing; nothing to do.")
        return
    has_revisions = "email_template_revisions" in tables
    ts_default = "CURRENT_TIMESTAMP" if engine.dialect.name == "sqlite" else "NOW()"

    with engine.begin() as conn:
        rows = {
            row.key: row
            for row in conn.execute(
                text(
                    "SELECT id, key, name, language, channel, body, description, status "
                    "FROM email_templates WHERE key IN "
                    "('reply_format', 'meeting_link', 'whatsapp_link')"
                )
            ).fetchall()
        }

        def _seed(key: str, name: str, language: str, body: str, description: str) -> None:
            if conn.execute(
                text("SELECT 1 FROM email_templates WHERE key = :k"), {"k": key}
            ).first():
                return
            conn.execute(
                text(
                    "INSERT INTO email_templates (key, name, language, channel, body, "
                    "description, status, version, created_at, updated_at) VALUES "
                    "(:key, :name, :language, 'email', :body, :description, 'active', 1, "
                    f"{ts_default}, {ts_default})"
                ),
                {
                    "key": key,
                    "name": name,
                    "language": language,
                    "body": body,
                    "description": description,
                },
            )
            logger.info("0069: seeded %s", key)

        meeting = rows.get("meeting_link")
        meeting_url = _url_of(meeting.body if meeting else "")
        whatsapp_url = _url_of(rows["whatsapp_link"].body if "whatsapp_link" in rows else "")

        if meeting is not None:
            labelled = _labelled(meeting.body, "미팅 링크")
            if labelled:
                if has_revisions:
                    _snapshot(conn, meeting, "0069: 주소를 「미팅 링크」 표기로", ts_default)
                conn.execute(
                    text(
                        "UPDATE email_templates SET body = :body, version = version + 1, "
                        f"updated_at = {ts_default} WHERE id = :id"
                    ),
                    {"body": labelled, "id": meeting.id},
                )

        if meeting_url:
            _seed(
                "meeting_link_en",
                "미팅 예약 링크 (영문)",
                "en",
                f"[Calendly]({meeting_url})",
                "영어 회신의 {{MEETING_LINK}} 이 이 값으로 치환됩니다.",
            )
        if whatsapp_url:
            _seed(
                "whatsapp_link_en",
                "WhatsApp 링크 (영문)",
                "en",
                f"[WhatsApp]({whatsapp_url})",
                "영어 회신의 {{WHATSAPP}} 이 이 값으로 치환됩니다.",
            )

        fmt = rows.get("reply_format")
        if fmt is not None and fmt.body:
            # 영문 행은 **국문 행을 고치기 전 모습**에서 뜹니다: 영문 회신에는 WhatsApp 이
            # 그대로 필요하고, 서식 자체는 모델에게 주는 지시라 한국어인 편이 맞습니다.
            _seed(
                "reply_format_en",
                "답변 메일 형식 (영문 문의)",
                "en",
                fmt.body,
                "영어 문의의 답변 초안 뼈대. 없으면 국문 행으로 떨어집니다.",
            )
            trimmed = _korean_format(fmt.body)
            if trimmed != fmt.body:
                if has_revisions:
                    _snapshot(conn, fmt, "0069: 국문 서식에서 WhatsApp 제거", ts_default)
                conn.execute(
                    text(
                        "UPDATE email_templates SET body = :body, version = version + 1, "
                        f"updated_at = {ts_default} WHERE id = :id"
                    ),
                    {"body": trimmed, "id": fmt.id},
                )
                logger.info("0069: 국문 reply_format 에서 WhatsApp 줄을 뺐습니다.")
