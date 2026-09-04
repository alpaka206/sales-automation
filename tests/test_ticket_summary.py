"""티켓 요약 — 오간 것마다 한 줄, 덧붙이기만.

두 가지를 고정합니다. ① 요약은 **나간 답**만 이야기한다: 예전에는 초안이 만들어진 직후에
대화 전체를 다시 써서, 아무도 보내지 않은 글이 「이에 …라고 안내했습니다」로 들어갔습니다.
② 앞 줄은 고치지 않는다: 기록은 나중에 말이 달라지면 기록이 아닙니다.
"""

from __future__ import annotations

import pathlib
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.base import Base
from src.db.models import Contact, Conversation, Message


@pytest.fixture()
def log_db_for_summary():
    """연락처 하나 + 티켓 하나. 라우트가 쓰는 두 모듈을 같이 패치합니다 — 한쪽만 패치하면
    조용히 진짜 DB 를 읽습니다."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        contact = Contact(
            email="s@example.com", normalized_email="s@example.com", full_name="요약 고객"
        )
        session.add(contact)
        session.flush()
        conv = Conversation(contact_id=contact.id, stage="negotiation")
        session.add(conv)
        session.commit()
        ids = {"contact": contact.id, "conv": conv.id}
    with (
        patch("src.api.routes.customer_ops.SessionLocal", factory),
        patch("src.api.routes.messages.SessionLocal", factory),
    ):
        yield factory, ids


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
        conversation_id=conv_id, direction=direction, subject="제목", body=body, status="received",
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


def test_the_ticket_screen_does_not_say_the_same_thing_twice():
    """티켓 화면에서 **요약 카드와 제목 줄을 뺐습니다** (2026-08-25 운영자 지시).

    요약의 불릿과 「이 티켓의 기록」 각 줄의 둘째 줄은 **같은 문자열**입니다 — 한 줄을
    만들어 `messages.summary_line` 과 `conversations.summary` 에 같이 쓰기 때문입니다
    (`append_summary_line`). 그래서 한 화면이 같은 말을 두 번 했고, 카드 쪽에는 시각도
    방향도 없었습니다. 제목 줄도 같은 이유입니다: 그 목록은 스레드 하나라 줄마다 같은
    제목이 반복되고, 우리 메일 줄은 그것을 번역해서 쓰기 때문에 원문·국문이 나란히 놓여
    다른 두 건처럼 보였습니다.

    **값은 그대로 쌓입니다.** 초안 프롬프트가 「기존 대화 요약」으로 읽으므로, 지운 것은
    화면과 그 화면에 보내던 값뿐입니다.
    """
    screen = pathlib.Path("frontend/src/screens/MessageDetail.tsx").read_text(
        encoding="utf-8"
    )
    assert "data.summary" not in screen
    assert "history-item__title" not in screen  # 티켓 기록의 메일 줄
    assert "hideSubject" in screen  # 소통 기록 줄
    route = pathlib.Path("src/api/routes/messages.py").read_text(encoding="utf-8")
    assert '"summary": conv.summary' not in route
    inbound = pathlib.Path("src/agents/inbound.py").read_text(encoding="utf-8")
    assert "기존 대화 요약" in inbound


def test_a_record_row_prints_korean_time():
    """기록 줄의 시각은 `kst()` 를 지납니다. API 가 주는 것은 오프셋 없는 UTC 라 잘라서
    그대로 쓰면 9시간 이른 값이 찍히는데, 같은 목록의 메일 줄은 변환해서 쓰고 있어서
    1분 차이로 오간 두 건이 9시간 떨어져 보였습니다(2026-08-25 실측)."""
    item = pathlib.Path("frontend/src/ui/InteractionForm.tsx").read_text(encoding="utf-8")
    assert "kst(item.happened_at)" in item
    assert "happened_at?.slice" not in item


def test_an_oversize_subject_is_cut_not_dropped():
    """`customer_interactions.subject` 는 varchar(300). SQLite 는 안 지키고 Postgres 는
    지켜서, 운영에서만 그 연락처의 기록이 통째로 안 들어왔습니다(2026-08-20)."""
    from src.api.routes.customer_ops import _fit

    assert _fit("x" * 400) == "x" * 300
    assert _fit("  제목  ") == "제목"
    assert _fit("") is None
    assert _fit(None) is None


# ---------- 요약이 비어 있는 기록을 뒤늦게 메운다 ----------


def _interaction(session, *, summary: str, context: str | None = None,
                 conversation_id: int | None = None) -> int:
    """기록 한 줄. ``conversation_id`` 가 없으면 **티켓에 안 달린** 고객 단위 기록입니다."""
    from src.db.models import CustomerInteraction

    contact = session.query(Contact).first()
    if contact is None:
        contact = Contact(
            email="d@example.com", normalized_email="d@example.com", full_name="요약"
        )
        session.add(contact)
        session.flush()
    row = CustomerInteraction(
        contact_id=contact.id,
        conversation_id=conversation_id,
        channel="email",
        direction="inbound",
        summary=summary,
        context=context,
    )
    session.add(row)
    session.commit()
    return row.id


def test_the_backfill_fills_every_empty_digest_including_ticketless_ones(
    db_session, db_session_factory
):
    """비어 있는 요약을 채웁니다 — **티켓에 안 달린 기록도** (2026-09-03 운영자 지시).

    조건은 `conversation_id` 가 아니라 「요약이 비었나」 하나입니다. 티켓 없는 기록을
    빼면 리드 히스토리의 고객 단위 메모가 영영 본문 앞머리만 보여 줍니다.
    """
    from src.agents import summaries
    from src.db.models import CustomerInteraction

    conv_id = _conv(db_session)
    long_body = "가" * 200
    with_ticket = _interaction(db_session, summary=long_body, conversation_id=conv_id)
    without_ticket = _interaction(db_session, summary=long_body)
    # 이미 사람이 적어 둔 줄은 건드리지 않습니다.
    already = _interaction(db_session, summary=long_body, context="운영자가 적은 줄")

    with patch.object(summaries, "SessionLocal", db_session_factory), patch.object(
        summaries, "one_line", return_value="한 줄 요약"
    ):
        filled = summaries.backfill_interaction_digests(limit=50)

    assert filled == 2
    rows = {r.id: r.context for r in db_session.query(CustomerInteraction).all()}
    assert rows[with_ticket] == "한 줄 요약"
    assert rows[without_ticket] == "한 줄 요약", "티켓 없는 기록도 채워야 합니다"
    assert rows[already] == "운영자가 적은 줄", "사람이 적은 줄을 덮으면 안 됩니다"


def test_even_a_short_record_goes_through_the_model(db_session, db_session_factory):
    """**짧은 본문도 모델이 씁니다** (2026-09-03 운영자 지시: 「다 쓰길 원해」).

    `one_line` 은 80자 미만이면 본문을 그대로 눌러 돌려주는 지름길이 있는데, 백필은 그것을
    끕니다(`always=True`). 티켓 요약은 그대로 두므로 — 그쪽은 오간 것마다 그때그때 도는
    자리라 — 두 경로가 갈리는 것이 의도입니다.
    """
    from src.agents import summaries
    from src.db.models import CustomerInteraction

    row_id = _interaction(db_session, summary="전화로 납기 협의함")

    with patch.object(summaries, "SessionLocal", db_session_factory), patch.object(
        summaries, "one_line", return_value="납기를 전화로 협의했다"
    ) as called:
        filled = summaries.backfill_interaction_digests(limit=10)

    assert filled == 1
    # 짧아도 모델 경로로 갑니다 — `always=True` 가 그 지름길을 끕니다.
    assert called.call_args.kwargs.get("always") is True
    assert db_session.get(CustomerInteraction, row_id).context == "납기를 전화로 협의했다"


def test_a_short_record_keeps_the_shortcut_outside_the_backfill():
    """지름길 자체는 남아 있습니다 — 티켓 요약이 그걸 씁니다."""
    from src.agents.summaries import one_line

    assert one_line("inbound", "제목", "전화로  납기   협의함") == "전화로 납기 협의함"


def test_a_failed_summary_leaves_the_row_for_the_next_round(db_session, db_session_factory):
    """모델이 실패하면 **값 없이 남깁니다** — 본문을 그대로 적어 두고 끝내지 않습니다.

    그렇게 굳히면 모델이 잠깐 죽어 있던 동안의 행들만 영영 요약 없이 남는데, 화면에서는
    그 차이가 안 보입니다. 비워 두면 다음 회차가 다시 집습니다.
    """
    from src.agents import summaries
    from src.db.models import CustomerInteraction

    row_id = _interaction(db_session, summary="가" * 200)

    with patch.object(summaries, "SessionLocal", db_session_factory), patch.object(
        summaries, "one_line", return_value=None
    ):
        assert summaries.backfill_interaction_digests(limit=10) == 0

    assert db_session.get(CustomerInteraction, row_id).context is None


def test_the_poller_runs_the_digest_backfill():
    """폴러 한 회차가 이 일을 합니다 — 배포하면 저절로 다 채워집니다."""
    from src.agents.inbound_poller import _poller_steps

    assert "interaction_digests" in {name for name, _ in _poller_steps()}


# ---------- 티켓 화면의 「리드 히스토리」는 요약만 그린다 ----------


def test_adding_a_touchpoint_updates_that_tickets_summary(log_db_for_summary):
    """**소통을 추가하면 그 티켓 요약이 바뀝니다** (2026-09-04 운영자 지시).

    티켓 화면의 「리드 히스토리」가 티켓마다 `conversations.summary` 한 문단을 그리므로,
    여기서 안 붙이면 운영자가 통화를 적고 새로고침해도 카드가 안 변합니다 — 10분 폴러의
    요약 백필은 `CustomerInteraction.context` 만 채우고 대화는 안 건드립니다.

    **모델을 안 부릅니다** — 운영자가 쓴 문장을 그대로 씁니다.
    """
    from fastapi.testclient import TestClient
    from src.api.main import app
    from src.db.models import Conversation as Conv

    factory, ids = log_db_for_summary
    with TestClient(app) as client:
        client.post(
            f"/customers/{ids['contact']}/interactions",
            data={
                "channel": "phone",
                "summary": "전화로 납기 협의, 다음 주 재통화 약속",
                "conversation_id": str(ids["conv"]),
            },
            follow_redirects=False,
        )
    with factory() as session:
        summary = session.get(Conv, ids["conv"]).summary or ""
    assert "전화로 납기 협의, 다음 주 재통화 약속" in summary
    assert summary.startswith("- "), "요약은 불릿 목록입니다"


def test_a_contact_level_record_leaves_every_summary_alone(log_db_for_summary):
    """티켓을 안 고르고 적은 기록은 어느 티켓 요약에도 안 붙습니다 — 어느 티켓인지
    모르는 기록이니까요. 그런 기록은 티켓 화면에서 「그 외 n건」으로만 셉니다."""
    from fastapi.testclient import TestClient
    from src.api.main import app
    from src.db.models import Conversation as Conv

    factory, ids = log_db_for_summary
    with TestClient(app) as client:
        client.post(
            f"/customers/{ids['contact']}/interactions",
            data={"channel": "phone", "summary": "고객 단위 메모"},
            follow_redirects=False,
        )
    with factory() as session:
        assert not (session.get(Conv, ids["conv"]).summary or "")


def test_the_lead_history_card_draws_summaries_not_rows():
    """티켓 화면의 「리드 히스토리」는 **요약 문단**이지 기록 줄 목록이 아닙니다.

    운영자 지시(2026-09-04): 「티켓별로 요약본만 보여지면 좋겠다 … 세부 이메일 내용 아예 x」.
    되돌아가면 화면에 `InteractionItem` 목록이 다시 서고, 그건 답을 쓰는 자리에 메일함을
    하나 더 세우는 일입니다.
    """
    import pathlib

    screen = pathlib.Path("frontend/src/screens/MessageDetail.tsx").read_text(
        encoding="utf-8"
    )
    card = screen[screen.index('<div className="section-header__title">리드 히스토리</div>'):]
    card = card[: card.index("티켓 정보")]
    assert "other.summary" in card, "티켓마다 요약 문단을 그려야 합니다"
    assert "InteractionItem" not in card, "리드 히스토리에 기록 줄이 서면 안 됩니다"
    # 「그 외 n건」 · 「전체보기」 · 눈에 띄는 빈 상태.
    assert "loose_count" in card and "전체보기" in card
    assert "이전 히스토리가 존재하지 않습니다." in card
    assert "empty__text--lead" in card
    # 지금 보고 있는 티켓은 안 들어갑니다 — 그 요약은 왼쪽 「이 티켓의 기록」과 같은 글자입니다.
    assert "data.other_tickets.map" in card


def test_the_bold_empty_state_has_a_rule_of_its_own():
    """`.empty__text--lead` 가 실제로 크고 두꺼워야 합니다 — `.tag` 처럼 규칙 없는 클래스를
    쓰면 화면에서는 아무것도 안 바뀝니다."""
    import pathlib

    css = pathlib.Path("src/api/static/console.css").read_text(encoding="utf-8")
    rule = css[css.index(".empty__text--lead"):]
    rule = rule[: rule.index("}") + 1]
    assert "font-weight" in rule and "font-size" in rule


def test_the_lead_history_sits_beside_the_draft_on_new_and_below_the_log_after():
    """**자리가 둘입니다** (2026-09-04 운영자 지시).

    New 에서는 오른쪽 — 그때 본론은 초안이고 이건 그것을 쓰기 위한 참고입니다. New 를
    지나면 본론이 「이 사람과 무슨 이야기가 오갔나」로 바뀌므로 「이 티켓의 기록」 **아래**,
    본문 칼럼에 섭니다.

    카드는 **한 벌**이라야 합니다 — 두 벌 적으면 한쪽만 고치는 날이 옵니다.
    """
    import pathlib

    screen = pathlib.Path("frontend/src/screens/MessageDetail.tsx").read_text(
        encoding="utf-8"
    )
    assert screen.count("const leadHistoryCard") == 1, "카드는 한 벌입니다"
    assert "{afterNew && leadHistoryCard}" in screen, "New 이후에는 본문 칼럼"
    assert "{!afterNew && leadHistoryCard}" in screen, "New 에서는 오른쪽"

    # 본문 칼럼 쪽은 「이 티켓의 기록」 **뒤**에 와야 합니다.
    log_at = screen.index('<div className="section-header__title">이 티켓의 기록</div>')
    main_at = screen.index("{afterNew && leadHistoryCard}")
    assert log_at < main_at, "「이 티켓의 기록」 아래에 서야 합니다"


def test_the_backfill_finishes_one_ticket_at_a_time(db_session, db_session_factory):
    """**티켓 하나를 통째로** 채웁니다 — 그래야 요약이 그 회차에 완성됩니다.

    표 전체에서 무작위로 집던 때에는 한 티켓의 줄이 다 뽑힐 때까지 몇 시간이 걸려 요약이
    하나도 안 만들어졌습니다(운영 로그: 「20건 채움, 티켓 요약 0건 재생성, 남은 1864건」).
    """
    from src.agents import summaries
    from src.db.models import Conversation as Conv

    conv_id = _conv(db_session)
    for index in range(5):
        _interaction(db_session, summary="가" * 200, conversation_id=conv_id)
    # 한 회차 예산이 그 티켓의 줄 수보다 커야 완성됩니다. 줄마다 다른 값이라야 합니다 —
    # `append_line` 은 똑같은 불릿을 두 번 안 붙입니다(웹훅과 폴러가 같은 이벤트를 두 번
    # 나르는 길이 있어서).
    with patch.object(summaries, "SessionLocal", db_session_factory), patch.object(
        summaries, "one_line", side_effect=[f"{i}번째 접점" for i in range(5)]
    ):
        assert summaries.backfill_interaction_digests(limit=120) == 5

    db_session.expire_all()
    # 줄이 다 찼으니 그 티켓 요약이 그 자리에서 만들어집니다.
    summary = db_session.get(Conv, conv_id).summary or ""
    assert summary.count("번째 접점") == 5, summary
