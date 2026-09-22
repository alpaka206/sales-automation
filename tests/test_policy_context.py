"""Request provenance and stale-input boundaries, with synthetic data only."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.agents import inbound
from src.db.models import Contact, Conversation, Event, Message, PolicySource
from src.llm.client import LLMClient
from src.llm.knowledge import SelectDocsResult
from src.llm.policy_context import PolicyContextError, PolicySnapshot
from src.llm.pricing import LLMResult


@pytest.fixture
def policy_db(db_session_factory, monkeypatch):
    monkeypatch.setattr("src.db.session.SessionLocal", db_session_factory)
    monkeypatch.setattr(inbound, "SessionLocal", db_session_factory)
    monkeypatch.setattr("src.agents.approval.SessionLocal", db_session_factory)
    with db_session_factory() as session:
        session.add_all([
            PolicySource(label="규칙", doc_key="rules", mode="rules", body="조건 충족 시 검토 가능"),
            PolicySource(label="원문", doc_key="source", mode="knowledge",
                         body="가상 문서: 과거 계약의 적용일과 예외는 원문에 따른다."),
            PolicySource(label="기밀", doc_key="secret", mode="rules", model_access="human_only",
                         body="PRIVATE_POLICY_SENTINEL"),
        ])
        session.commit()
    return db_session_factory


def test_snapshot_filters_before_input_and_keeps_original(policy_db):
    snap = PolicySnapshot.capture("first")
    assert len(snap.documents) == 2
    assert "PRIVATE" not in snap.rules
    assert "과거 계약" in snap.candidates[0].body
    assert "PRIVATE" not in json.dumps(snap.manifest())
    assert snap.manifest()["sources"][0]["version"] == 1


@pytest.mark.parametrize("change", ["body", "model_access", "version", "scope"])
def test_policy_mutation_invalidates_snapshot_even_without_version_bump(policy_db, change):
    snap = PolicySnapshot.capture("first")
    with policy_db() as session:
        row = session.query(PolicySource).filter_by(doc_key="source").one()
        setattr(row, change, {"body": "예외 삭제", "model_access": "human_only",
                             "version": 2, "scope": "followup"}[change])
        session.commit()
    with pytest.raises(PolicyContextError, match="변경"):
        snap.assert_current()


def test_snapshot_rules_are_used_by_the_actual_client(policy_db, monkeypatch):
    snap = PolicySnapshot.capture("first")
    dispatch = MagicMock(return_value="ok")
    monkeypatch.setattr(LLMClient, "_dispatch", dispatch)
    LLMClient().complete("test/hello", {"name": "X"}, stage="first", policy_snapshot=snap)
    assert dispatch.call_args.kwargs["system"] == snap.rules
    with pytest.raises(ValueError):
        LLMClient().complete("test/hello", stage="followup", policy_snapshot=snap)


def _draft_setup(factory):
    with factory() as session:
        contact = Contact(normalized_email="synthetic@example.test", email="synthetic@example.test",
                          full_name="가상 고객")
        session.add(contact)
        session.flush()
        conv = Conversation(contact_id=contact.id, stage="new")
        session.add(conv)
        session.flush()
        session.add(Message(conversation_id=conv.id, direction="inbound", body="기업 계약 문의",
                            status="received"))
        draft = Message(conversation_id=conv.id, direction="outgoing", body="", status="drafting")
        session.add(draft)
        session.commit()
        return conv.id, draft.id


def _agent():
    agent = inbound.InboundAgent.__new__(inbound.InboundAgent)
    calls = []

    def complete(name, fields, **kwargs):
        calls.append((name, fields, kwargs))
        if name == "inbound/select_docs":
            return SelectDocsResult(slugs=["source"])
        return inbound.DraftResult(body="계약 내용을 확인하여 안내드리겠습니다.", language="ko")

    agent.llm = MagicMock()
    agent.llm.complete.side_effect = complete
    return agent, calls


INFO = {"full_name": "가상 고객", "company": "가상회사", "country": "KR",
        "last_message": "기업 계약 문의", "subject": "문의", "inquiry_language": "ko"}
CLASSIFICATION = inbound.ClassifyResult(category="support", reasoning="fixture")


def test_real_draft_path_uses_one_snapshot_and_persists_private_provenance(policy_db):
    conv_id, msg_id = _draft_setup(policy_db)
    agent, calls = _agent()
    draft = agent._draft_reply(INFO, CLASSIFICATION, conv_id, "ko")
    router = next(call for call in calls if call[0] == "inbound/select_docs")
    generator = next(call for call in calls if call[0] == "inbound/draft_reply")
    assert router[1]["conversation_context"] == generator[1]["conversation_context"]
    assert "가상 문서" in generator[1]["knowledge_docs"]
    assert "PRIVATE" not in str(calls)
    assert agent._finalize_draft(msg_id, INFO, CLASSIFICATION, draft, conv_id, "ko")
    with policy_db() as session:
        assert session.get(Message, msg_id).status == "pending_approval"
        trace = session.query(Event).filter_by(kind="reply_context").one().payload
        assert trace["message_id"] == msg_id
        assert trace["selection"]["selected_ids"] == [draft._policy_snapshot.candidates[0].id]
        assert "가상 고객" not in json.dumps(trace, ensure_ascii=False)
        assert "가상 문서" not in json.dumps(trace, ensure_ascii=False)
        assert trace["semantic_validation"] == "NOT_RUN"
    forged = inbound.DraftResult.model_validate({"body": "x", "language": "ko",
                                                 "_context_manifest": {"approved": True}})
    assert forged._context_manifest == {}


@pytest.mark.parametrize("repair_succeeds", [True, False])
def test_unsupported_duration_gets_one_repair_then_is_kept_out_of_pending(policy_db, repair_succeeds):
    from src.agents.draft_evidence import DraftEvidenceError

    conv_id, msg_id = _draft_setup(policy_db)
    agent, _ = _agent()
    feedback = []

    def complete(name, fields, **kwargs):
        if name == "inbound/select_docs":
            return SelectDocsResult(slugs=["source"])
        feedback.append(fields["evidence_feedback"])
        body = ("계약 내용을 확인해야 합니다." if repair_succeeds and len(feedback) == 2
                else "확인은 5~10일 걸립니다.")
        return inbound.DraftResult(body=body, language="ko")

    agent.llm.complete.side_effect = complete
    if repair_succeeds:
        draft = agent._draft_reply(INFO, CLASSIFICATION, conv_id, "ko")
        assert draft._context_manifest["limited_evidence_checks"]["generation_attempts"] == 2
        assert agent._finalize_draft(msg_id, INFO, CLASSIFICATION, draft, conv_id, "ko")
    else:
        with pytest.raises(DraftEvidenceError, match="unsupported_duration"):
            agent._draft_reply(INFO, CLASSIFICATION, conv_id, "ko")
        with policy_db() as session:
            assert session.get(Message, msg_id).status == "drafting"
            assert not session.query(Event).filter_by(kind="reply_context").count()
    assert len(feedback) == 2
    assert feedback[0] == ""
    assert "unsupported_duration" in feedback[1]


def test_new_customer_correction_prevents_finalizing_old_draft(policy_db):
    conv_id, msg_id = _draft_setup(policy_db)
    agent, _ = _agent()
    draft = agent._draft_reply(INFO, CLASSIFICATION, conv_id, "ko")
    with policy_db() as session:
        session.add(Message(conversation_id=conv_id, direction="inbound", body="정정합니다.",
                            status="received"))
        session.commit()
    with pytest.raises(RuntimeError, match="대화가 변경"):
        agent._finalize_draft(msg_id, INFO, CLASSIFICATION, draft, conv_id, "ko")
    with policy_db() as session:
        assert session.get(Message, msg_id).status == "drafting"
        assert not session.query(Event).filter_by(kind="reply_context").count()


def test_schema_failure_does_not_copy_customer_text_to_error_logs(monkeypatch, caplog):
    monkeypatch.setattr("src.llm.client.call_gemini", lambda *a, **k: LLMResult(
        text='{"body": "PRIVATE_CUSTOMER_SENTINEL"}', input_tokens=0, output_tokens=0, model="fake"))
    with pytest.raises(RuntimeError) as raised:
        LLMClient().complete("test/hello", schema=inbound.DraftResult)
    assert "PRIVATE_CUSTOMER_SENTINEL" not in caplog.text
    assert "PRIVATE_CUSTOMER_SENTINEL" not in str(raised.value)


def _reviewed_draft(factory):
    from src.agents.approval import approve

    conv_id, msg_id = _draft_setup(factory)
    agent, _ = _agent()
    draft = agent._draft_reply(INFO, CLASSIFICATION, conv_id, "ko")
    agent._finalize_draft(msg_id, INFO, CLASSIFICATION, draft, conv_id, "ko")
    with factory() as session:
        session.get(Message, msg_id).to_address = "synthetic@example.test"
        session.get(Conversation, conv_id).hubspot_ticket_id = "fixture-ticket"
        session.commit()
    return conv_id, approve(msg_id, "local-test", edited_body="검토한 답변입니다.")


@pytest.mark.parametrize("field,value", [
    ("body", "unreviewed"), ("to_address", "other@example.test"),
    ("subject", "changed"), ("cc_addresses", "other@example.test"),
    ("channel_account_id", "changed-account"), ("status", "rejected"),
])
def test_approved_content_recipient_and_status_changes_block_send(policy_db, field, value):
    from src.agents.reply_safety import validate_approved_message

    _, reviewed = _reviewed_draft(policy_db)
    validate_approved_message(reviewed)
    with policy_db() as session:
        setattr(session.get(Message, reviewed.id), field, value)
        session.commit()
    with pytest.raises(RuntimeError, match="승인 후"):
        validate_approved_message(reviewed)


@pytest.mark.parametrize("change", ["policy", "correction"])
def test_approved_draft_invalidated_by_new_evidence(policy_db, change):
    from src.agents.reply_safety import validate_approved_message

    conv_id, reviewed = _reviewed_draft(policy_db)
    with policy_db() as session:
        if change == "policy":
            session.query(PolicySource).filter_by(doc_key="source").one().body = "changed"
        else:
            session.add(Message(conversation_id=conv_id, direction="inbound",
                                body="고객 정정", status="received"))
        session.commit()
    with pytest.raises(RuntimeError, match="변경"):
        validate_approved_message(reviewed)


def test_editing_an_unselected_document_does_not_invalidate_the_queue(policy_db):
    """전체 digest 로 재면 문서 하나 저장에 대기열 전체가 막히고, 출구는 다시 쓰기뿐입니다.

    라우터가 안 고른 참고 문서는 초안이 못 본 것이라 고쳐도 승인·발송이 그대로여야 합니다.
    """
    from src.agents.reply_safety import validate_approved_message

    with policy_db() as session:
        session.add(PolicySource(label="무관", doc_key="other", mode="knowledge", body="무관한 문서"))
        session.commit()
    _, reviewed = _reviewed_draft(policy_db)  # `_agent()` 는 "source" 만 고릅니다.
    with policy_db() as session:
        session.query(PolicySource).filter_by(doc_key="other").one().body = "고친 무관한 문서"
        session.commit()
    validate_approved_message(reviewed)


def test_a_new_rules_document_invalidates_the_draft(policy_db):
    """규칙은 모든 회신에 실리므로, 초안이 못 본 규칙이 생긴 것도 정책 변경입니다."""
    from src.agents.reply_safety import validate_approved_message

    _, reviewed = _reviewed_draft(policy_db)
    with policy_db() as session:
        session.add(PolicySource(label="새 규칙", doc_key="rules2", mode="rules", body="새 조건"))
        session.commit()
    with pytest.raises(RuntimeError, match="정책"):
        validate_approved_message(reviewed)


def test_approval_refuses_stale_generated_context(policy_db):
    from src.agents.approval import ApprovalError, approve

    conv_id, msg_id = _draft_setup(policy_db)
    agent, _ = _agent()
    draft = agent._draft_reply(INFO, CLASSIFICATION, conv_id, "ko")
    agent._finalize_draft(msg_id, INFO, CLASSIFICATION, draft, conv_id, "ko")
    with policy_db() as session:
        session.query(PolicySource).filter_by(doc_key="source").one().body = "changed"
        session.commit()
    with pytest.raises(ApprovalError, match="정책"):
        approve(msg_id, "local-test")
    with policy_db() as session:
        assert session.get(Message, msg_id).status == "pending_approval"
        assert not session.query(Event).filter_by(kind="reply_approval").count()


@pytest.mark.asyncio
async def test_correction_during_remote_lookup_blocks_the_actual_transport(policy_db, monkeypatch):
    from unittest.mock import AsyncMock
    from src.integrations.delivery import DeliveryPermanentError
    from src.integrations.hubspot import ConversationReplyContext
    from src.integrations.senders import send

    conv_id, reviewed = _reviewed_draft(policy_db)
    with policy_db() as session:
        message = session.get(Message, reviewed.id)
        _ = message.conversation  # Load the relationship before detaching.
        session.expunge_all()

    async def lookup(*args):
        with policy_db() as session:
            session.add(Message(conversation_id=conv_id, direction="inbound",
                                body="방금 정정", status="received"))
            session.commit()
        return ConversationReplyContext("fixture-thread", "1002", "fixture-account")

    client = MagicMock()
    client.find_default_reply_context = AsyncMock(side_effect=lookup)
    client.send_conversation_message = AsyncMock()
    client.close = AsyncMock()
    monkeypatch.setattr("src.integrations.hubspot.HubSpotClient", lambda: client)
    with pytest.raises(DeliveryPermanentError, match="대화"):
        await send(message)
    client.send_conversation_message.assert_not_awaited()
    client.close.assert_awaited_once()


def test_the_workers_claim_passes_the_send_gate(policy_db, monkeypatch):
    """워커가 잡은 행은 status 가 `sending:<pid>:<random>` 입니다 — 그게 곧 행 잠금입니다.

    이 검사가 `"sending"` 한 글자만 받으면 사람이 승인한 회신이 **전부** send_failed 가
    됩니다. 로컬에서는 안 잡힙니다: 안전 모드는 이 검사 앞에서 SendingDisabled 로 빠지고,
    기존 테스트는 `approved` 행에 대고 검사를 불렀습니다. 그래서 워커와 같은 길로 잡습니다.
    """
    from src.agents import send_worker
    from src.agents.reply_safety import validate_approved_message

    monkeypatch.setattr(send_worker, "SessionLocal", policy_db)
    _, reviewed = _reviewed_draft(policy_db)
    assert send_worker._claim_id(reviewed.id)
    with policy_db() as session:
        assert session.get(Message, reviewed.id).status.startswith("sending:")
    validate_approved_message(reviewed)  # 잡힌 행은 승인된 그대로입니다.
    with policy_db() as session:
        session.get(Message, reviewed.id).status = "rejected"
        session.commit()
    with pytest.raises(RuntimeError, match="승인 후"):
        validate_approved_message(reviewed)


def test_history_sync_and_bookkeeping_rows_do_not_invalidate_a_draft(policy_db):
    """초안 뒤에 온 **고객 메시지**만 「대화 변경」입니다.

    10분 폴러의 스레드 수집은 최초 문의의 사본을 `hubspot:conv:` 줄로 넣고(문의 `messages`
    행에는 스레드 id 가 없어 안 걸러집니다), 운영자는 티켓에 「수신」 기록을 적습니다. 그
    줄들로 승인이 막히면 New 티켓마다 다시 쓰기 — 운영자 편집이 사라지는 길 — 뿐입니다.
    """
    from datetime import datetime
    from src.agents.approval import approve
    from src.agents.reply_safety import validate_approved_message
    from src.db.models import CustomerInteraction

    conv_id, msg_id = _draft_setup(policy_db)
    agent, _ = _agent()
    draft = agent._draft_reply(INFO, CLASSIFICATION, conv_id, "ko")
    with policy_db() as session:
        conv = session.get(Conversation, conv_id)
        session.add_all([
            # 수집기가 넣은 최초 문의의 사본 — 시각은 문의 시각(초안 전)입니다.
            CustomerInteraction(contact_id=conv.contact_id, conversation_id=conv_id,
                                direction="incoming", channel="email", summary="기업 계약 문의",
                                external_id="hubspot:conv:original", happened_at=datetime(2026, 1, 1)),
            # 운영자가 적은 기록 — 고객의 새 말이 아닙니다.
            CustomerInteraction(contact_id=conv.contact_id, conversation_id=conv_id,
                                direction="outgoing", channel="phone", summary="전화로 안내",
                                happened_at=datetime(2030, 1, 1)),
        ])
        session.commit()
    agent._finalize_draft(msg_id, INFO, CLASSIFICATION, draft, conv_id, "ko")
    with policy_db() as session:
        session.get(Message, msg_id).to_address = "synthetic@example.test"
        session.get(Conversation, conv_id).hubspot_ticket_id = "fixture-ticket"
        session.commit()
    reviewed = approve(msg_id, "local-test", edited_body="검토한 답변입니다.")
    validate_approved_message(reviewed)
    # 초안 뒤에 **고객이** 보낸 것은 막습니다 — 수집으로 들어온 답장도 같은 규칙입니다.
    with policy_db() as session:
        session.add(CustomerInteraction(contact_id=conv.contact_id, conversation_id=conv_id,
                                        direction="incoming", channel="email", summary="정정합니다",
                                        external_id="hubspot:conv:reply", happened_at=datetime(2030, 1, 2)))
        session.commit()
    with pytest.raises(RuntimeError, match="대화가 변경"):
        validate_approved_message(reviewed)


def test_first_reply_routes_the_latest_customer_correction(policy_db):
    from datetime import datetime
    from src.db.models import CustomerInteraction

    conv_id, _ = _draft_setup(policy_db)
    with policy_db() as session:
        conv = session.get(Conversation, conv_id)
        session.add(CustomerInteraction(contact_id=conv.contact_id, conversation_id=conv_id,
                                        direction="incoming", channel="email", happened_at=datetime(2030, 1, 1),
                                        summary="정정: 기업 계약이 아니라 개인 이용입니다."))
        session.commit()
    agent, calls = _agent()
    agent._draft_reply(INFO, CLASSIFICATION, conv_id, "ko")
    router = next(call for call in calls if call[0] == "inbound/select_docs")
    generator = next(call for call in calls if call[0] == "inbound/draft_reply")
    assert router[1]["inquiry"] == generator[1]["last_message"]
    assert "정정:" in generator[1]["last_message"]
    assert generator[2]["stage"] == "first"
    assert INFO["last_message"] in generator[1]["conversation_context"]


def test_request_extraction_includes_synced_corrections_and_can_clear_withdrawn_requests(policy_db):
    from datetime import datetime
    from src.db.models import CustomerInteraction

    conv_id, _ = _draft_setup(policy_db)
    with policy_db() as session:
        conv = session.get(Conversation, conv_id)
        conv.customer_requests = "과거 요청"
        session.add(CustomerInteraction(contact_id=conv.contact_id, conversation_id=conv_id,
                                        direction="incoming", channel="email", happened_at=datetime(2030, 1, 1),
                                        summary="기존 요청을 철회합니다."))
        session.commit()
    agent, _ = _agent()
    agent.llm.complete.side_effect = None
    agent.llm.complete.return_value = inbound._RequestsResult(customer_requests="")
    agent._extract_requests(conv_id, INFO)
    fields = agent.llm.complete.call_args.args[1]
    assert "기존 요청을 철회" in fields["thread_text"]
    assert "고객 주장·미검증" in fields["thread_text"]
    with policy_db() as session:
        assert session.get(Conversation, conv_id).customer_requests is None


@pytest.mark.parametrize("slugs", [[], ["PRIVATE_CUSTOMER_SENTINEL"]])
def test_router_logs_do_not_echo_model_reasoning_or_keys(policy_db, caplog, slugs):
    from src.llm.knowledge import select_relevant_docs

    llm = MagicMock()
    llm.complete.return_value = SelectDocsResult(slugs=slugs, reasoning="PRIVATE_CUSTOMER_SENTINEL")
    with caplog.at_level("INFO"):
        result = select_relevant_docs("synthetic", "support", llm,
                                      candidates=PolicySnapshot.capture("first").candidates)
    assert result
    assert "PRIVATE_CUSTOMER_SENTINEL" not in caplog.text
