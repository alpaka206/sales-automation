from types import SimpleNamespace

import pytest

from src.agents.draft_evidence import AnswerPoint, PolicyQuote, check_draft, compose_answer


DOCS = [SimpleNamespace(id=1, body="결제 후 14일 이내 신청 가능. 프로젝트 보관은 30일입니다.")]


@pytest.mark.parametrize("body", [
    "환불 처리는 영업일 기준 5~10일 정도 소요됩니다.",
    "확인 절차는 보통 1~2 영업일이 소요됩니다.",
    "Refund processing takes 5-10 business days.",
])
def test_live_hallucinated_durations_are_detected(body):
    assert "unsupported_duration" in check_draft(body, [], documents=DOCS, customer_text="3일 전 결제")


@pytest.mark.parametrize("body", [
    "고객님의 문의 내용에 따라 환불 신청이 접수되었습니다.",
    "현재 담당 부서에서 환불 절차를 진행 중입니다.",
    "환불 가능 여부를 확인하고 있습니다.",
    "Your refund has been processed.",
    "현재 환불 절차가 진행 중이며, 아직 완료되지 않았습니다.",
    "현재 환불이 완료된 상태는 아닙니다.",
    "아직 환불이 완료된 것은 아니며, 처리가 완료되면 알려드리겠습니다.",
    "환불이 완료되지 않았습니다.",
    "Your refund has not been completed.",
])
def test_claiming_an_unexecuted_action_is_detected(body):
    assert "unsupported_execution_status" in check_draft(body, [], documents=DOCS, customer_text="")


def test_supported_answers_customer_durations_and_explicit_unknowns_survive():
    body = "말씀하신 결제 후 3일 건은 14일 이내입니다. 보관은 30일입니다. 환불이 완료됐는지는 확인할 수 없습니다."
    assert not check_draft(body, [PolicyQuote(source_id=1, quote="프로젝트 보관은 30일입니다.")],
                           documents=DOCS, customer_text="3일 전 결제")


def test_translated_units_and_conditional_status_are_not_rejected():
    body = "Projects are stored for 30 days. 환불이 완료됐다고 확인할 수 없습니다."
    assert not check_draft(body, [], documents=DOCS, customer_text="")


def test_composition_cannot_drop_known_answer_when_internal_verification_is_needed():
    points = [AnswerPoint(question="환불?", supported_answer="14일 이내라면 신청 대상입니다.",
                          verification_needed="결제일과 다운로드 이력 확인이 필요합니다."),
              AnswerPoint(question="자막?", supported_answer="SRT로 내보낼 수 있습니다.")]
    assert compose_answer(points) == (
        "14일 이내라면 신청 대상입니다.\n\n결제일과 다운로드 이력 확인이 필요합니다.\n\n"
        "SRT로 내보낼 수 있습니다.")


def test_grounded_schema_does_not_accept_a_body_without_answer_points():
    from pydantic import ValidationError
    from src.agents.inbound import GroundedDraftResult

    with pytest.raises(ValidationError):
        GroundedDraftResult.model_validate({"body": "확인하겠습니다.", "language": "ko"})


@pytest.mark.parametrize("quote", [PolicyQuote(source_id=2, quote="비밀"),
                                  PolicyQuote(source_id=1, quote="환불은 21일")])
def test_existing_id_or_valid_json_does_not_make_a_quote_valid(quote):
    assert "invalid_policy_quote" in check_draft("답변", [quote], documents=DOCS, customer_text="")
