"""Tests for HubSpot inbound_status property updates."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.agents.inbound import InboundAgent
from src.api.main import app
from src.common.config import settings
from src.db.models import Contact, CustomerInteraction, CustomerProfile


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _auth_headers() -> dict[str, str]:
    return {"X-Internal-Token": settings.INTERNAL_API_TOKEN}


@pytest.fixture(autouse=True)
def _disable_approval_token_and_send_worker():
    with (
        patch.object(settings, "APPROVAL_REQUIRE_TOKEN", False),
        patch.object(settings, "SEND_WORKER_ENABLED", False),
    ):
        yield


# ---------- InboundAgent sets "analyzed" ----------


@patch.object(settings, "HUBSPOT_UPDATE_CONTACT_INBOUND_STATUS", True)
@patch("src.agents.inbound.InboundAgent._finalize_draft")
@patch(
    "src.agents.inbound.InboundAgent._persist_placeholder",
        return_value=(1, 1, False),
)
@patch("src.agents.inbound.InboundAgent._draft_reply")
@patch("src.agents.inbound.InboundAgent._classify")
@patch("src.agents.inbound.InboundAgent._fetch_contact")
@patch("src.agents.inbound.notify_approval_once")
def test_handle_sets_analyzed_status(
    mock_notify, mock_fetch, mock_classify, mock_draft, mock_placeholder, mock_finalize
):
    """After classification, inbound_status should be updated to 'analyzed'."""
    mock_fetch.return_value = {
        "object_id": "700",
        "email": "x@test.com",
        "last_message": "I want to buy your product",
    }

    mock_classify_result = MagicMock()
    mock_classify_result.category = "purchase_inquiry"
    mock_classify.return_value = mock_classify_result

    mock_draft_result = MagicMock()
    mock_draft_result.subject = "Re: Inquiry"
    mock_draft_result.body = "Thank you"
    mock_draft.return_value = mock_draft_result

    hs_mock = MagicMock()
    agent = InboundAgent.__new__(InboundAgent)
    agent.llm = MagicMock()
    agent.hubspot = hs_mock

    from src.agents.inbound import _processed

    _processed.discard("700:None")

    agent.handle({"event_type": "contact.creation", "object_id": "700"})

    hs_mock.update_inbound_status_sync.assert_called_once_with("700", "analyzed")


@patch("src.agents.inbound.InboundAgent._finalize_draft")
@patch(
    "src.agents.inbound.InboundAgent._persist_placeholder",
        return_value=(2, 2, False),
)
@patch("src.agents.inbound.InboundAgent._draft_reply")
@patch("src.agents.inbound.InboundAgent._classify")
@patch("src.agents.inbound.InboundAgent._fetch_contact")
@patch("src.agents.inbound.notify_approval_once")
def test_handle_continues_on_status_update_failure(
    mock_notify, mock_fetch, mock_classify, mock_draft, mock_placeholder, mock_finalize
):
    """If status update fails, handle() should still complete."""
    mock_fetch.return_value = {
        "object_id": "701",
        "email": "y@test.com",
        "last_message": "I have a question",
    }

    mock_classify_result = MagicMock()
    mock_classify_result.category = "general_inquiry"
    mock_classify.return_value = mock_classify_result

    mock_draft_result = MagicMock()
    mock_draft_result.subject = "Re: Question"
    mock_draft_result.body = "Hello"
    mock_draft.return_value = mock_draft_result

    hs_mock = MagicMock()
    hs_mock.update_inbound_status_sync.side_effect = Exception("API error")
    agent = InboundAgent.__new__(InboundAgent)
    agent.llm = MagicMock()
    agent.hubspot = hs_mock

    from src.agents.inbound import _processed

    _processed.discard("701:None")

    result = agent.handle({"event_type": "contact.creation", "object_id": "701"})

    assert result is not None
    assert result["message_id"] == 2


def test_no_hubspot_skips_status_update():
    """Without HubSpot client, status update should be skipped."""
    with (
        patch.object(
            InboundAgent, "_fetch_contact", return_value={"object_id": "702", "email": "z@test.com"}
        ),
        patch.object(InboundAgent, "_classify") as mock_c,
        patch.object(InboundAgent, "_pick_channel", return_value="email"),
        patch.object(InboundAgent, "_draft_reply"),
        patch.object(InboundAgent, "_persist_placeholder", return_value=(3, 3, False, False)),
        patch.object(InboundAgent, "_extract_requests"),
        patch("src.agents.inbound.notify_approval_once"),
    ):

        mock_classify_result = MagicMock()
        mock_classify_result.category = "support"
        mock_c.return_value = mock_classify_result

        agent = InboundAgent.__new__(InboundAgent)
        agent.llm = MagicMock()
        agent.hubspot = None

        from src.agents.inbound import _processed

        _processed.discard("702:None")

        result = agent.handle({"event_type": "contact.creation", "object_id": "702"})
        assert result is not None


# ---------- Send bookkeeping sets customer + optional HubSpot status ----------


@pytest.mark.asyncio
@patch.object(settings, "HUBSPOT_UPDATE_CONTACT_INBOUND_STATUS", True)
@patch("src.agents.send_worker.add_progress")
@patch("src.integrations.hubspot.HubSpotClient")
async def test_send_bookkeeping_sets_meeting_link_sent(mock_hs_cls, mock_progress) -> None:
    from src.agents.send_worker import _post_send_bookkeeping

    session = MagicMock()
    contact = MagicMock(spec=Contact)
    contact.hubspot_contact_id = "hs-500"
    profile = MagicMock(spec=CustomerProfile)
    session.get.side_effect = lambda model, _id: contact if model is Contact else profile
    # 실제 호출부는 `conv.stage` 를 먼저 올리고 부릅니다 — 미러링은 그때만 돕니다.
    conv = MagicMock(id=2, contact_id=500, hubspot_ticket_id=None,
                     stage="meeting_link_sent")
    msg = MagicMock(subject="Meeting")
    client = mock_hs_cls.return_value
    client.update_inbound_status = AsyncMock()
    client.close = AsyncMock()

    await _post_send_bookkeeping(session, msg, conv, 10)

    client.update_inbound_status.assert_awaited_once_with("hs-500", "meeting_link_sent")
    assert profile.pipeline_stage == "meeting_link_sent"
    session.commit.assert_called_once()
    mock_progress.assert_called_once()


@pytest.mark.asyncio
@patch.object(settings, "HUBSPOT_UPDATE_CONTACT_INBOUND_STATUS", True)
@patch("src.agents.send_worker.add_progress")
@patch("src.integrations.hubspot.HubSpotClient")
async def test_send_bookkeeping_ignores_hubspot_status_failure(mock_hs_cls, _progress) -> None:
    from src.agents.send_worker import _post_send_bookkeeping

    session = MagicMock()
    contact = MagicMock(spec=Contact)
    contact.hubspot_contact_id = "hs-501"
    profile = MagicMock(spec=CustomerProfile)
    session.get.side_effect = lambda model, _id: contact if model is Contact else profile
    conv = MagicMock(id=3, contact_id=501, hubspot_ticket_id=None,
                     stage="meeting_link_sent")
    client = mock_hs_cls.return_value
    client.update_inbound_status = AsyncMock(side_effect=RuntimeError("prop missing"))
    client.close = AsyncMock()

    await _post_send_bookkeeping(session, MagicMock(subject="Reply"), conv, 11)

    assert profile.pipeline_stage == "meeting_link_sent"


@pytest.mark.asyncio
@patch.object(settings, "HUBSPOT_UPDATE_CONTACT_INBOUND_STATUS", True)
@patch("src.agents.send_worker.add_progress")
@patch("src.integrations.hubspot.move_ticket_stage_after_send")
@patch("src.integrations.hubspot.HubSpotClient")
async def test_a_reply_on_a_negotiating_ticket_does_not_drag_the_stage_back(
    mock_hs_cls, mock_move, _progress
) -> None:
    """**후속 회신이 협상 건을 Qualified 로 되돌리면 안 됩니다** (2026-08-31).

    `_send_one` 은 `conv.stage` 를 앞으로만 올리는데(`_ADVANCES_FROM`), 여기 미러링 셋은
    그 조건을 안 보고 언제나 `meeting_link_sent` 를 썼습니다. 자동 초안이 New 티켓에만
    생기던 동안에는 드러나지 않았지만, 운영자가 직접 쓰는 후속 회신이 생기면 협상·수주
    티켓에 한 통 보낼 때마다 허브스팟 티켓이 뒤로 끌려갑니다.
    """
    from src.agents.send_worker import _post_send_bookkeeping

    session = MagicMock()
    contact = MagicMock(spec=Contact)
    contact.hubspot_contact_id = "hs-502"
    profile = MagicMock(spec=CustomerProfile)
    profile.pipeline_stage = "negotiation"
    session.get.side_effect = lambda model, _id: contact if model is Contact else profile
    conv = MagicMock(id=4, contact_id=502, hubspot_ticket_id="T-9", stage="negotiation")
    client = mock_hs_cls.return_value
    client.update_inbound_status = AsyncMock()
    client.close = AsyncMock()

    await _post_send_bookkeeping(session, MagicMock(subject="후속"), conv, 12)

    mock_move.assert_not_called()
    client.update_inbound_status.assert_not_awaited()
    assert profile.pipeline_stage == "negotiation"


@pytest.mark.asyncio
@patch.object(settings, "HUBSPOT_UPDATE_CONTACT_INBOUND_STATUS", True)
@patch("src.agents.send_worker.append_summary_line")
@patch("src.agents.send_worker.add_progress")
@patch("src.integrations.hubspot.move_ticket_stage_after_send")
@patch("src.integrations.hubspot.HubSpotClient")
async def test_a_follow_up_reminder_does_not_push_contacted_again(
    mock_hs_cls, mock_move, mock_progress, mock_summary
) -> None:
    """**후속 리마인더는 「단계를 올린 발송」이 아닙니다** (2026-09-17).

    이미 Contacted 인 티켓에서 나가서 `conv.stage == meeting_link_sent` 가 참인데, 그대로 두면
    리마인더마다 허브스팟 티켓을 Contacted 로 다시 PUT 합니다 — 영업이 방금 Negotiating 으로
    옮겼고 웹훅이 아직 안 왔으면 사람의 이동을 기계가 되돌립니다. 요약에 한 줄을 보태지도
    않습니다(「답을 세 번 했다」로 읽힙니다).
    """
    from src.agents.followup_sequence import REMINDER_1
    from src.agents.send_worker import _post_send_bookkeeping

    session = MagicMock()
    contact = MagicMock(spec=Contact)
    contact.hubspot_contact_id = "hs-503"
    profile = MagicMock(spec=CustomerProfile)
    profile.pipeline_stage = "meeting_link_sent"
    session.get.side_effect = lambda model, _id: contact if model is Contact else profile
    conv = MagicMock(id=5, contact_id=503, hubspot_ticket_id="T-10", stage="meeting_link_sent")
    client = mock_hs_cls.return_value
    client.update_inbound_status = AsyncMock()
    client.close = AsyncMock()

    msg = MagicMock(id=77, subject="RE: quote", prompt_variant=REMINDER_1,
                    post_send_sync_attempts=0)
    await _post_send_bookkeeping(session, msg, conv, 13)

    mock_move.assert_not_called()
    client.update_inbound_status.assert_not_awaited()
    mock_summary.assert_not_called()
    # 글자는 운영자의 것(2026-09-22: 「Reminder Sent 1」) — 티켓 배너 · 보드 카드와 같은 말.
    assert mock_progress.call_args.args[2] == "Reminder Sent 1"

    # **소통 히스토리에 한 줄** (2026-09-21 운영자 지시). 위의 진행 기록 `reply` 는 읽을 때
    # 걸러지므로(`ROUTINE_PROGRESS_KINDS`) 그것만으로는 운영자 눈에 아무것도 안 남습니다.
    row = next(c.args[0] for c in session.add.call_args_list
               if isinstance(c.args[0], CustomerInteraction))
    assert row.summary == "Reminder Sent 1"
    assert row.external_id == "followup:reminder:77"
    # `inbound` 이면 `followup_sequence._replies` 가 우리 리마인더를 고객 답장으로 읽어
    # 티켓을 Negotiating 으로 옮기고 시퀀스를 멈춥니다.
    assert row.direction == "outgoing"
    assert row.conversation_id == 5 and row.contact_id == 503
