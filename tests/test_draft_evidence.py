from types import SimpleNamespace

import pytest

from src.agents.draft_evidence import AnswerPoint, PolicyQuote, answer_point_gaps, check_draft


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


@pytest.mark.parametrize("body", [
    "미팅은 30분 정도 진행됩니다.",
    "2026년 9월 21일에 안내드린 내용입니다.",
    "9월 30일까지 신청하실 수 있습니다.",
    "환불 요청이 접수되었는지 여부는 확인이 필요합니다.",
])
def test_meeting_lengths_dates_and_the_requested_hedge_are_not_rejected(body):
    assert check_draft(body, [], documents=DOCS, customer_text="") == []


def test_business_days_in_a_korean_doc_support_the_translated_english_draft():
    docs = [SimpleNamespace(id=1, body="영업일 3일 이내 처리됩니다")]
    assert check_draft("Refunds are processed within 3 business days.", [], documents=docs, customer_text="") == []


def test_the_context_filter_still_catches_an_invented_processing_period():
    assert "unsupported_duration" in check_draft("환불 처리는 24시간 이내 완료됩니다.", [], documents=DOCS, customer_text="")


def test_the_model_writes_the_body_and_answer_points_are_an_optional_audit():
    """본문은 모델이 쓴 이메일 전체다 (2026-10-06). 예전에는 answer_points 를 코드가 이어 붙여 본문으로
    썼고 그래서 answer_points 가 하나도 없으면 스키마가 거절했다 — 이제는 점검용이라 없어도 받는다."""
    from src.agents.inbound import DraftResult

    draft = DraftResult.model_validate({"body": "Hi Ana,\n\nThanks for writing.", "language": "en"})
    assert draft.body == "Hi Ana,\n\nThanks for writing."
    assert draft.answer_points == [] and draft.placeholders == [] and draft.subject == ""
    # 점검용 칸이 비어도 초안 전체가 스키마에서 떨어지지 않는다 — 그러면 JSON 재시도와 큐 재시도만 쌓인다.
    partial = DraftResult.model_validate({"body": "b", "language": "en", "answer_points": [{"question": "q"}]})
    assert partial.answer_points[0].supported_answer == ""


def test_a_draft_without_a_body_does_not_parse_as_one():
    from pydantic import ValidationError
    from src.agents.inbound import DraftResult

    with pytest.raises(ValidationError):
        DraftResult.model_validate({"answer_points": [{"question": "q", "supported_answer": "a"}], "language": "en"})


_REFUND = AnswerPoint(question="refund?", supported_answer="Within 14 days of payment.")
_PLAN = AnswerPoint(question="plan?", supported_answer="12,000 minutes, 20% off.")


@pytest.mark.parametrize("body,point,gap", [
    ("Refunds are possible within 14 days of payment.", _REFUND, False),
    # ADR 2026-09-21 의 그 사고 — 조건의 숫자가 본문에서 빠졌다.
    ("Refunds are possible within two weeks of payment.", _REFUND, True),
    ("The 12,000-minute plan includes a 20% discount.", _PLAN, False),
    ("The 12000-minute plan includes a 20 % discount.", _PLAN, False),
    ("The plan includes a discount.", _PLAN, True),
])
def test_a_number_the_answer_points_promise_must_be_in_the_body(body, point, gap):
    assert answer_point_gaps(body, [point]) == (["answer_point_missing"] if gap else [])


def test_list_numbers_and_empty_points_are_not_promises():
    assert answer_point_gaps("We support SRT.", [AnswerPoint(supported_answer="1. We support SRT."),
                                                 AnswerPoint(question="?")]) == []


def test_thousands_separators_are_one_number_not_a_new_duration():
    """「12,000 minutes」를 「000 minutes」로 읽어 문서에 있는 분량이 지어낸 기간으로 걸렸다."""
    docs = [SimpleNamespace(id=1, body="Processing of up to 12,000 minutes takes 3 business days.")]
    body = "Processing of 12,000 minutes takes 3 business days."
    assert check_draft(body, [], documents=docs, customer_text="") == []
    assert "unsupported_duration" in check_draft("Processing of 13,000 minutes takes 3 business days.",
                                                 [], documents=docs, customer_text="")


def test_a_quote_survives_table_and_markdown_reformatting():
    """노션의 탭 표를 | 표로, 굵은 글씨를 ** 없이 옮겨 적은 인용은 같은 문장이다(평가 F415d)."""
    docs = [SimpleNamespace(id=1, body="플랜\t분량\t가격\n**Pro**\t180분\t공개 가격\n\n환불은 _14일_ 이내입니다.")]
    quotes = [PolicyQuote(source_id=1, quote="| Pro | 180분 | 공개 가격 |"),
              PolicyQuote(source_id=1, quote="환불은 14일 이내입니다.")]
    assert check_draft("답변", quotes, documents=docs, customer_text="") == []


def test_a_real_sentence_cited_under_the_neighbouring_id_is_still_a_real_quote():
    """모델이 옆 문서의 id 로 인용한 진짜 문장(평가 R424)은 인용 실패가 아니다 — 지어낸 문장만 실패다."""
    docs = [SimpleNamespace(id=17, body="응대 지침."), SimpleNamespace(id=18, body="보관 기간은 30일입니다.")]
    assert check_draft("답변", [PolicyQuote(source_id=17, quote="보관 기간은 30일입니다.")],
                       documents=docs, customer_text="") == []


@pytest.mark.parametrize("quote", [PolicyQuote(source_id=2, quote="비밀"),
                                  PolicyQuote(source_id=1, quote="환불은 21일")])
def test_existing_id_or_valid_json_does_not_make_a_quote_valid(quote):
    assert "invalid_policy_quote" in check_draft("답변", [quote], documents=DOCS, customer_text="")
