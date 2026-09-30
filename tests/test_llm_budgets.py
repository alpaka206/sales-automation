"""출력 한도가 작은 호출은 생각 토큰이 들어갈 자리를 남긴다 (2026-09-30).

한도(max_output_tokens)는 **생각 토큰까지 포함한다.** 두 자리가 `gemini-3.8-flash` 가 되면서 생각 단계가
LOW 가 됐고(3.8 은 MINIMAL 을 거절한다), LOW 도 짧은 일에 수십 토큰을 생각한다 — 실측 언어 판별 최대 67(두 번 잰 것 중 큰 값).
한도 8 이던 언어 판별은 20건 중 2건이 잘려 판별에 실패했고(실패하면 영어로 떨어져 네덜란드어 고객에게 영어
회신이 간다), 한 줄 요약(120)은 생각이 조금만 늘어도 문장 중간에서 끊긴다. 청구는 쓴 만큼이라 한도를 넉넉히
둬도 값은 안 오른다.
"""

from __future__ import annotations

import pytest

from src.llm.client import LLMClient

ROOM_FOR_THINKING = 256


class _Recorder:
    def __init__(self, answer: str):
        self.answer = answer
        self.max_tokens: list[int] = []

    def complete(self, prompt_name, variables=None, **kwargs):
        self.max_tokens.append(kwargs.get("max_tokens"))
        return self.answer


def test_language_detection_leaves_room_for_thinking():
    from src.llm.language import detect_language

    llm = _Recorder("es")
    assert detect_language("Hola, gracias por la respuesta.", llm=llm) == "es"
    assert llm.max_tokens and llm.max_tokens[0] >= ROOM_FOR_THINKING


def test_usage_note_leaves_room_for_thinking():
    from src.llm.knowledge import usage_note_from_body

    llm = _Recorder("쓰는 경우: 환불 문의 / 담긴 것: 14일 규정")
    usage_note_from_body("환불 규정", "결제 후 14일 이내이며 다운로드 이력이 없으면 환불 신청 대상이다.", llm=llm)
    assert llm.max_tokens and llm.max_tokens[0] >= ROOM_FOR_THINKING


@pytest.mark.parametrize("where", ["summaries", "customer_ops"])
def test_one_line_summaries_leave_room_for_thinking(monkeypatch, where):
    """한 줄 요약 함수는 둘이다 — `summaries.one_line`(티켓 요약 · 백필 · 요약 다시 만들기)과
    `customer_ops._one_line`(연락처 기록 가져오기). 둘 다 생각이 들어갈 자리를 남겨야 한다."""
    seen: list[int] = []

    def fake_complete(self, prompt_name, variables=None, **kwargs):
        seen.append(kwargs.get("max_tokens"))
        return "견적 문의 — 50분 영상 30편 영어·일본어"

    monkeypatch.setattr(LLMClient, "complete", fake_complete)
    body = "안녕하세요. 50분짜리 강의 영상 30편을 영어와 일본어로 더빙하려고 합니다. 견적과 납기가 궁금합니다. " * 2
    if where == "summaries":
        from src.agents.summaries import one_line

        one_line("inbound", "견적 문의", body, always=True)
    else:
        from src.api.routes.customer_ops import _one_line

        _one_line("inbound", "견적 문의", body)
    assert seen and seen[0] >= ROOM_FOR_THINKING
