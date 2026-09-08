"""티켓별 대화 수집 — 「하나도 빠짐없이」가 이 파일이 고정하는 것입니다.

실측(2026-09-02)으로 드러난 누락이 근거입니다: 티켓 327건 중 133건에서 고객 메시지 627건이
없었고, 채팅 채널 메시지 896건은 기존 경로로 볼 수조차 없었습니다. 여기 있는 검사는 그
누락을 하나씩 되짚습니다 — 통과한다고 완벽해지는 것은 아니지만, **다시 그렇게 되는 것은**
막습니다.
"""

from __future__ import annotations

import pytest

from src.agents.ticket_history import classify_direction, collect_ticket_history


class _FakeClient:
    """스레드/메시지 응답을 흉내 냅니다. 실제 응답 모양 그대로입니다."""

    def __init__(self, pages: dict[str, list[dict]]):
        self.pages = pages
        self.calls: list[str] = []

    async def _get_conversation_json(self, path: str, params=None, action="") -> dict:
        after = (params or {}).get("after")
        key = f"{path}|{after or ''}"
        self.calls.append(key)
        return self.pages.get(key, {"results": []})


# --------------------------------------------------------------------------- #
# 방향 — 운영자 규칙과 실측 보완
# --------------------------------------------------------------------------- #
def _sender(address=None, actor=None) -> dict:
    party: dict = {}
    if address:
        party["deliveryIdentifier"] = {"type": "HS_EMAIL_ADDRESS", "value": address}
    if actor:
        party["actorId"] = actor
    return {"senders": [party]}


@pytest.mark.parametrize(
    ("message", "expected", "why"),
    [
        (_sender("untae@estsoft.com"), "outgoing", "운영자 규칙 — 우리 도메인"),
        (_sender("perso.ai@estsoft.com"), "outgoing", "팀 주소"),
        # **이것이 없으면 실측 60건이 고객으로 뒤집힙니다.**
        (_sender("support@perso.ai"), "outgoing", "perso.ai 도 우리 도메인"),
        (_sender("support@45169260.hubspot-inbox.com"), "outgoing", "허브스팟 전달 주소"),
        (_sender("support@perso.co.kr.hs-inbox.com"), "outgoing", "허브스팟 전달 주소"),
        (_sender("buyer@gmail.com"), "inbound", "그 외는 전부 고객"),
        (_sender("someone@estsoft.com.evil.com"), "inbound", "도메인 끝이 우리가 아니면 남"),
    ],
)
def test_the_sender_domain_decides_who_sent_it(message, expected, why):
    assert classify_direction(message) == expected, why


def test_a_chat_message_with_no_address_falls_back_to_the_actor():
    """발신 주소가 없는 메시지가 실측 896건입니다 — 채팅·봇.

    주소 규칙만 두면 이 전부가 한쪽으로 쏠립니다. `V-` 는 방문자라 고객, `A-`(상담원)·
    `B-`(봇)는 우리입니다.
    """
    assert classify_direction(_sender(actor="V-12345")) == "inbound"
    assert classify_direction(_sender(actor="A-82843387")) == "outgoing"
    assert classify_direction(_sender(actor="B-9956121")) == "outgoing"


def test_hubspot_own_direction_is_only_the_last_resort():
    """허브스팟의 `direction` 을 먼저 믿으면 안 됩니다.

    우리 영업이 **자기 메일함에서** 답한 것을 허브스팟은 INCOMING 으로 적습니다(실측 35건).
    주소가 우리 것이면 주소가 이깁니다.
    """
    ours_but_labelled_incoming = {**_sender("untae@estsoft.com"), "direction": "INCOMING"}
    assert classify_direction(ours_but_labelled_incoming) == "outgoing"
    # 주소도 액터도 없을 때만 그 값으로 떨어집니다.
    assert classify_direction({"direction": "INCOMING"}) == "inbound"
    assert classify_direction({"direction": "OUTGOING"}) == "outgoing"


# --------------------------------------------------------------------------- #
# 완전성 — 실측으로 드러난 누락 셋
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_it_follows_the_cursor_instead_of_counting_results():
    """**「페이지가 안 찼으면 끝」으로 판단하면 안 됩니다.**

    실측으로 `limit=2` 요청이 결과 1건과 `paging.next.after` 를 **같이** 돌려줍니다.
    수를 세서 끝났다고 판단하면 조용히 절반만 가져옵니다.
    """
    threads = "/conversations/v3/conversations/threads"
    msgs = "/conversations/v3/conversations/threads/t1/messages"
    client = _FakeClient({
        f"{threads}|": {"results": [{"id": "t1"}]},
        # 결과 1건 + 커서 — 여기서 멈추면 두 번째 페이지를 통째로 잃습니다.
        f"{msgs}|": {
            "results": [{"type": "MESSAGE", "id": "m1", "text": "첫 통",
                         "createdAt": "2026-01-01T00:00:00Z", **_sender("buyer@gmail.com")}],
            "paging": {"next": {"after": "CUR"}},
        },
        f"{msgs}|CUR": {
            "results": [{"type": "MESSAGE", "id": "m2", "text": "둘째 통",
                         "createdAt": "2026-01-02T00:00:00Z", **_sender("buyer@gmail.com")}],
        },
    })

    rows = await collect_ticket_history(client, "ticket-1")

    assert [r["external_id"] for r in rows] == ["hubspot:conv:m1", "hubspot:conv:m2"]


@pytest.mark.asyncio
async def test_it_reads_every_thread_not_just_the_first():
    """티켓 하나에 스레드가 여럿입니다 — 실측 60건 중 41건이 2개 이상, 최대 7개.

    첫 스레드만 읽으면 나머지가 통째로 빠집니다. 이것이 「계속 빼먹는다」의 큰 몫이었습니다.
    """
    threads = "/conversations/v3/conversations/threads"
    client = _FakeClient({
        f"{threads}|": {"results": [{"id": "t1"}, {"id": "t2"}, {"id": "t3", "archived": True}]},
        "/conversations/v3/conversations/threads/t1/messages|": {"results": [
            {"type": "MESSAGE", "id": "a", "text": "메일", "createdAt": "2026-01-01T00:00:00Z",
             "channelId": "1002", **_sender("buyer@gmail.com")}]},
        "/conversations/v3/conversations/threads/t2/messages|": {"results": [
            {"type": "MESSAGE", "id": "b", "text": "채팅", "createdAt": "2026-01-02T00:00:00Z",
             "channelId": "1000", **_sender(actor="V-1")}]},
        # 보관된 스레드는 안 읽습니다 — 화면에서 치운 대화를 되살릴 이유가 없습니다.
        "/conversations/v3/conversations/threads/t3/messages|": {"results": [
            {"type": "MESSAGE", "id": "c", "text": "보관됨", "createdAt": "2026-01-03T00:00:00Z"}]},
    })

    rows = await collect_ticket_history(client, "ticket-1")

    assert [r["external_id"] for r in rows] == ["hubspot:conv:a", "hubspot:conv:b"]
    # 채널이 화면에 숫자로 뜨지 않게 말로 옮깁니다.
    assert [r["channel"] for r in rows] == ["이메일", "채팅"]
    assert [r["direction"] for r in rows] == ["inbound", "inbound"]


@pytest.mark.asyncio
async def test_a_truncated_body_is_fetched_in_full():
    """목록이 주는 본문은 잘려 있을 수 있습니다 — 실측 265건 중 46건.

    화면에 잘렸다는 표시가 없어서, 그대로 두면 반쪽짜리 기록이 남습니다(실측 174자 대
    원본 1,670자). **잘렸다고 적혀 있을 때만** 원본을 따로 받습니다 — 매번 받으면 메시지마다
    호출이 한 번씩 더 늡니다.
    """
    threads = "/conversations/v3/conversations/threads"
    msgs = "/conversations/v3/conversations/threads/t1/messages"
    client = _FakeClient({
        f"{threads}|": {"results": [{"id": "t1"}]},
        f"{msgs}|": {"results": [
            {"type": "MESSAGE", "id": "cut", "text": "앞부분만…",
             "truncationStatus": "TRUNCATED_TO_MOST_RECENT_REPLY",
             "createdAt": "2026-01-01T00:00:00Z", **_sender("buyer@gmail.com")},
            {"type": "MESSAGE", "id": "whole", "text": "온전한 본문",
             "createdAt": "2026-01-02T00:00:00Z", **_sender("buyer@gmail.com")},
        ]},
        f"{msgs}/cut/original-content|": {"text": "앞부분만… 그리고 뒤에 이어지는 진짜 본문"},
    })

    rows = await collect_ticket_history(client, "ticket-1")

    assert rows[0]["summary"] == "앞부분만… 그리고 뒤에 이어지는 진짜 본문"
    assert rows[1]["summary"] == "온전한 본문"
    # 안 잘린 메시지에는 원본을 안 부릅니다.
    assert f"{msgs}/whole/original-content|" not in client.calls


@pytest.mark.asyncio
async def test_only_real_messages_become_records():
    """`type != MESSAGE` 인 것(시스템 표시 등)은 대화가 아닙니다."""
    threads = "/conversations/v3/conversations/threads"
    client = _FakeClient({
        f"{threads}|": {"results": [{"id": "t1"}]},
        "/conversations/v3/conversations/threads/t1/messages|": {"results": [
            {"type": "ASSIGNMENT", "id": "sys", "createdAt": "2026-01-01T00:00:00Z"},
            {"type": "MESSAGE", "id": "real", "text": "진짜",
             "createdAt": "2026-01-02T00:00:00Z", **_sender("buyer@gmail.com")},
            {"type": "MESSAGE", "text": "id 가 없으면 못 셉니다",
             "createdAt": "2026-01-03T00:00:00Z"},
        ]},
    })

    rows = await collect_ticket_history(client, "ticket-1")

    assert [r["external_id"] for r in rows] == ["hubspot:conv:real"]


@pytest.mark.asyncio
async def test_records_are_keyed_so_a_rerun_adds_nothing():
    """같은 티켓을 다시 돌려도 같은 열쇠가 나와야 합니다.

    `external_id` 가 HubSpot 메시지 id 라서 「지우고 새로 받기」가 필요 없습니다 — 실패한
    회차를 그냥 다시 돌리면 되고, 이관 0106 이 그 칸에 유니크를 걸어 두 번째 방어선을 둡니다.
    """
    threads = "/conversations/v3/conversations/threads"
    pages = {
        f"{threads}|": {"results": [{"id": "t1"}]},
        "/conversations/v3/conversations/threads/t1/messages|": {"results": [
            {"type": "MESSAGE", "id": "m1", "text": "한 통",
             "createdAt": "2026-01-01T00:00:00Z", **_sender("buyer@gmail.com")}]},
    }

    first = await collect_ticket_history(_FakeClient(pages), "ticket-1")
    again = await collect_ticket_history(_FakeClient(pages), "ticket-1")

    assert [r["external_id"] for r in first] == [r["external_id"] for r in again]


def test_a_form_submission_is_always_inbound():
    """폼은 고객이 우리에게 내는 것입니다 — 채널이 곧 방향입니다.

    실측으로 잡힌 버그입니다: 티켓 330705398519 의 폼에 제출자 주소가
    `mina14@estsoft.com` 이라, 주소 규칙만으로는 「우리가 보낸 것」이 됐습니다. 우리 직원이
    고객을 대신해 폼을 넣는 일이 실제로 있습니다.
    """
    submitted_by_us = {"channelId": "1003", **_sender("mina14@estsoft.com")}
    assert classify_direction(submitted_by_us) == "inbound"
    # 이메일 채널에서는 그대로 주소 규칙입니다.
    assert classify_direction({"channelId": "1002", **_sender("mina14@estsoft.com")}) == "outgoing"


def test_the_importer_never_touches_last_incoming_at():
    """**그 칸은 워크북 append 대기열의 방아쇠입니다.**

    `sheet_sync.sync_pending_inbound_rows` 가 매 폴러 회차마다
    `sheet_inbound_row IS NULL AND last_incoming_at IS NOT NULL` 을 고릅니다. 백필이 만든
    300건 넘는 티켓은 그 칸이 **일부러** NULL 인데(`hubspot_backfill` docstring), 수집기가
    채우면 그 전부가 영업팀 공용 워크북에 한꺼번에 실려 나갑니다 — 운영은 시트 쓰기가
    켜져 있습니다.

    한 번 채웠다가 지운 코드라, 다시 들어오지 않게 여기서 막습니다.
    """
    import pathlib

    source = pathlib.Path("src/agents/ticket_history.py").read_text(encoding="utf-8")
    body = source[source.index("def _store("):]
    assert "conversation.last_incoming_at" not in body


# --------------------------------------------------------------------------- #
# 개인 사서함 메일 붙이기 — 잘못 붙이면 아무도 모릅니다
# --------------------------------------------------------------------------- #
def test_attaching_personal_email_is_reachable_and_guarded():
    """**부르는 곳이 없으면 없는 기능입니다.**

    이 기능은 한 번 「구현했다」고 커밋해 놓고 부르는 곳이 하나도 없었습니다 — 라우트도,
    폴러 단계도, 화면 버튼도. 배포해도 아무 일이 안 일어나는 상태였습니다.

    그리고 **티켓이 하나일 때만** 붙인다는 규칙이 주석에만 있고 코드에는 없었습니다.
    그대로 두면 티켓이 둘인 연락처의 메일이 엉뚱한 티켓에 붙는데, 그건 허브스팟에 쓰는
    동작이라 되돌리기 전까지 남고 아무도 잘못을 눈치채지 못합니다.
    """
    import pathlib

    from src.api.routes import customer_ops

    paths = [getattr(r, "path", "") for r in customer_ops.router.routes]
    assert "/internal/tickets/{conversation_id}/attach-personal-emails" in paths

    source = pathlib.Path("src/agents/ticket_history.py").read_text(encoding="utf-8")
    body = source[source.index("async def attach_personal_emails("):]
    # 규칙이 코드에 있어야 합니다 — 주석만으로는 안 됩니다.
    assert "ticket_count != 1" in body
    # 그리고 왜 안 붙였는지 돌려줘야 합니다.
    assert '"skipped"' in body


def test_the_operator_can_see_how_far_the_import_got():
    """**진행 상황이 화면에 없으면 「아직 안 왔다」와 「안 돌고 있다」가 구별되지 않습니다.**

    이 저장소가 옛 백필에서 겪은 그대로입니다(CLAUDE.md: 「누른 직후에는 아무 일도 안
    일어난 것처럼 보인다」). 로그는 30분이면 스크롤 밖이고, 운영자는 로그를 볼 수 없습니다.

    다 끝나면 화면은 아무 말도 안 합니다 — 조용한 것이 정상 상태입니다(「환율 없는 계약
    수」가 쓰는 규칙과 같습니다).
    """
    import pathlib

    from src.api.routes import ui_api

    paths = [getattr(r, "path", "") for r in ui_api.router.routes]
    assert "/api/ui/ticket-history/progress" in paths

    screen = pathlib.Path("frontend/src/screens/Customers.tsx").read_text(encoding="utf-8")
    assert "ticket-history/progress" in screen
    # 남은 것이 없으면 그리지 않습니다.
    assert "sync.remaining > 0" in screen


# --------------------------------------------------------------------------- #
# 같은 메일이 세 번 — 한 메일, 객체 셋 (2026-09-07 운영자 지적)
# --------------------------------------------------------------------------- #
# 운영자가 본 것: 2026-09-01 11:05 에 나간 회신 한 통이 「이 티켓의 기록」에 세 줄로.
# 셋의 출처가 다 다릅니다 — 발송이 남긴 `messages` 행(말풍선), CRM 이메일 수집,
# 스레드 수집. 앞의 둘을 잇는 열쇠가 없어서 서로를 못 알아봤습니다.
def test_the_crm_email_and_the_thread_message_share_one_key():
    """CRM 이메일과 스레드 메시지를 잇는 **유일한** 열쇠입니다.

    `hs_email_message_id` 안에 `-cv-<스레드 메시지 id>` 가 박혀 있습니다(실측 발신 40건
    중 39건 일치, 나머지 1건은 개인 사서함 메일이라 스레드 쌍둥이가 없습니다). 이 칸을
    안 물어보던 동안 두 수집기가 각자의 id 로 같은 메일을 두 번 저장했습니다.
    """
    from src.integrations.hubspot import conversation_message_id

    assert conversation_message_id(
        "facsimile-3-cv-d0945b6b-6462-4558-ab05-0eacc94f9530"
        "@facsimile.hubspot-networks.net"
    ) == "d0945b6b-6462-4558-ab05-0eacc94f9530"
    # 스레드를 안 지난 메일에는 그 조각이 없습니다 — 그때는 옛 열쇠로 남아야 합니다.
    assert conversation_message_id("<CAF=abc@mail.gmail.com>") is None
    assert conversation_message_id(None) is None


def test_the_old_crm_row_is_folded_into_the_thread_row():
    """열쇠를 맞추기 **전에** 쌓인 두 벌은 수집기가 돌면서 치웁니다.

    이관으로 한 번 돌고 끝내지 않는 이유: 수집기는 티켓을 끝없이 한 바퀴씩 돌므로
    (`sync_pending_ticket_history`) 여기 두면 한 바퀴 안에 전부 정리되고 그 뒤로도
    유지됩니다. 이관은 그때 아직 안 들어와 있던 줄을 영영 못 만납니다.

    한 줄 요약은 **살려서 옮깁니다** — 백필이 이미 만들어 둔 것이라, 지우면 그 티켓만
    다시 모델을 부릅니다.
    """
    from datetime import datetime, timezone
    from unittest.mock import patch

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from src.agents import ticket_history
    from src.db.base import Base
    from src.db.models import Contact, Conversation, CustomerInteraction

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    at = datetime(2026, 9, 1, 2, 5, 38, 979000, tzinfo=timezone.utc)
    with factory() as session:
        contact = Contact(
            normalized_email="buyer@acme.com", email="buyer@acme.com",
            full_name="Acme Buyer",
        )
        session.add(contact)
        session.flush()
        conv = Conversation(contact_id=contact.id, stage="negotiation")
        session.add(conv)
        session.flush()
        session.add_all([
            # CRM 쪽 줄 — 한 줄 요약은 이쪽에만 있습니다.
            CustomerInteraction(
                contact_id=contact.id, conversation_id=conv.id,
                external_id="hubspot:email:395992827622", channel="이메일",
                direction="outgoing", summary="Thanks for reaching out…",
                context="견적 안내와 미팅 링크를 보냈습니다.", happened_at=at,
            ),
            # 스레드 쪽 줄 — 같은 메일, 같은 초. 본문은 이쪽이 더 완전합니다.
            CustomerInteraction(
                contact_id=contact.id, conversation_id=conv.id,
                external_id="hubspot:conv:d0945b6b", channel="이메일",
                direction="outgoing", summary="Thanks for reaching out…",
                context=None, happened_at=at.replace(microsecond=0),
            ),
        ])
        session.commit()
        ids = (conv.id, contact.id)

    with patch.object(ticket_history, "SessionLocal", factory):
        ticket_history._store(ids[0], ids[1], [])

    with factory() as session:
        rows = session.query(CustomerInteraction).all()
        assert [r.external_id for r in rows] == ["hubspot:conv:d0945b6b"], "한 줄만 남습니다"
        assert rows[0].context == "견적 안내와 미팅 링크를 보냈습니다.", "요약은 옮겨 탑니다"


# --------------------------------------------------------------------------- #
# 고객이 답장하면 협의 중으로 (2026-09-07 운영자 지시)
# --------------------------------------------------------------------------- #
def _reply_row(direction: str, when):
    return {"external_id": f"hubspot:conv:{direction}", "channel": "이메일",
            "direction": direction, "subject": None, "summary": "…",
            "handler": None, "happened_at": when}


def test_a_new_customer_reply_moves_contacted_to_negotiating():
    """운영자 지시: 「보내는 기준이 아니고 그 사람한테 답변이 오면 negotiating 으로 가는 것」.

    이 사실을 아는 자리가 수집기뿐입니다 — 접수(폴러·웹훅)는 **New 티켓만** 보므로
    Contacted 로 넘어간 티켓에 온 답장은 그 문을 아예 안 지납니다.
    """
    from datetime import datetime, timedelta, timezone

    from src.agents.ticket_history import reply_advances_stage

    seen = datetime(2026, 9, 5)
    later = (seen + timedelta(hours=1)).replace(tzinfo=timezone.utc)
    earlier = (seen - timedelta(days=30)).replace(tzinfo=timezone.utc)

    assert reply_advances_stage("meeting_link_sent", seen, [_reply_row("inbound", later)])

    # **첫 수집(도장 NULL)에서는 안 옮깁니다.** 수집기는 티켓을 끝없이 한 바퀴씩 도는데
    # 첫 바퀴에는 그 티켓의 과거가 통째로 들어옵니다 — 기준이 없으면 몇 달 전 답장 하나로
    # Contacted 에 서 있던 티켓 수백 건이 한 회차에 옮겨지고, 허브스팟과 영업팀 워크북까지
    # 나갑니다. 잃는 것은 없습니다: 다음 바퀴부터 정상으로 판정됩니다.
    assert not reply_advances_stage("meeting_link_sent", None, [_reply_row("inbound", later)])
    # 마지막으로 본 때보다 오래된 것은 새 답장이 아닙니다 — 매 회차 다시 옮기게 됩니다.
    assert not reply_advances_stage("meeting_link_sent", seen, [_reply_row("inbound", earlier)])
    # 우리가 보낸 것은 이미 Contacted 를 만든 사건입니다.
    assert not reply_advances_stage("meeting_link_sent", seen, [_reply_row("outgoing", later)])
    # New 는 우리가 아직 답을 안 한 자리입니다 — 그 티켓에는 검토할 초안이 대기 중입니다.
    assert not reply_advances_stage("new", seen, [_reply_row("inbound", later)])
    # 이미 지나간 단계는 되돌리지 않습니다(발송 워커가 「앞으로만 간다」로 막은 그 사고).
    for stage in ("negotiation", "won", "closed_lost", "closed"):
        assert not reply_advances_stage(stage, seen, [_reply_row("inbound", later)])


def test_the_reply_advance_never_deletes_a_draft():
    """운영자가 「메일 발송」으로 열어 둔 후속 초안이 고객 답장 한 통에 사라지면 안 됩니다.

    초안을 지우는 규칙의 근거는 「단계가 넘어갔다는 것은 **답이 다른 경로로 나갔다**는
    뜻」인데, 이 전환은 정반대입니다 — 우리가 답한 것이 아니라 고객이 쓴 것이라 지울 근거가
    없습니다. 그리고 실제로 사고가 납니다: 후속 초안은 Contacted 티켓에 서고, 그 티켓이
    바로 이 전환의 출발점입니다.
    """
    import pathlib

    source = pathlib.Path("src/agents/ticket_history.py").read_text(encoding="utf-8")
    body = source[source.index("async def _advance_on_customer_reply"):]
    assert "retire_drafts=False" in body


# --------------------------------------------------------------------------- #
# 대화 웹훅 (2026-09-08, 운영자가 비공개 앱에서 구독을 켰습니다)
# --------------------------------------------------------------------------- #
def test_a_conversation_event_puts_that_customers_tickets_at_the_front():
    """**고객이 답장하면 웹훅이 옵니다** — 오래 「구독이 없다」로 알고 있었는데, 없던 것은
    플랫폼이 아니라 우리 포털 설정이었습니다.

    이 이벤트가 하는 일은 둘입니다: **서비스를 깨우고**(무료 플랜은 15분 무접속이면
    잡니다), 그 티켓을 수집 큐 맨 앞으로 옮기는 것. 대화를 받아오는 일은 여기서 안
    합니다 — 웹훅이 느리면 허브스팟이 배치를 통째로 재전송합니다.

    **스레드에는 티켓 id 가 없습니다**(실측: 키가 `associatedContactId` · `inboxId` …).
    그래서 연락처로 되짚고 그 사람의 티켓을 전부 표시합니다 — 어느 티켓의 스레드인지는
    수집기가 실제로 받아 보면서 가립니다.
    """
    import pathlib

    source = pathlib.Path("src/api/webhook.py").read_text(encoding="utf-8")
    assert "conversation.newMessage" in source
    block = source[source.index("def _refresh_conversation"):]
    block = block[: block.index("\n\ndef ")]
    assert "mark_ticket_history_stale" in block, "큐 맨 앞으로 올립니다"
    # 웹훅 안에서 대화를 통째로 받아오면 안 됩니다 — 느리면 배치가 재전송됩니다.
    assert "collect_ticket_history" not in block


def test_the_collector_is_a_queue_not_a_round_robin():
    """**같은 일을 하는 기계를 둘로 두지 않습니다** (2026-09-08 운영자 지적: 「같은 행동을
    하는 걸 굳이 여러 개로 나눠야 하나… 인수인계했을 때 혼란만 줄 것 같은데」).

    수집기는 이제 **대기열만** 비웁니다 — 조건이  하나입니다.
    NULL 이 되는 길이 둘이지만(한 번도 안 받았다 / 웹훅이 도장을 지웠다) 둘 다 같은
    말이라 코드에는 갈래가 없습니다.

    그래서 **평소에는 아무 일도 안 합니다** — 대기열이 비면 허브스팟 왕복이 0 이고
    메모리도 안 씁니다. 무료로 운영하는 동안 이게 가장 큰 절약입니다.
    """
    import pathlib

    from src.agents.ticket_history import TICKETS_PER_SWEEP

    source = pathlib.Path('src/agents/ticket_history.py').read_text(encoding='utf-8')
    assert 'Conversation.history_synced_at.is_(None),' in source, '대기열은 NULL 하나'
    # 「가장 오래된 것부터 다시」가 남아 있으면 순환이 그대로라는 뜻입니다.
    assert 'Conversation.history_synced_at.asc()' not in source
    # 할 일이 있을 때만 무는 상한이라 작게 둘 이유가 없습니다.
    assert TICKETS_PER_SWEEP >= 8
