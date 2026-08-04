"""Seed the web-editable reply FORMAT (and the two links it ends on).

The shape of a reply — how it opens, what the middle must contain, how it closes —
used to live in the drafting prompt, so changing "인사 문구를 이렇게 바꿔주세요" meant a
deploy. It is now a normal ``email_templates`` row, injected into the draft prompt at
``{{reply_format}}`` and read fresh for every draft, so an edit on /email-templates
applies to the very next reply.

The two links are separate rows on purpose: they are the parts most likely to change on
their own (a new booking calendar, a different number), and keeping them out of the
format body means the operator can rotate a link without touching the wording. The
drafting prompt tells the model to emit the ``{{MEETING_LINK}}`` / ``{{WHATSAPP}}``
tokens verbatim; the send path substitutes the real URLs, so a long booking URL can
never be mangled by the model.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

MEETING_LINK = (
    "https://calendar.google.com/calendar/u/0/appointments/schedules/"
    "AcZssZ3woViQ906eyzcO97gG4oZPCyESiCL7x_WyBERhh3-LZqZSpl-ZPAhONZtZWyQgIN7FzEtqrzwi"
)
WHATSAPP_LINK = "https://wa.me/821054802261"

# Korean, because the draft the operator reviews is Korean and this is an instruction to
# the model — not text that ships to the customer.
REPLY_FORMAT = """회신 형식(반드시 이 뼈대를 따릅니다. 내용은 문의에 맞게 씁니다):

1) 인사: "안녕하세요 {고객 이름}님," 한 줄. 이름을 모르면 "안녕하세요,".
2) 감사: Perso Dubbing에 문의해 주셔서 감사하다는 한 문장.
3) 본론: 고객이 물은 것에 대한 직접적인 답. 여러 질문이면 물은 순서대로.
   플랜·한도·기능처럼 병렬 정보가 2개 이상일 때만 `- ` 불릿을 씁니다.
4) 되묻기: 견적·제안을 준비하기 위해 필요한 정보를 한 번에 묶어서 묻습니다.
   (예: 예상 볼륨, 언어, 영상 종류)
5) 마무리 안내: 아래 두 줄을 그대로 씁니다. 링크 자리에는 토큰을 그대로 두세요.

미팅 예약: {{MEETING_LINK}}
WhatsApp: {{WHATSAPP}}

6) 맺음: 도움이 되기를 바란다는 한 문장 뒤에 company rules에 정의된 서명.

주의:
- 토큰 {{MEETING_LINK}}, {{WHATSAPP}}는 절대 바꾸거나 풀어쓰지 말고 그대로 출력합니다.
- 이 형식은 뼈대일 뿐입니다. 고객이 미팅을 이미 잡았거나 링크가 불필요한 문의라면
  5)를 생략합니다."""

_ROWS = (
    {
        "key": "reply_format",
        "name": "답변 메일 형식",
        "language": "all",
        "body": REPLY_FORMAT,
        "description": "답변 초안의 뼈대(인사·본론·되묻기·마무리). 내용이 아니라 형식만 정합니다.",
    },
    {
        "key": "meeting_link",
        "name": "미팅 예약 링크",
        "language": "all",
        "body": MEETING_LINK,
        "description": "답변 본문의 {{MEETING_LINK}} 토큰이 이 값으로 치환됩니다.",
    },
    {
        "key": "whatsapp_link",
        "name": "WhatsApp 링크",
        "language": "all",
        "body": WHATSAPP_LINK,
        "description": "답변 본문의 {{WHATSAPP}} 토큰이 이 값으로 치환됩니다.",
    },
)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    if "email_templates" not in set(inspector.get_table_names()):
        logger.info("0042: email_templates missing; nothing to seed.")
        return

    is_sqlite = engine.dialect.name == "sqlite"
    ts_default = "CURRENT_TIMESTAMP" if is_sqlite else "NOW()"

    with engine.begin() as conn:
        existing = {
            row[0] for row in conn.execute(text("SELECT key FROM email_templates")).fetchall()
        }
        for row in _ROWS:
            if row["key"] in existing:
                # Never overwrite: the operator may already have edited these.
                continue
            conn.execute(
                text(
                    "INSERT INTO email_templates (key, name, language, channel, body, "
                    "description, status, version, created_at, updated_at) "
                    "VALUES (:key, :name, :language, 'email', :body, :description, "
                    f"'active', 1, {ts_default}, {ts_default})"
                ),
                row,
            )
            logger.info("0042: seeded email template %s", row["key"])
