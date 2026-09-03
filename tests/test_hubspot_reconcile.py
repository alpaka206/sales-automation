"""The 최신화 button on 운영 로그.

HubSpot and our copy drift: a ticket answered or deleted there while our queue still
shows an unsent draft, so the operator is asked to send a reply the customer already has
— or one for a thread that no longer exists.

The property worth pinning is not that it syncs. It is that a 404 alone never retires
anyone's draft.
"""

from __future__ import annotations

import httpx
import pytest

from src.db.models import Contact, Conversation, Message
from src.db.session import SessionLocal


@pytest.fixture
def waiting_draft():
    """One thread holding an unsent draft against a ticket id."""
    with SessionLocal() as session:
        contact = Contact(email="drift@example.com", normalized_email="drift@example.com",
                          full_name="드리프트", company="드리프트 주식회사")
        session.add(contact)
        session.flush()
        contact_id = contact.id
        conversation = Conversation(
            contact_id=contact_id, stage="new", hubspot_ticket_id="99999999"
        )
        session.add(conversation)
        session.flush()
        conversation_id = conversation.id
        message = Message(
            conversation_id=conversation_id, direction="outgoing",
            status="pending_approval", subject="RE: 문의", body="초안",
        )
        session.add(message)
        session.flush()
        message_id = message.id
        session.commit()
        ids = (contact_id, conversation_id, message_id)

    yield ids

    # Child rows first, by hand: the ORM nullifies the FK on a bare parent delete and
    # every one of these columns is NOT NULL. The confirmed pass also writes a progress
    # row, so that one has to go too.
    from sqlalchemy import delete as sql_delete

    from src.db.models import ConversationProgress

    contact_id, conversation_id, _message_id = ids
    with SessionLocal() as session:
        session.execute(
            sql_delete(ConversationProgress).where(
                ConversationProgress.conversation_id == conversation_id
            )
        )
        session.execute(sql_delete(Message).where(Message.conversation_id == conversation_id))
        session.execute(sql_delete(Conversation).where(Conversation.id == conversation_id))
        session.execute(sql_delete(Contact).where(Contact.id == contact_id))
        session.commit()


GONE_TICKET = "99999999"


def _hubspot_says_gone(monkeypatch):
    """This one ticket is missing, the way a deleted one is — and the way an id from
    another portal, or one we recorded wrong, also is.

    Only this one: both the batch check and the per-ticket lookup answer for whatever
    else the shared test database happens to hold, and "everything is gone" would have
    the confirmed pass delete the other tests' rows along with ours.
    """
    from src.agents import hubspot_reconcile

    class Gone:
        def existing_ticket_ids_sync(self, ticket_ids):
            return {str(t) for t in ticket_ids} - {GONE_TICKET}

        def get_ticket_sync(self, ticket_id):
            request = httpx.Request("GET", f"https://api.hubspot.com/tickets/{ticket_id}")
            raise httpx.HTTPStatusError(
                "not found", request=request, response=httpx.Response(404, request=request)
            )

    monkeypatch.setattr(hubspot_reconcile, "HubSpotClient", lambda: Gone())
    monkeypatch.setattr(
        "src.agents.inbound_poller.reconcile_ticket_stages_once", lambda: 0
    )


def test_a_404_alone_never_retires_a_draft(monkeypatch, waiting_draft):
    """404 is how a DELETED ticket looks. It is also how a ticket id belonging to another
    portal looks, and a backfilled row, and an id we stored wrong. Acting on it
    unprompted is how a button meant to tidy the queue throws away an answer nobody had
    sent yet — which is exactly what the first version of this did on its first run.
    """
    from src.agents.hubspot_reconcile import reconcile_with_hubspot

    _hubspot_says_gone(monkeypatch)
    _contact_id, _conversation_id, message_id = waiting_draft

    report = reconcile_with_hubspot()

    assert report["deleted"] == 1, "it still has to SAY the ticket is missing"
    assert report["retired"] == 0, "but it must not act on that alone"
    assert report["applied"] is False
    with SessionLocal() as session:
        assert session.get(Message, message_id).status == "pending_approval"


def test_the_confirmed_pass_deletes_the_thread(monkeypatch, waiting_draft):
    """Once the operator has seen the count and said yes. The ticket was deleted in
    HubSpot, so the thread goes with it."""
    from src.agents.hubspot_reconcile import reconcile_with_hubspot

    _hubspot_says_gone(monkeypatch)
    _contact_id, conversation_id, message_id = waiting_draft

    report = reconcile_with_hubspot(apply=True)

    assert report["retired"] == 1
    with SessionLocal() as session:
        assert session.get(Message, message_id) is None
        assert session.get(Conversation, conversation_id) is None


def test_a_draft_that_was_approved_can_still_be_deleted(waiting_draft):
    """**승인 기록이 붙은 초안도 지워집니다** (2026-09-03, 운영자 500 재현).

    `approvals` 는 모델에 `ondelete="CASCADE"` 라고 적혀 있지만 **운영 DB 의 제약에는 그것이
    없습니다** — 그 표를 만든 옛 마이그레이션이 안 걸었고, 모델의 선언은 이미 만들어진 제약을
    바꾸지 않습니다. 그래서 「허브스팟 최신화」가 승인을 한 번이라도 받은 대화를 만나면
    `ForeignKeyViolation ... still referenced from table "approvals"` 로 죽었고, **그 한 건
    때문에 최신화 전체가 아무 일도 못 했습니다.**

    이 테스트는 SQLite 로 도는데 거기서는 제약이 안 걸려 실패를 재현하지 못합니다. 그래서
    **행이 실제로 사라지는지**를 봅니다 — 남겨 두는 구현으로 되돌리면 여기서 걸립니다.
    """
    from src.agents.hubspot_reconcile import delete_conversation
    from src.db.models import Approval

    _contact_id, conversation_id, message_id = waiting_draft
    with SessionLocal() as session:
        session.add(Approval(message_id=message_id, approver="운영자", action="approve"))
        session.commit()

    delete_conversation(conversation_id, "99999999")

    with SessionLocal() as session:
        assert session.get(Message, message_id) is None
        assert session.query(Approval).filter_by(message_id=message_id).count() == 0


def test_deleting_a_thread_never_takes_the_customer_or_the_money(waiting_draft):
    """The blast radius, asserted rather than assumed. A ticket deleted in HubSpot says
    nothing about whether the customer is real or whether they signed something."""
    from decimal import Decimal

    from src.agents.hubspot_reconcile import delete_conversation
    from src.db.models import ContractRecord, CustomerInteraction

    contact_id, conversation_id, _message_id = waiting_draft
    with SessionLocal() as session:
        session.add(ContractRecord(contact_id=contact_id, conversation_id=conversation_id,
                                   status="active", amount=Decimal("1000"), currency="USD"))
        session.add(CustomerInteraction(contact_id=contact_id, conversation_id=conversation_id,
                                        channel="meeting", summary="미팅 요약"))
        session.commit()

    delete_conversation(conversation_id, "99999999")

    with SessionLocal() as session:
        assert session.get(Conversation, conversation_id) is None
        assert session.get(Contact, contact_id) is not None, "the person is still real"
        contract = session.query(ContractRecord).filter_by(contact_id=contact_id).one()
        assert contract.amount == Decimal("1000"), "a contract is never deleted for this"
        assert contract.conversation_id is None, "just detached"
        notes = session.query(CustomerInteraction).filter_by(contact_id=contact_id).all()
        assert any(n.summary == "미팅 요약" for n in notes), "the meeting still happened"
        # **나가지 않은 초안은 안 옮겨집니다** (2026-08-19 운영자 지시). 이 픽스처의 메일은
        # 검토 대기 상태라 고객이 본 적이 없습니다 — 히스토리에 넣으면 나중에 읽는 사람이
        # 보낸 적 없는 답변을 보낸 것으로 셉니다.
        assert not any(n.summary == "초안" for n in notes)


def test_a_contact_with_nothing_left_goes_too(waiting_draft):
    """수주도 다른 티켓도 없으면 **연락처까지** 지웁니다 (2026-08-19 운영자 지시).

    안 그러면 대화도 계약도 메모도 없는 빈 연락처가 리드 히스토리 목록에 계속 서 있습니다 —
    운영 DB 에 실제로 그런 행이 있었습니다. 옮겨 담을 메일도 여기서는 남기지 않습니다:
    남길 사람이 없는데 히스토리만 남기면 그것이 곧 빈 연락처를 남기는 이유가 됩니다.
    """
    from src.agents.hubspot_reconcile import delete_conversation
    from src.db.models import CustomerInteraction

    contact_id, conversation_id, _message_id = waiting_draft

    delete_conversation(conversation_id, "99999999")

    with SessionLocal() as session:
        assert session.get(Conversation, conversation_id) is None
        assert session.get(Contact, contact_id) is None, "빈 연락처는 남지 않습니다"
        assert session.query(CustomerInteraction).filter_by(contact_id=contact_id).count() == 0


def test_a_second_ticket_keeps_the_contact_and_only_the_mail_that_went_out(waiting_draft):
    """다른 티켓이 있으면 연락처는 남고, **실제로 오간 메일만** 히스토리로 옮겨집니다.

    받은 문의는 무조건 남깁니다 — 고객이 실제로 보낸 것입니다. 나간 답변은 발송된 것만
    남깁니다. 검토 대기로 있던 초안은 우리 안에서만 있던 문서라, 옮기면 「이 고객과 오간
    것」의 수가 틀려집니다.
    """
    from src.agents.hubspot_reconcile import delete_conversation
    from src.db.models import CustomerInteraction

    contact_id, conversation_id, _message_id = waiting_draft
    with SessionLocal() as session:
        session.add(
            Conversation(contact_id=contact_id, stage="new", hubspot_ticket_id="88888888")
        )
        session.add(
            Message(conversation_id=conversation_id, direction="inbound",
                    status="received", subject="문의", body="고객이 보낸 문의")
        )
        session.add(
            Message(conversation_id=conversation_id, direction="outgoing",
                    status="sent", subject="RE: 문의", body="나간 답변")
        )
        session.commit()

    delete_conversation(conversation_id, "99999999")

    with SessionLocal() as session:
        assert session.get(Contact, contact_id) is not None
        moved = session.query(CustomerInteraction).filter_by(contact_id=contact_id).all()
        assert sorted(n.summary for n in moved) == ["고객이 보낸 문의", "나간 답변"]
        assert all(n.conversation_id is None for n in moved), "가리킬 대화가 곧 사라집니다"
        session.query(Conversation).filter_by(hubspot_ticket_id="88888888").delete()
        session.query(CustomerInteraction).filter_by(contact_id=contact_id).delete()
        session.commit()


def test_a_deletion_webhook_removes_the_thread(monkeypatch, waiting_draft):
    """HubSpot tells us when a ticket is deleted and nothing used to listen, so the draft
    sat in 발송 대기 waiting on a thread that had stopped existing."""
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.common.config import settings

    _contact_id, conversation_id, _message_id = waiting_draft
    with (
        patch.object(settings, "HUBSPOT_WEBHOOK_SECRET", ""),
        patch.object(settings, "HUBSPOT_WEBHOOK_REQUIRE_SIGNATURE", False),
        TestClient(app) as client,
    ):
        response = client.post(
            "/webhooks/hubspot",
            json=[{"subscriptionType": "ticket.deletion", "objectId": 99999999,
                   "eventId": 1, "occurredAt": 1}],
            headers={"X-Internal-Token": settings.INTERNAL_API_TOKEN},
        )
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["status"] == "deleted"
    with SessionLocal() as session:
        assert session.get(Conversation, conversation_id) is None


def test_a_thread_that_leaves_takes_its_workbook_row_with_it(monkeypatch, waiting_draft):
    """콘솔에서 사라진 문의는 시트에서도 사라져야 합니다 (2026-08-19, 운영자 지시).

    안 그러면 같은 문의를 콘솔은 없다고 하고 워크북은 있다고 해서, 두 화면의 건수가 영영
    안 맞습니다 — 그 어긋남은 아무도 고칠 수 없습니다. 시트에서 행을 찾는 자연키는
    `sheet_client_id` 이고, 대화를 지우면 그 값도 같이 사라지므로 **지우기 전에** 들고
    나와야 합니다. 그 순서가 이 검사의 요점입니다.
    """
    from src.agents import hubspot_reconcile
    from src.integrations import google_sheets

    _contact_id, conversation_id, _message_id = waiting_draft
    with SessionLocal() as session:
        conversation = session.get(Conversation, conversation_id)
        conversation.sheet_client_id = 1234
        session.commit()

    asked: list[int] = []
    monkeypatch.setattr(google_sheets, "delete_inbound_row", lambda cid: asked.append(cid) or True)

    hubspot_reconcile.delete_conversation(conversation_id, "99999999")
    assert asked == [1234]


def test_a_thread_with_no_workbook_row_asks_the_sheet_for_nothing(monkeypatch, waiting_draft):
    """워크북에 붙은 적 없는 문의(`sheet_client_id` 가 비어 있음)는 시트를 부르지 않습니다.
    Client ID 없이 부르면 그 호출은 아무 행도 못 찾고 실패 로그만 남깁니다."""
    from src.agents import hubspot_reconcile
    from src.integrations import google_sheets

    _contact_id, conversation_id, _message_id = waiting_draft
    calls: list[int] = []
    monkeypatch.setattr(google_sheets, "delete_inbound_row", lambda cid: calls.append(cid) or True)

    hubspot_reconcile.delete_conversation(conversation_id, "99999999")
    assert calls == []


def test_a_deleted_ticket_is_never_queued_as_inbound_work():
    """Fetching a ticket that no longer exists is not work. Mapping the subscription type
    would have enqueued exactly that."""
    from src.api import webhook

    assert "ticket.deletion" not in webhook._HUBSPOT_SUBSCRIPTION_MAP


def test_an_auth_failure_is_not_a_deleted_ticket(monkeypatch, waiting_draft):
    """401 means the token is wrong. Reporting that as "this ticket was deleted" would
    invite the operator to confirm away every draft they have."""
    from src.agents import hubspot_reconcile

    class Unauthorized:
        def existing_ticket_ids_sync(self, ticket_ids):
            raise RuntimeError("tickets batch read failed (401)")

        def get_ticket_sync(self, ticket_id):
            request = httpx.Request("GET", "https://api.hubspot.com/tickets/1")
            raise httpx.HTTPStatusError(
                "unauthorized", request=request,
                response=httpx.Response(401, request=request),
            )

    monkeypatch.setattr(hubspot_reconcile, "HubSpotClient", lambda: Unauthorized())
    monkeypatch.setattr("src.agents.inbound_poller.reconcile_ticket_stages_once", lambda: 0)

    report = hubspot_reconcile.reconcile_with_hubspot(apply=True)

    assert report["deleted"] == 0
    assert report["retired"] == 0
    # 그리고 **왜** 아무것도 안 나왔는지 화면에 적힙니다. 조용히 건너뛰면 「정리할 항목
    # 없음」이 정말 없는 것과 보지 못한 것 두 가지를 뜻하고, 운영자에게는 똑같이 보입니다.
    assert report["error"] and "401" in report["error"]


def test_only_threads_holding_an_answer_are_asked_about_their_stage(monkeypatch, waiting_draft):
    """A thread we already replied to can be out of date about its STAGE without anyone
    being asked to do the wrong thing, and the poller sweeps those anyway. Existence is
    the other half and is checked for everything — see the test below."""
    from src.agents.hubspot_reconcile import _all_ticket_ids, _open_ticket_ids

    _contact_id, conversation_id, message_id = waiting_draft
    assert (conversation_id, GONE_TICKET) in _open_ticket_ids()

    with SessionLocal() as session:
        session.get(Message, message_id).status = "sent"
        session.commit()
    assert (conversation_id, GONE_TICKET) not in _open_ticket_ids()
    assert (conversation_id, GONE_TICKET) in _all_ticket_ids()


def test_a_deleted_ticket_leaves_the_board_with_no_draft_to_find_it_by(
    monkeypatch, waiting_draft
):
    """The one the operator hit: a ticket deleted in HubSpot kept its card on the
    파이프라인 board.

    Three things look for absence and none of them covered this. The deletion webhook
    fires once — if it was never subscribed to, or never arrived, it never comes again.
    The 10-minute poller sweeps the tickets HubSpot HAS, so a ticket it no longer has
    appears in no sweep. And 최신화 asked only about threads holding an unsent draft,
    which an answered 협상중 or Won card has none of.
    """
    from src.agents import hubspot_reconcile

    _contact_id, conversation_id, message_id = waiting_draft
    with SessionLocal() as session:
        session.get(Message, message_id).status = "sent"
        session.commit()

    _hubspot_says_gone(monkeypatch)
    report = hubspot_reconcile.reconcile_with_hubspot(apply=True)

    assert report["deleted"] == 1
    with SessionLocal() as session:
        assert session.get(Conversation, conversation_id) is None


def test_a_thread_past_new_keeps_its_thread_but_loses_the_unsent_draft(monkeypatch, waiting_draft):
    """After 최신화 only New keeps a waiting draft. Anything past it was answered in
    HubSpot, so our draft is a reply the customer already has.

    Retired, not deleted, and the difference is the point: the ticket still EXISTS. Only
    a ticket that is gone from HubSpot earns a delete.
    """
    from src.agents import hubspot_reconcile
    from src.agents.hubspot_backfill import B2B_PIPELINE_ID

    class Negotiating:
        def existing_ticket_ids_sync(self, ticket_ids):
            return {str(t) for t in ticket_ids}

        def get_ticket_sync(self, ticket_id):
            class Ticket:
                id = ticket_id
                pipeline_stage = "negotiating-stage-id"
                # 아직 **우리** 파이프라인 안입니다. 밖으로 나간 티켓은 이 패스가 지웁니다
                # (우리 관할이 아니게 된 문의) — 이 검사는 그 경우가 아닙니다.
                pipeline = B2B_PIPELINE_ID
            return Ticket()

    monkeypatch.setattr(hubspot_reconcile, "HubSpotClient", lambda: Negotiating())
    monkeypatch.setattr("src.agents.inbound_poller.reconcile_ticket_stages_once", lambda: 0)
    monkeypatch.setattr("src.agents.stage_sync.local_stage_for", lambda _id: "negotiation")
    monkeypatch.setattr("src.agents.stage_sync.sync_stage_from_hubspot",
                        lambda *a, **k: None)

    _contact_id, conversation_id, message_id = waiting_draft
    dry = hubspot_reconcile.reconcile_with_hubspot()
    assert dry["stale"] == 1 and dry["retired"] == 0

    hubspot_reconcile.reconcile_with_hubspot(apply=True)
    with SessionLocal() as session:
        assert session.get(Message, message_id) is None, "나가지 않은 초안은 지웁니다"
        assert session.get(Conversation, conversation_id) is not None, "the ticket exists"


def test_the_one_off_purge_takes_only_the_truly_empty(tmp_path) -> None:
    """이관 0078 — 대화·메모·계약·수주가 넷 다 없는 연락처만 지웁니다.

    하나라도 있으면 남깁니다. 「빈 껍데기를 치운다」와 「고객을 지운다」는 한 글자 차이이고,
    그 경계가 이 검사입니다.
    """
    import importlib
    from decimal import Decimal

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as PlainSession

    from src.db.base import Base
    from src.db.models import Client, ContractRecord, CustomerInteraction

    engine = create_engine(f"sqlite:///{tmp_path}/purge.db")
    Base.metadata.create_all(engine)
    with PlainSession(engine) as session:
        keep_ids = {}
        for name in ("빈사람", "대화있음", "메모있음", "계약있음", "수주고객"):
            person = Contact(normalized_email=f"{name}@example.com", full_name=name)
            session.add(person)
            session.flush()
            keep_ids[name] = person.id
        session.add(Conversation(contact_id=keep_ids["대화있음"], stage="new"))
        session.add(
            CustomerInteraction(contact_id=keep_ids["메모있음"], channel="meeting", summary="미팅")
        )
        session.add(
            ContractRecord(
                contact_id=keep_ids["계약있음"], status="active",
                amount=Decimal("1"), currency="USD",
            )
        )
        session.add(
            Client(client_id=1001, company="수주사", contact_id=keep_ids["수주고객"])
        )
        session.commit()

    importlib.import_module("src.db.migrations.0078_empty_contacts_leave_the_lead_history").up(engine)

    with PlainSession(engine) as session:
        left = {c.full_name for c in session.query(Contact).all()}
        assert left == {"대화있음", "메모있음", "계약있음", "수주고객"}
