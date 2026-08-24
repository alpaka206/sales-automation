"""티켓 요약 — 오간 것마다 한 줄, 덧붙이기만.

두 가지를 고정합니다. ① 요약은 **나간 답**만 이야기한다: 예전에는 초안이 만들어진 직후에
대화 전체를 다시 써서, 아무도 보내지 않은 글이 「이에 …라고 안내했습니다」로 들어갔습니다.
② 앞 줄은 고치지 않는다: 기록은 나중에 말이 달라지면 기록이 아닙니다.
"""

from __future__ import annotations

import pathlib
from unittest.mock import patch

from src.db.models import Contact, Conversation, Message


def _conv(session) -> int:
    contact = Contact(
        email="x@example.com", normalized_email="x@example.com", full_name="테스터"
    )
    session.add(contact)
    session.flush()
    conv = Conversation(contact_id=contact.id, stage="new")
    session.add(conv)
    session.commit()
    return conv.id


def _msg(session, conv_id: int, direction: str, body: str) -> int:
    row = Message(
        conversation_id=conv_id, direction=direction, channel="email",
        subject="제목", body=body, status="received",
    )
    session.add(row)
    session.commit()
    return row.id


def test_lines_are_appended_and_never_rewritten(db_session, db_session_factory):
    from src.agents import summaries

    conv_id = _conv(db_session)
    first_id = _msg(db_session, conv_id, "inbound", "문의 본문")
    second_id = _msg(db_session, conv_id, "outgoing", "회신 본문")
    with patch.object(summaries, "SessionLocal", db_session_factory), patch.object(
        summaries, "one_line", side_effect=["문의가 왔다", "답을 보냈다"]
    ):
        summaries.append_summary_line(first_id)
        conv = db_session.get(Conversation, conv_id)
        db_session.refresh(conv)
        assert conv.summary == "- 문의가 왔다"
        # 줄은 그 메시지의 것이기도 합니다 — 화면이 한 줄만 보여 주고 본문은 눌러야 나옵니다.
        assert db_session.get(Message, first_id).summary_line == "문의가 왔다"

        summaries.append_summary_line(second_id)
        db_session.refresh(conv)
        # 앞 줄이 그대로 남아 있는 것이 이 테스트의 전부입니다.
        assert conv.summary == "- 문의가 왔다\n- 답을 보냈다"


def test_the_same_message_twice_adds_one_line(db_session, db_session_factory):
    """티켓 하나에 이벤트가 여러 번 옵니다(웹훅 + 10분 폴러)."""
    from src.agents import summaries

    conv_id = _conv(db_session)
    msg_id = _msg(db_session, conv_id, "inbound", "문의 본문")
    with patch.object(summaries, "SessionLocal", db_session_factory), patch.object(
        summaries, "one_line", return_value="같은 문의"
    ):
        summaries.append_summary_line(msg_id)
        summaries.append_summary_line(msg_id)
    conv = db_session.get(Conversation, conv_id)
    db_session.refresh(conv)
    assert conv.summary == "- 같은 문의"


def test_the_draft_can_no_longer_write_the_summary():
    """요약을 쓰는 길은 `append_summary_line` 하나이고, 초안 경로에는 없습니다."""
    inbound = pathlib.Path("src/agents/inbound.py").read_text(encoding="utf-8")
    assert "conv.summary =" not in inbound
    # 문의 한 줄은 저장이 **실제로 일어났을 때만** 붙습니다(안 썼으면 id 가 None).
    assert "append_summary_line(inbound_message_id)" in inbound
    # 우리 답 한 줄은 Conversations 발송을 지난 뒤에만 붙습니다.
    worker = pathlib.Path("src/agents/send_worker.py").read_text(encoding="utf-8")
    assert "append_summary_line" in worker


def test_an_oversize_subject_is_cut_not_dropped():
    """`customer_interactions.subject` 는 varchar(300). SQLite 는 안 지키고 Postgres 는
    지켜서, 운영에서만 그 연락처의 기록이 통째로 안 들어왔습니다(2026-08-20)."""
    from src.api.routes.customer_ops import _fit

    assert _fit("x" * 400) == "x" * 300
    assert _fit("  제목  ") == "제목"
    assert _fit("") is None
    assert _fit(None) is None
