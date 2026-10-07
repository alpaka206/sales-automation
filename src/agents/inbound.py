"""Inbound agent - classifies, drafts a reply, and queues it for approval."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any, NamedTuple

from pydantic import BaseModel, PrivateAttr
from sqlalchemy import select

from ..common.config import settings
from ..common.domains import is_personal_domain
from ..common.subjects import choose_reply_subject
from ..common.textwash import text_wash
from ..db.conversation_history import add_progress
from ..db.models import (
    Contact,
    Conversation,
    CustomerInteraction,
    CustomerProfile,
    Event,
    InboundJob,
    Message,
)
from ..db.session import SessionLocal
from ..integrations.hubspot import HubSpotClient, HubSpotNotConfigured
from ..common.sheet_values import (
    COMPANY_TYPES,
    UNKNOWN_COMPANY_TYPE,
    UNKNOWN_COUNTRY,
    country_in_korean,
    normalise_plan,
)
from ..llm.client import LLMClient
from ..llm.knowledge import FIRST, FOLLOWUP, select_relevant_docs
from ..llm.policy_context import PolicySnapshot, fingerprint, render_rules
from ..llm.prompts import get_reply_format, load_prompt
from ._notify import notify_approval_once
from .inbound_scoring import (  # noqa: F401 — re-exported for callers/tests
    _build_enrichment_context,
    _domain_from_email,
    _normalize_email,
)
from .summaries import append_summary_line
from .stage_sync import _retire_superseded_drafts
from .followup_sequence import REMINDER_NOTE_PREFIX, REMINDER_VARIANTS
from .draft_evidence import (
    AnswerPoint,
    DraftEvidenceError,
    PolicyQuote,
    answer_point_gaps,
    check_draft,
    repair_instruction,
)

logger = logging.getLogger(__name__)


def _default_signature() -> str | None:
    """어느 서명으로 시작할지 — 목록의 첫 서명. 그 건에서 바꾸는 것은 검토 화면입니다.

    Imported here rather than at module load for the same reason every other template
    helper in this file is: the DB layer must not be pulled in before settings are.
    """
    from ..db.email_templates import default_signature_key

    return default_signature_key()


_DEFAULT_HUBSPOT = object()
# 「메일 발송」이 연 후속 초안의 표시 — `routes.messages.MANUAL_REPLY_VARIANT` 와 같은 글자입니다.
_MANUAL_VARIANT = "manual"

# **고객에게 나가는 초안은 생각 단계를 LOW 로 따로 넘긴다** (2026-09-23 유료 블라인드 짝 비교, `tmp/model-switch-2026-09-23`).
# 같은 문의 14건 × 2회를 가린 채 채점했을 때 3.5 Flash 는 MINIMAL 이 평균 4.39·실패 0, LOW 가 4.50·실패 0·
# 지어낸 문장 0 이었다(2.5 Pro 는 4.07·실패 2). 2026-09-30 부터는 자리 기본값도 두 자리 다 LOW 라(`gemini-3.8-flash` 는
# MINIMAL 을 거절한다) 지금은 같은 값이다 — 자리 기본값이 다시 바뀌어도 초안은 LOW 에 머물도록 따로 둔다
# (`llm/client._THINKING_LEVEL_BY_TIER`).
_DRAFT_THINKING_LEVEL = "LOW"
# 본문 전체 + 질문별 점검 기록 + 인용이 한 응답이다. 4000 이던 때 가장 긴 초안이 1,645 토큰이었고(평가 52건), 이제는
# 본문을 따로 한 번 더 쓰므로 그 두 배를 넘을 수 있다 — 한도에서 잘리면 JSON 이 깨지고 재시도도 같은 한도라 실패한다.
_DRAFT_MAX_TOKENS = 8000
# 보내기 전 검토(`_review_draft`)의 출력 한도 — 문제 목록 JSON 과 생각 토큰이 이 안에 들어갑니다.
_REVIEW_MAX_TOKENS = 4000
_REVIEW_THINKING_LEVEL = "LOW"

# **코드는 상황을 사실로만 적는다** (2026-10-06). 예전에는 여기에 영업 지시가 있었다 — 첫 회신이면 「스페셜
# 프로모션을 언급하고 미팅을 제안하라」, 후속이면 「금액은 미팅에서 안내하겠다고 쓰라」·「더 자세히 쓰라」.
# 콘솔 문서가 같은 자리에 다른 말을 하고 있었고(할인 약속 금지 · 행동 제안은 하나 · 「새 정보를 흘려 상대를
# 유인하지 않는다」), 둘이 부딪히면 모델은 그때그때 다르게 골랐다. 무엇을 쓸지는 콘솔 문서가 정하고(정리기가
# 기밀만 빼고 정리해 싣는다 — `organizer.knowledge_for`), 코드는 지금이 어떤 회신인지만 말한다.
FIRST_REPLY, ANSWER_REPLY, NUDGE = "first_reply", "answer_reply", "nudge"
_SITUATION_FACTS = {
    FIRST_REPLY: "우리 영업이 이 고객에게 보내는 첫 이메일입니다.",
    ANSWER_REPLY: "우리 영업의 마지막 이메일 뒤에 고객이 새 메시지를 보냈습니다. 위 '가장 최근 문의'가 그 메시지입니다.",
    NUDGE: "우리 영업의 마지막 이메일 뒤로 고객의 답장이 아직 없습니다.",
}
_PREVIOUS_CHARS = 2000


# Kept for compatibility with older extensions/tests; durable queue keys now
# provide production deduplication and this set is intentionally not consulted.
_processed: set[str] = set()


class _Turn(NamedTuple):
    at: datetime
    direction: str  # "inbound" | "outgoing" | "note"
    subject: str | None
    body: str
    reminder: bool = False  # 후속 리마인더 — 우리 차례이지만 「이미 적은 말」의 근거는 아닙니다
    source_ref: str = ""
    # **누가** 말했나 — customer · sales · cs · bot · form · note · reminder (`history_view.turn_role`).
    # 「우리 회신」은 `role == "sales"` 하나입니다: 챗봇 답 · CS 안내도 방향은 outgoing 입니다.
    role: str = ""
    channel: str = ""
    origin: str = ""  # console · hubspot · crm · gmail · manual (`history_view.turn_origin`)


# 초안 프롬프트의 말머리. 「우리」 하나로 두면 모델이 챗봇 답과 CS 안내를 우리 영업 회신으로 읽습니다.
_ROLE_LABELS = {
    "customer": "고객", "sales": "우리(영업)", "cs": "우리(CS 안내)", "bot": "챗봇", "form": "폼",
    "note": "기록", "reminder": "우리(리마인더)",
}
# 실제로 나간 회신. 초안·승인 대기는 아직 고객이 못 본 글이라 「우리가 한 말」이 아닙니다.
_SENT_STATUSES = ("sent",)


def thread_events(conv_id: int | None, *, factory=None) -> list[_Turn]:
    """이 티켓에서 오간 것 전부, 오래된 순. **두 표를 합칩니다.**

    ``messages`` 에는 이 콘솔이 만든 것만 있습니다 — 최초 문의 하나와 우리가 여기서 보낸
    회신. New 이후의 실제 대화(고객 답장, 허브스팟 화면에서 사람이 보낸 회신, 채팅·폼)는
    ``customer_interactions`` 에 있고, 그것은 허브스팟 스레드 수집기가 넣습니다
    (``agents/ticket_history``). **한쪽만 보면 New 이후가 통째로 빕니다** — 그래서 후속
    초안이 최초 문의에 다시 답하고, 「첫 회신인가」 판정이 틀립니다.

    **같은 메시지를 두 번 세지 않습니다.** 우리가 여기서 보낸 회신은 수집기가 허브스팟에서
    도로 가져오므로 두 표에 다 있습니다. 가르는 것은 ``history_view.message_copies`` 하나입니다 —
    열쇠(발송 응답이 돌려준 스레드 메시지 id = ``messages.hubspot_message_id``)가 있으면 열쇠로,
    없으면(첫 문의 · 「나갔다」로 확인한 발송) 본문으로. 티켓 화면이 중복을 거를 때와 같은 규칙입니다.

    **누가 말했는지는 `role` 이 답합니다** (2026-10-06). 방향만 보면 챗봇 답과 CS 주소의 안내도 「우리
    회신」이라, 영업이 한 통도 안 보낸 티켓이 「이미 답한 티켓」이 됐습니다. 자는 `history_view.turn_role`
    하나이고, 「우리 영업의 이메일 회신」은 `role == "sales"` 입니다(`history_view.is_sales_email`).

    조회 실패는 빈 대화가 아닙니다. 해당 초안을 실패 처리해 다시 시도하게 합니다.
    """
    if not conv_id:
        return []
    try:
        with (factory or SessionLocal)() as session:
            threads = threads_events(session, [conv_id])
        if conv_id not in threads:
            raise RuntimeError("대화를 찾을 수 없습니다.")
        return threads[conv_id]
    except Exception:
        logger.warning("대화 이력을 못 읽었습니다 (conv=%s).", conv_id, exc_info=True)
        raise


def threads_events(session, conv_ids) -> dict[int, list[_Turn]]:
    """대화 여럿의 ``thread_events`` 를 쿼리 셋으로 — 없는 대화는 결과에 없습니다.

    선례 후보(`llm.precedents`)처럼 대화 수십 개를 훑는 자리용입니다. 대화마다 부르면 대화당 왕복이
    셋이라 초안 한 건에 수백 번이었습니다. 대화마다의 결과는 ``thread_events`` 와 같습니다 — 같은
    ``_thread_turns`` 가 만듭니다.
    """
    ids = list(dict.fromkeys(conv_ids))
    if not ids:
        return {}
    owners = dict(session.execute(
        select(Conversation.id, Conversation.contact_id).where(Conversation.id.in_(ids))
    ).all())
    messages: dict[int, list[Message]] = {conv_id: [] for conv_id in owners}
    for row in (
        session.query(Message)
        .filter(
            Message.conversation_id.in_(ids),
            Message.body != "",
            (Message.direction == "inbound")
            | (
                (Message.direction == "outgoing")
                & Message.status.in_(_SENT_STATUSES)
                & ((Message.prompt_variant.is_(None)) | (Message.prompt_variant != "auto_ack"))
            ),
        )
        .order_by(Message.id)
    ):
        messages[row.conversation_id].append(row)
    interactions: dict[int, list[CustomerInteraction]] = {conv_id: [] for conv_id in owners}
    for item in (
        session.query(CustomerInteraction)
        .join(Conversation, Conversation.id == CustomerInteraction.conversation_id)
        # 그 대화의 사람 것만 — 다른 연락처 줄이 붙어 있을 수 있습니다.
        .filter(CustomerInteraction.conversation_id.in_(ids),
                CustomerInteraction.contact_id == Conversation.contact_id)
        .order_by(CustomerInteraction.id)
    ):
        interactions[item.conversation_id].append(item)
    return {conv_id: _thread_turns(messages[conv_id], interactions[conv_id]) for conv_id in owners}


def _thread_turns(messages: list, interactions: list) -> list[_Turn]:
    """한 대화의 메시지 행과 접점 기록 줄을 시간순 `_Turn` 으로 — 규칙은 ``thread_events`` 의 설명 그대로."""
    # 열쇠가 없는 행(첫 문의 · 「나갔다」로 확인한 발송)은 본문으로 — 자는 `history_view.message_copies` 하나.
    from ..db.history_view import message_copies, non_sales_senders, turn_origin, turn_role

    drawn_by_messages = message_copies(messages, interactions)
    non_sales = non_sales_senders()
    # **후속 리마인더의 소통 히스토리 줄은 대화가 아닙니다** (2026-09-21). 발송 뒤 정리가
    # 「Reminder Sent 1」 한 줄을 `customer_interactions` 에 남기는데(운영자 지시로 그
    # 표에 남겨야 화면에 뜹니다), 그 줄은 같은 리마인더를 **두 번째로** 그리는 것이고
    # 위 `hubspot:conv:` 규칙으로는 안 걸립니다 — 열쇠가 다릅니다.
    #
    # 그대로 두면 셋이 깨집니다: `last_sent_reply` 가 「지난 회신」으로 그 일곱 글자를
    # 집어 실제 회신을 앵커에서 밀어내고(리마인더를 건너뛰는 규칙이 이 줄에는 안 걸립니다
    # — `reminder` 표는 `messages` 쪽에만 있습니다), 초안 프롬프트에 우리가 그렇게 답한
    # 것처럼 실리며, `latest_customer_message` 의 「마지막 회신 뒤」 기준선이 앞당겨져
    # 직전에 온 고객 답장이 안 보이게 됩니다.
    #
    # 메일 자체는 `messages` 행으로 이미 이 목록에 있습니다(`reminder=True` 로).
    # 사본이 더 이른 시각을 들고 있으면 그것이 진짜다 — 접수가 만든 문의 행의 시각은 고객이 쓴 때가
    # 아니라 우리가 받아 적은 때다(잠든 서버 · 놓친 웹훅이면 몇 분~몇 시간 늦다, 2026-09-28).
    earliest: dict[int, datetime] = {}
    for item in interactions:
        source = drawn_by_messages.get(item.id)
        if source is not None and item.happened_at is not None:
            at = _naive(item.happened_at)
            earliest[source.id] = min(at, earliest.get(source.id, at))
    turns: list[_Turn] = []
    for row in messages:
        at = row.sent_at or row.created_at
        if row.id in earliest:
            at = min(_naive(at), earliest[row.id])
        turns.append(_Turn(
            at=_naive(at),
            direction="inbound" if row.direction == "inbound" else "outgoing",
            subject=row.subject,
            body=row.body or "",
            reminder=row.prompt_variant in REMINDER_VARIANTS,
            source_ref=f"message:{row.id}",
            role=turn_role(row, non_sales=non_sales),
            channel=row.channel or "",
            origin=turn_origin(row),
        ))
    for item in interactions:
        if item.id in drawn_by_messages:
            continue
        if (item.external_id or "").startswith(REMINDER_NOTE_PREFIX):
            continue
        body = (item.summary or "").strip() or (item.subject or "").strip()
        if not body:
            continue
        turns.append(_Turn(
            at=_naive(item.happened_at),
            # 옛 철자를 같은 말로. CRM 수집기는 `incoming`/`outbound` 를 씁니다.
            direction={"incoming": "inbound", "outbound": "outgoing"}.get(
                item.direction or "note", item.direction or "note"
            ),
            subject=item.subject,
            body=body,
            source_ref=f"interaction:{item.id}",
            role=turn_role(item, non_sales=non_sales),
            channel=item.channel or "",
            origin=turn_origin(item),
        ))
    turns.sort(key=lambda turn: (turn.at, turn.source_ref))
    return turns


def _naive(value: datetime | None) -> datetime:
    """정렬만 하면 되므로 시간대를 떼어 맞춥니다.

    한 표는 시간대가 붙은 값을, 다른 표는 안 붙은 값을 돌려줄 수 있습니다(수집기는 UTC
    aware 로 쓰고, 열은 시간대 없는 DateTime 입니다). 섞이면 정렬이 TypeError 로 죽습니다.
    """
    if value is None:
        return datetime.min
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def latest_customer_message(conv_id: int | None, *, events=None) -> _Turn | None:
    """마지막으로 나간 우리 **영업** 회신 뒤에 온 고객 메시지. 없으면 None.

    이것이 후속 초안의 갈림길입니다 (운영자 지시): 있으면 **그 메시지에 답하고**, 없으면
    「답이 없을 때 다시 보내는 메일」(nudge)입니다 — 무엇을 쓸지는 콘솔 문서가 정합니다. 기준선은
    `last_sent_reply` 와 같은 자(`role == "sales"`)입니다 —
    챗봇 답이나 CS 안내로 끊으면 그 뒤에 숨은 고객 질문이 「이미 답한 말」이 됩니다.
    """
    events = thread_events(conv_id) if events is None else events
    after = 0
    for index, turn in enumerate(events):
        if turn.role == "sales":
            after = index + 1
    for turn in reversed(events[after:]):
        if turn.direction == "inbound":
            return turn
    return None


def last_sent_reply(conv_id: int | None, *, events=None) -> _Turn | None:
    """마지막으로 나간 **우리 영업의 이메일 회신**. 「이미 적은 말을 되풀이하지 마라」의 근거이고,
    없으면 첫 회신입니다(`_is_first_reply`).

    챗봇 답 · CS 주소의 안내(`NON_SALES_SENDER_ADDRESSES`) · 리마인더 · 전화·미팅 기록은 회신이
    아닙니다(`history_view.is_sales_email`). 2026-10-06 운영 재생에서 영업이 한 통도 안 보낸 티켓 42건이
    그 줄들 때문에 「이미 답함」으로 읽혀 첫 회신 문서 대신 후속 회신 문서를 받았습니다.
    """
    for turn in reversed(thread_events(conv_id) if events is None else events):
        if turn.role == "sales":
            return turn
    return None


def customer_turns_since(events: list[_Turn], since: datetime, *, seen=()) -> list[_Turn]:
    """``since`` 뒤에 **고객이** 보낸 것 — 「초안이 못 본 말」의 기준입니다.

    초안이 근거로 삼은 대화가 그 뒤에 바뀌었는지는 이것으로 잽니다. 대화 전체의 해시로
    재면 안 됩니다: 10분 폴러의 스레드 수집(``ticket_history``)이 **최초 문의의 사본**을
    ``hubspot:conv:`` 줄로 넣는데, 문의 ``messages`` 행에는 스레드 id 가 없어 그 사본은
    안 걸러집니다 — 그러면 초안이 서고 수집이 지나간 **모든 New 티켓**이 「대화가
    변경되었습니다」로 승인이 막히고, 빠져나갈 길은 다시 쓰기(운영자 편집이 사라진다)
    뿐입니다. 운영자가 그 티켓에 「수신」 기록을 적어도, 개인함 수집이 옛 메일을 붙여도
    같은 일이 났습니다. 그 줄들은 초안이 답해야 할 새 말이 아닙니다.

    그래서 재는 것은 **초안이 못 본(``seen`` 에 없는) 고객 메시지 중 시각이 초안 뒤인 것**
    뿐입니다. 사본은 원래 시각을 들고 오므로 안 걸리고, 초안 뒤에 온 답장·정정은 걸립니다.
    우리 회신·기록 줄은 세지 않습니다. ``since`` 는 시간대 없는 UTC(``_naive`` 가 turn.at 에
    쓰는 것과 같은 자)이고, ``seen`` 은 초안이 읽었던 turn 의 ``source_ref`` 들입니다.
    """
    seen = set(seen)
    return [turn for turn in events
            if turn.direction == "inbound" and turn.source_ref not in seen and turn.at > since]


def utcnow_naive() -> datetime:
    """``customer_turns_since`` 의 자 — ``_naive`` 와 같은 시간대(UTC, tz 없음)입니다."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _answered_turn(events: list[_Turn], first_reply: bool) -> _Turn | None:
    """이번 회신이 답할 고객 메시지. None 이면 티켓의 문의(``contact_info["last_message"]``)에 답합니다.

    - 후속 회신: 우리 영업의 마지막 이메일 뒤에 온 고객 메시지(``latest_customer_message``).
    - 첫 회신: 티켓의 문의. 그 뒤 고객이 메일로 더 쓴 말(정정 · 추가 질문)이 있으면 그것 — **채팅 줄과 폼
      제출은 맥락입니다.** 첫 회신의 기준선은 이제 챗봇 답 · CS 안내에서 안 끊기므로(``role``), 챗봇과
      주고받은 마지막 한마디(「감사합니다」)가 「가장 최근 문의」가 되면 그 한마디에 답합니다.
    """
    if not first_reply:
        return latest_customer_message(None, events=events)
    return next((turn for turn in reversed(events) if turn.role == "customer" and turn.channel != "채팅"), None)


_FORM_CHANNELS = ("폼", "form")


def _language_sample(contact_info: dict) -> str:
    """대화 언어를 잴 글 — 문의 본문. 본문이 서너 낱말도 안 되면 티켓 이름을 같이 잽니다.

    폼 문의의 티켓 이름은 대개 고객이 쓴 제목이라, 「video」 한 낱말보다 「estudo」 가 고객의 언어를 말합니다
    (2026-10-07 평가 R429 — 브라질 고객이 en 으로 저장되면 회신도 리마인더도 영어로 갑니다). CS 가 붙인 내부
    이름(「[Form] … > 엔터프라이즈 전달」 · 「[Chatbot] …」 · 한글 이름)은 고객의 말이 아니라 안 씁니다.
    """
    body = (contact_info.get("last_message") or "").strip()
    subject = (contact_info.get("subject") or "").strip()
    if len(body.split()) >= 4 or not subject or subject.startswith("[") or re.search(r"[가-힣]", subject):
        return body
    return f"{subject}\n{body}"


def subject_sources(conv_id: int | None, *, factory=None) -> tuple[str | None, str | None]:
    """(이 대화에서 가장 최근에 오간 이메일의 제목, 고객이 폼에 쓴 제목) — ``choose_reply_subject`` 의 재료.

    대화(``thread_events``)가 아니라 **표에서** 읽습니다: 고객의 첫 메일과 폼 제출의 허브스팟 사본은 대화에서
    접수 행의 사본으로 접히는데(``message_copies``), 고객이 쓴 제목은 그 사본에만 있습니다. 접수 행은 안 봅니다 —
    그 제목은 허브스팟 티켓 이름이고, 티켓 이름은 CS 가 붙인 내부 이름(「[Form] … > 엔터프라이즈 전달」)이거나
    챗봇 꼬리표입니다. 우리 쪽은 실제로 나간 콘솔 회신만 봅니다(초안 · 승인 대기는 고객이 못 본 제목입니다).
    """
    if not conv_id:
        return None, None
    from ..db.history_view import EMAIL_CHANNELS

    with (factory or SessionLocal)() as session:
        conv = session.get(Conversation, conv_id)
        if conv is None:
            return None, None
        rows = [(row.sent_at or row.created_at, "email", row.subject) for row in session.query(Message).filter(
            Message.conversation_id == conv_id, Message.direction == "outgoing",
            Message.status.in_(_SENT_STATUSES), Message.subject.isnot(None),
        )]
        rows += [(row.happened_at, row.channel, row.subject) for row in session.query(CustomerInteraction).filter(
            CustomerInteraction.conversation_id == conv_id,
            CustomerInteraction.contact_id == conv.contact_id,
            CustomerInteraction.channel.in_((*EMAIL_CHANNELS, *_FORM_CHANNELS)),
            CustomerInteraction.subject.isnot(None),
        )]
    newest = sorted(((_naive(at), channel, subject.strip()) for at, channel, subject in rows if subject.strip()),
                    key=lambda row: row[0], reverse=True)
    thread = next((subject for _at, channel, subject in newest if channel in EMAIL_CHANNELS), None)
    form = next((subject for _at, channel, subject in newest if channel in _FORM_CHANNELS), None)
    return thread, form


def _situation_text(situation: str, previous: _Turn | None, events: list[_Turn], now: datetime) -> str:
    """초안 프롬프트의 「이번 회신의 상황」 — 대화 기록에서 확인한 **사실만**. 무엇을 쓰라는 말은 없습니다."""
    lines = [f"- 상황: {situation} — {_SITUATION_FACTS[situation]}"]
    # 문서는 회신을 차수로 셉니다(「1차 회신에서는 …」). 몇 번째 메일인지가 있어야 첫 회신에만 걸린 조건을 가립니다.
    sent = sum(1 for turn in events if turn.role == "sales")
    lines.append(f"- 이번 메일은 우리 영업이 이 대화에서 보내는 {sent + 1}번째 이메일입니다.")
    if previous is not None and previous.at != datetime.min:
        days = max(0, (now - previous.at).days)
        lines.append(f"- 우리 영업의 마지막 이메일: {days}일 전({previous.at.date().isoformat()}, UTC)")
    latest_in = next((turn for turn in reversed(events) if turn.direction == "inbound"), None)
    if latest_in is not None and latest_in.at != datetime.min:
        days = max(0, (now - latest_in.at).days)
        lines.append(f"- 고객의 마지막 메시지: {days}일 전({latest_in.at.date().isoformat()}, UTC)")
    roles = {turn.role for turn in events}
    if "bot" in roles:
        lines.append("- 이 대화에는 챗봇의 자동 답변이 있습니다 — 우리 영업의 답이 아닙니다.")
    if "cs" in roles:
        lines.append("- 이 대화에는 CS 주소가 보낸 안내 메일이 있습니다 — 우리 영업의 답이 아닙니다.")
    if previous is not None and (previous.body or "").strip():
        text = text_wash(previous.body)
        clipped = text[:_PREVIOUS_CHARS] + (" [이하 생략]" if len(text) > _PREVIOUS_CHARS else "")
        lines.append(f'- 우리 영업이 마지막으로 보낸 이메일 본문(고객이 이미 받은 글입니다):\n"""\n{clipped}\n"""')
    return "\n".join(lines)


def _company_knowledge(documents, situation: str) -> tuple[str, dict]:
    """(초안 호출의 system 글, 매니페스트용 기록).

    콘솔 문서를 정리한 것(``organizer.knowledge_for`` — 상황으로 거르지 않고 기밀 구간만 빠지며 금지
    용어는 지워집니다). 정리기가 없거나 실패하면 **예전처럼 문서 통째로** 갑니다(``render_rules``) — 조회
    실패를 빈 문맥으로 바꾸지 않습니다. 그때 매니페스트는 ``mode='full'`` 과 실패한 이유의 이름입니다.
    """
    try:
        from ..llm.organizer import knowledge_for

        found = knowledge_for(documents, situation)
    except Exception as exc:
        logger.warning("정리된 회사 문서를 못 읽어 문서 통째로 싣습니다 (%s).", type(exc).__name__, exc_info=True)
        trace = {"mode": "full", "map_sha": None, "section_ids": [], "error": type(exc).__name__}
        return render_rules(documents), trace
    trace = {"mode": found["mode"], "map_sha": found["map_sha"], "section_ids": list(found["section_ids"])}
    return found["text"], trace


def _precedents(conv_id: int | None, customer_text: str, situation: str, llm) -> dict:
    """우리 팀이 비슷한 상황에서 실제로 보낸 메일(``llm.precedents``). 없거나 실패하면 없이 씁니다 — 참고일 뿐이라
    그것 때문에 초안이 실패하면 안 됩니다."""
    if not conv_id:
        return {"text": "", "ids": []}
    try:
        # 가져오기도 같은 울타리 안 — 모듈이 없어도 · 가져오다 깨져도(ImportError 가 아닌 것까지) 초안은 섭니다.
        from ..llm.precedents import precedents_for

        found = precedents_for(conv_id, customer_text=customer_text, situation=situation, llm=llm) or {}
        return {"text": str(found.get("text") or ""), "ids": [str(i) for i in found.get("ids") or []]}
    except Exception:
        logger.warning("비슷한 상황의 지난 메일을 못 가져와 없이 씁니다 (conv=%s).", conv_id, exc_info=True)
        return {"text": "", "ids": []}


class ClassifyResult(BaseModel):
    category: str
    reasoning: str


class CompanyTypeResult(BaseModel):
    company_type: str
    confidence: str = ""
    reason: str = ""


class DraftResult(BaseModel):
    """초안 호출의 응답 — **본문은 모델이 쓴 이메일 전체이고 코드는 고쳐 쓰지 않습니다** (2026-10-06).

    ``answer_points`` · ``policy_quotes`` 는 점검용 기록입니다(숫자가 본문에서 빠졌나 · 인용이 문서에 있나,
    `draft_evidence`). 빈 본문은 스키마가 받되 ``_draft_reply`` 가 ``DraftEvidenceError`` 로 끝냅니다 — 보여
    줄 글이 없는 초안이라 큐가 재시도 없이 닫습니다.
    """

    # Server-owned provenance; JSON returned by the model cannot populate it.
    _context_manifest: dict = PrivateAttr(default_factory=dict)
    _policy_snapshot: PolicySnapshot | None = PrivateAttr(default=None)
    # 모델이 제안한 제목. 그대로 쓰지 않습니다 — 이어지는 이메일 스레드가 있으면 그 제목이 이기고, 없을 때만
    # 검사를 지나면 씁니다(`choose_reply_subject`).
    subject: str = ""
    body: str
    answer_points: list[AnswerPoint] = []
    policy_quotes: list[PolicyQuote] = []
    # 본문에 남긴 `[[…]]` 자리표시 — 운영자만 아는 사실(결제 링크·날짜·예외 결정)의 자리. 승인은 이 목록이
    # 아니라 본문을 직접 다시 훑어 막습니다(`reply_flags.unfilled_slots`).
    placeholders: list[str] = []
    language: str
    tone_notes: str = ""
    # 운영자가 읽을 한국어 대역. **모델이 채우지 않습니다** — `schema=` 는 응답을 파싱할
    # 때만 쓰이고(프롬프트가 JSON 모양을 따로 적습니다), 이 칸은 링크 정리가 전부
    # 끝난 뒤 `_draft_reply` 가 채웁니다. 본문이 이미 한국어면 빈 문자열입니다.
    body_ko: str = ""


class ReviewProblem(BaseModel):
    """재작성에 실을 문제 하나(`draft_evidence.repair_instruction`)."""

    quote: str = ""
    problem: str = ""
    fix: str = ""


class DraftReview(BaseModel):
    """보내기 전 검토(`inbound/review_draft`)의 답 — 고칠 것만. 없으면 빈 목록입니다.

    항목마다 적게 하는 대조표도 시험했습니다(2026-10-06). 모델이 예시 메일의 문장을 그 문장의 근거로 대며 전부
    「맞음」으로 채워, 오히려 찾는 것이 줄었습니다. 그래서 문제만 묻되, 예시는 근거가 아니라는 것과 「답할 수 있는데
    미룬 것」을 따로 짚게 합니다(`review_draft.md`).
    """

    problems: list[ReviewProblem] = []


class _RequestsResult(BaseModel):
    customer_requests: str = ""


class InboundAgent:
    """Handles inbound HubSpot events end-to-end."""

    def __init__(
        self,
        llm: LLMClient | None = None,
        hubspot: HubSpotClient | None | object = _DEFAULT_HUBSPOT,
    ) -> None:
        self.llm = llm or LLMClient()
        if hubspot is not _DEFAULT_HUBSPOT:
            self.hubspot = hubspot
            return
        try:
            self.hubspot = HubSpotClient()
        except HubSpotNotConfigured:
            self.hubspot = None

    def handle(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """Process an inbound webhook event. Returns summary dict or None if skipped."""
        contact_info = self._fetch_contact(event)

        # **이미 있는 초안으로 돌아온 작업은 단계로 막지 않습니다.** 이 관문은 「새 초안을
        # 저절로 만들지 마라」는 규칙이지 「이 초안을 마저 쓰지 마라」가 아닙니다. 막으면
        # 리스가 끊겨 돌아온 작업도, 운영자가 누른 「재생성」도 그 자리에서 조용히 멈추고
        # 메시지는 ``drafting`` 인 채로 남습니다. 단계가 정말 넘어갔다면 완성된 초안을
        # ``_retire_superseded_drafts`` 가 곧바로 종료시키므로 나갈 길은 어차피 없습니다.
        expected_stage = settings.HUBSPOT_TICKET_STAGE_NEW.strip()
        if (
            expected_stage
            and not event.get("_draft_message_id")
            and contact_info.get("ticket_id")
            and contact_info.get("ticket_stage") != expected_stage
        ):
            logger.info(
                "Inbound skipped — ticket %s is stage %s, expected new stage %s.",
                contact_info.get("ticket_id") or "-",
                contact_info.get("ticket_stage") or "unknown",
                expected_stage,
            )
            return {
                "message_id": None,
                "status": "skipped_not_new",
                "object_id": contact_info.get("object_id"),
            }

        if not (contact_info.get("last_message") or "").strip():
            logger.warning(
                "Inbound skipped — empty body (ticket=%s contact=%s). The ticket has "
                "no subject/content to reply to; add the inquiry text to the ticket.",
                contact_info.get("ticket_id") or "-",
                contact_info.get("object_id", "?"),
            )
            return {
                "message_id": None,
                "status": "skipped_no_body",
                "object_id": contact_info.get("object_id"),
            }

        # Ticket retries must never create a second usable reply. For contact-only
        # events, only an unfinished draft blocks the next genuine customer message.
        resume_message_id = event.get("_draft_message_id")
        existing = self._existing_pending_draft_id(contact_info, resume_message_id)
        if existing is not None:
            logger.warning(
                "Inbound skipped — a draft (msg %d) is already awaiting action in the same "
                "thread (ticket=%s contact=%s). Approve/reject it before a new draft is made.",
                existing,
                contact_info.get("ticket_id") or "-",
                contact_info.get("object_id", "?"),
            )
            return {
                "message_id": existing,
                "status": "skipped_existing_pending",
                "object_id": contact_info.get("object_id"),
            }

        channel = self._pick_channel(contact_info)

        # 이어 쓰는 초안이 무엇인지 — 리스가 끊겨 돌아온 New 초안 · 「초안 다시 쓰기」 · 「메일 발송」의 후속 초안.
        resumed_variant, stored_lang, stored_category = self._resumed_draft(resume_message_id)
        follow_up = resumed_variant == _MANUAL_VARIANT

        # Detect the inquiry language once, up front. Every operator-approved reply
        # in this thread must go out in this language; that is enforced in code.
        #
        # **이어 쓰는 초안은 대화에 저장된 언어로 씁니다** (2026-10-06). 그 칸은 「첫 문의가 정하고
        # 그대로 둔다」이고(`_persist_placeholder`), 티켓 본문을 다시 재면 후속 회신의 언어가 모델 한 번의
        # 판별에 다시 걸립니다. 저장된 값이 없을 때(백필 티켓)만 잽니다.
        from ..llm.language import detect_language

        inquiry_lang = stored_lang or detect_language(_language_sample(contact_info), llm=self.llm)
        contact_info["inquiry_language"] = inquiry_lang

        # Persist the inquiry + a "drafting" placeholder up front so the ticket shows
        # on the site immediately, before the (slower) AI reply draft is ready. The
        # placeholder flips to pending_approval once the draft finishes.
        message_id, conv_id, inbound_message_id = self._persist_placeholder(
            contact_info,
            channel,
            inquiry_lang,
            resume_message_id=resume_message_id,
            inbound_job_id=event.get("_inbound_job_id"),
        )

        # 티켓 요약은 **일어난 일마다 한 줄**입니다. 문의가 실제로 저장됐을 때만 붙입니다 —
        # 티켓 하나에 이벤트가 여러 번 오고, 본문을 저장하는 것은 그중 한 번뿐입니다.
        append_summary_line(inbound_message_id)

        # 이 대화에는 이미 회신이 있습니다. 자동 초안은 첫 회신 한 번뿐이고, 이후는 사람이
        # 직접 등록합니다. 고객 문의는 위에서 이미 기록됐으므로 화면에는 그대로 보입니다.
        if message_id is None:
            logger.info(
                "Inbound recorded without a draft — conv %s already has a reply. "
                "이후 회신은 사람이 등록합니다.",
                conv_id,
            )
            return {
                "message_id": None,
                "status": "skipped_reply_exists",
                "object_id": contact_info.get("object_id"),
            }

        # Record the inquiry before the slower AI draft work.
        #
        # **워크북에는 새 문의만 실립니다** (2026-10-06). 「초안 다시 쓰기」(`event_type == "redraft"`)와
        # 「메일 발송」의 후속 초안도 이 길을 지나는데, 그것은 새 문의가 아닙니다 — 시트 행이 없는 티켓(백필
        # 티켓 300여 건)이면 그 자리에서 New · Inquiry 행이 영업팀 공용 워크북에 섰습니다(평가 F356 · F427).
        # 리스가 끊겨 돌아온 New 티켓의 첫 초안은 그대로 싣습니다 — 앞 시도가 행을 못 남겼을 수 있습니다.
        if event.get("event_type") != "redraft" and not follow_up:
            self._mirror_new_inbound_to_sheet(contact_info, channel, message_id, conv_id)

        # 한국어가 아닌 문의를 지금 옮겨 둡니다. 운영자가 이 티켓을 열 때는 이미 행에
        # 들어 있도록, 아래의 더 느린 초안 작성 전에 처리합니다.
        try:
            cache_korean_inquiries(conversation_id=conv_id)
        except Exception:
            # 못 옮겨도 파이프라인은 갑니다. 화면은 원문을 보여 주고 폴러가 다시 집습니다.
            logger.warning("문의 번역을 미리 해 두지 못했습니다 (conv=%s)", conv_id, exc_info=True)

        # History, deal, and domain research enriches the reviewable draft.
        self._enrich_draft_context(contact_info)

        classification = None
        draft = None
        try:
            # **후속 회신은 문의 유형을 다시 분류하지 않습니다** (2026-10-06). 유형은 스레드의 성질이고
            # (목록이 그 값을 그립니다), 분류가 읽는 것은 어차피 최초 문의(티켓 본문)라 다시 하면 같은 답이거나
            # 모델이 흔들린 답입니다 — 그 답을 `_finalize_draft` 가 대화에 덮어썼습니다. 저장된 유형이 없을
            # 때(백필 티켓)만 분류해 채웁니다.
            if follow_up and stored_category:
                classification = ClassifyResult(category=stored_category, reasoning="")
            else:
                classification = self._classify(contact_info)

            if (
                settings.HUBSPOT_UPDATE_CONTACT_INBOUND_STATUS
                and self.hubspot
                and contact_info.get("object_id")
            ):
                try:
                    self.hubspot.update_inbound_status_sync(contact_info["object_id"], "analyzed")
                except Exception:
                    logger.warning(
                        "Failed to set inbound_status=analyzed for %s",
                        contact_info["object_id"],
                        exc_info=True,
                    )

            draft = self._draft_reply(contact_info, classification, conv_id, inquiry_lang)
            awaiting_review = self._finalize_draft(
                message_id, contact_info, classification, draft, conv_id, inquiry_lang
            )
        except Exception:
            # Don't leave the card spinning forever — surface the failure.
            self._mark_draft_failed(message_id)
            raise

        # 고객 요청사항만 새로 뽑습니다(best-effort). 티켓 요약은 위에서 문의 한 줄이
        # 이미 붙었고, 우리 답은 **정말 나갔을 때** send_worker 가 붙입니다.
        self._extract_requests(conv_id, contact_info)

        # The draft is always pending_approval now — the old `if auto_approved: dispatch
        # else: notify` fork went with the auto-approval branch. The one exception is a
        # draft that finished after its ticket had already moved on: it was closed in
        # _finalize_draft, and asking a human to review a closed draft is worse than
        # saying nothing.
        if awaiting_review:
            try:
                notify_approval_once(
                    message_id=message_id,
                    subject=draft.subject,
                    body_snippet=draft.body,
                    category=classification.category,
                    title="새 인바운드 문의 — 회신 검토 요청",
                    inquiry=contact_info.get("last_message"),
                    contact_name=contact_info.get("full_name"),
                    contact_company=contact_info.get("company"),
                    contact_email=contact_info.get("email"),
                )
            except Exception:
                logger.warning(
                    "Approval notification failed for message %d.", message_id, exc_info=True
                )

        # A webhook-processed ticket must also be marked for the polling fallback;
        # otherwise the next poll can treat the same HubSpot ticket as unseen.
        if contact_info.get("ticket_id"):
            try:
                from .inbound_poller import _mark_ticket_processed

                _mark_ticket_processed(str(contact_info["ticket_id"]))
            except Exception:
                logger.warning(
                    "Failed to persist ticket dedup marker for %s.",
                    contact_info["ticket_id"],
                    exc_info=True,
                )

        logger.info(
            "Inbound processed: contact_id=%s category=%s msg_id=%d",
            contact_info.get("object_id", "unknown"),
            classification.category,
            message_id,
        )

        return {
            "message_id": message_id,
            "category": classification.category,
            "channel": channel,
        }

    def _mirror_new_inbound_to_sheet(
        self, contact_info: dict, channel: str, message_id: int, conv_id: int
    ) -> None:
        """Append the new inquiry once and remember its exact external row."""
        try:
            from .sheet_sync import reserve_inbound_client_id
            from ..integrations.google_sheets import record_inbound

            reserved_client_id = reserve_inbound_client_id(conv_id)

            with SessionLocal() as session:
                conv = session.get(Conversation, conv_id)
                if not conv or conv.sheet_inbound_row:
                    return
                contact = session.get(Contact, conv.contact_id)
                profile = session.get(CustomerProfile, conv.contact_id)
                inquiry_key = conv.sheet_inquiry_key
                legacy_inquiry_keys = session.scalars(
                    select(Conversation.sheet_inquiry_key).where(
                        Conversation.id != conv.id,
                        Conversation.sheet_client_id == reserved_client_id,
                        Conversation.sheet_inbound_row.isnot(None),
                        Conversation.sheet_inquiry_key.isnot(None),
                    )
                ).all()

            when = datetime.now(timezone.utc)
            raw_when = contact_info.get("occurred_at")
            try:
                if isinstance(raw_when, (int, float)) or str(raw_when or "").isdigit():
                    stamp = float(raw_when)
                    when = datetime.fromtimestamp(stamp / 1000 if stamp > 10_000_000_000 else stamp, timezone.utc)
                elif raw_when:
                    when = datetime.fromisoformat(str(raw_when).replace("Z", "+00:00"))
            except (ValueError, OSError, OverflowError):
                logger.debug("Could not parse inbound occurred_at=%r; using now.", raw_when)

            excerpt = (contact_info.get("last_message") or "").strip().replace("\n", " ")
            result = record_inbound(
                {
                    "client_id": reserved_client_id,
                    "inquiry_key": inquiry_key,
                    "_legacy_inquiry_keys": legacy_inquiry_keys,
                    "sales_direction": "Inbound",
                    "inquiry_date": when.date().isoformat(),
                    "deal_stage": "New",
                    "deal_stage_detail": "Inquiry",
                    # `pipeline` 은 안 보냅니다 (이관 0104). 그 칸은 구독 플랜을 읽는
                    # 수식이고, 행을 쓴 직후 `_write_pipeline_formula` 가 다시 깝니다 —
                    # 여기서 무엇을 실어 보내든 시트에 남은 적이 없습니다.
                    "company": contact_info.get("company") or "알 수 없음",
                    "full_name": contact_info.get("full_name", ""),
                    "phone": contact_info.get("phone") or "알 수 없음",
                    "email": contact_info.get("email", ""),
                    # HubSpot's IP-derived country, in the language the column is in.
                    "country": country_in_korean(contact_info.get("ip_country"))
                    if contact_info.get("ip_country")
                    else (contact_info.get("country") or UNKNOWN_COUNTRY),
                    "company_type": (profile.industry if profile else None)
                    or self._company_type(contact_info),
                    "channel": "허브스팟" if contact_info.get("object_id") else channel,
                    "plan": normalise_plan(profile.current_plan if profile else None),
                    "user_seq": profile.user_seq if profile else "",
                    "source": profile.source if profile else "",
                    "history": excerpt[:2000],
                    "inquiry_month": when.strftime("%Y-%m"),
                    "inquiry_quarter": f"{when.year}-Q{(when.month - 1) // 3 + 1}",
                }
            )
            if not result:
                return
            with SessionLocal() as session:
                conv = session.get(Conversation, conv_id)
                if not conv:
                    return
                contact = session.get(Contact, conv.contact_id)
                conv.sheet_inbound_row = result.row
                conv.sheet_client_id = result.client_id
                if contact and result.client_id and not contact.sheet_client_id:
                    contact.sheet_client_id = result.client_id
                session.commit()
        except Exception:
            logger.warning("Sheet mirror skipped/failed for msg %d.", message_id, exc_info=True)

    def _resumed_draft(self, message_id: int | None) -> tuple[str | None, str | None, str | None]:
        """이어 쓰는 초안의 (prompt_variant, 대화에 저장된 문의 언어, 저장된 문의 유형). 새 초안이면 셋 다 None."""
        if not message_id:
            return None, None, None
        with SessionLocal() as session:
            msg = session.get(Message, message_id)
            conv = session.get(Conversation, msg.conversation_id) if msg else None
            return (
                msg.prompt_variant if msg else None,
                (conv.inquiry_language or None) if conv else None,
                (conv.inquiry_category or None) if conv else None,
            )

    def _existing_pending_draft_id(
        self, contact_info: dict, resume_message_id: int | None = None
    ) -> int | None:
        """Return a reply that means this event should not create another draft.

        Thread key: ticket_id if present, otherwise the contact (looked up by
        normalized email, falling back to hubspot_contact_id). A ticket represents
        one inquiry, so any usable reply blocks retries. Contact-only conversations
        may receive real later replies, so only unfinished drafts block them.
        """
        ticket_id = contact_info.get("ticket_id")
        session = SessionLocal()
        try:
            conv_id: int | None = None
            if ticket_id:
                conv = session.query(Conversation).filter_by(hubspot_ticket_id=ticket_id).first()
                conv_id = conv.id if conv else None
            else:
                email = contact_info.get("email", "")
                norm = _normalize_email(email) if email else ""
                contact = (
                    session.query(Contact).filter_by(normalized_email=norm).first()
                    if norm
                    else None
                )
                if not contact and contact_info.get("object_id"):
                    contact = (
                        session.query(Contact)
                        .filter_by(hubspot_contact_id=str(contact_info["object_id"]))
                        .first()
                    )
                if contact:
                    # Match contact-keyed conv only (mirror the fix in _persist).
                    conv = (
                        session.query(Conversation)
                        .filter_by(contact_id=contact.id, hubspot_ticket_id=None)
                        .first()
                    )
                    conv_id = conv.id if conv else None

            if conv_id is None:
                return None

            status_filter = (
                Message.status.notin_(["draft_failed", "rejected"])
                if ticket_id
                else Message.status.in_(["drafting", "pending_approval", "approved"])
            )
            existing = (
                session.query(Message)
                .filter(
                    Message.conversation_id == conv_id,
                    Message.direction == "outgoing",
                    status_filter,
                    (Message.prompt_variant.is_(None))
                    | (Message.prompt_variant != "auto_ack"),
                )
                .order_by(Message.created_at.desc())
                .first()
            )
            if (
                existing
                and existing.id == resume_message_id
                and existing.status in {"drafting", "draft_failed"}
            ):
                return None
            return existing.id if existing else None
        finally:
            session.close()

    def _fetch_contact(self, event: dict) -> dict[str, Any]:
        info: dict[str, Any] = {
            "object_id": event.get("object_id", ""),
            "email": event.get("email", ""),
            "full_name": event.get("full_name", "Unknown"),
            "company": event.get("company", ""),
            "country": event.get("country", ""),
            "lifecycle_stage": event.get("lifecycle_stage", ""),
            # ``subject`` = the customer's own subject line (kept separate so the UI
            # shows it AND so the reply subject can be "RE: <subject>"). ``last_message``
            # = the body we reply to (content, falling back to subject if empty).
            "subject": event.get("subject", ""),
            "last_message": event.get("last_message", ""),
            "phone": event.get("phone"),
            "ticket_id": event.get("ticket_id"),
            "ticket_stage": event.get("ticket_stage"),
            "recent_emails": "",
            "deal_summary": "",
            "domain_profile": None,
        }

        if not self.hubspot:
            info["inbound_source"] = "event_payload" if info["last_message"] else "none"
            return info

        contact_id = info["object_id"]
        if not contact_id:
            return info

        try:
            hs_contact = self.hubspot.get_contact_sync(contact_id)
            full_name = " ".join(filter(None, [hs_contact.firstname, hs_contact.lastname]))
            if full_name:
                info["full_name"] = full_name
            info["email"] = hs_contact.email or info["email"]
            info["company"] = hs_contact.company or info["company"]
            info["country"] = hs_contact.country or info["country"]
            info["phone"] = hs_contact.phone or info["phone"]
            info["lifecycle_stage"] = hs_contact.lifecyclestage or info["lifecycle_stage"]
        except Exception:
            logger.warning("HubSpot contact fetch failed, using event payload.", exc_info=True)

        # Ticket events carry the inbound body directly (subject + content). When
        # present, we trust the ticket over the form/email/note fallbacks because
        # that's the explicit source the operator created in HubSpot.
        ticket_id = info.get("ticket_id")
        if ticket_id and self.hubspot:
            try:
                ticket = self.hubspot.get_ticket_sync(ticket_id)
                info["ticket_stage"] = ticket.pipeline_stage
                # The reply goes to the address the TICKET is held against, not to
                # whatever the contact record says. A contact can carry an older or
                # personal address; the ticket's is the one the inquiry arrived on, and
                # the operator's rule is that the answer belongs to the ticket.
                ticket_email = ticket.contact_email
                if ticket_email and ticket_email != info.get("email"):
                    logger.info(
                        "Ticket %s: replying to its own address %s (contact record says %s).",
                        ticket_id,
                        ticket_email,
                        info.get("email") or "-",
                    )
                if ticket_email:
                    info["email"] = ticket_email
                if ticket.subject and not info["subject"]:
                    info["subject"] = ticket.subject
                # Body to reply to = ticket content; fall back to the subject so a
                # subject-only ticket ("가끔 제목만 오더라") is never treated as empty.
                body = ticket.content or ticket.subject or ""
                if body and not info["last_message"]:
                    info["last_message"] = body
                    info["inbound_source"] = "ticket"
                    logger.info(
                        "Inbound message from ticket %s for contact %s", ticket_id, contact_id
                    )
            except Exception:
                logger.warning("HubSpot ticket fetch failed for %s", ticket_id, exc_info=True)

        # Fetch actual message body: form submission → inbound email → note → event payload
        if not info["last_message"] and self.hubspot:
            inbound_source = None
            body = None
            try:
                body = self.hubspot.get_latest_form_submission(contact_id)
                if body:
                    inbound_source = "form_submission"
            except Exception:
                logger.debug("Form submission fetch failed for %s", contact_id)

            if not body:
                try:
                    body = self.hubspot.get_latest_inbound_email(contact_id)
                    if body:
                        inbound_source = "inbound_email"
                except Exception:
                    logger.debug("Inbound email fetch failed for %s", contact_id)

            if not body:
                try:
                    body = self.hubspot.get_latest_note(contact_id)
                    if body:
                        inbound_source = "note"
                except Exception:
                    logger.debug("Note fetch failed for %s", contact_id)

            if body:
                info["last_message"] = body
                info["inbound_source"] = inbound_source
                logger.info("Inbound message from %s for contact %s", inbound_source, contact_id)
            else:
                info["inbound_source"] = "event_payload"
                if not info["last_message"]:
                    logger.warning("No message body found for contact %s", contact_id)
        elif info["last_message"]:
            info["inbound_source"] = "event_payload"
        else:
            info["inbound_source"] = "none"

        return info

    def _enrich_draft_context(self, info: dict[str, Any]) -> None:
        """Add slower CRM/history/domain facts after the receipt email is sent."""
        contact_id = info.get("object_id")
        if self.hubspot and contact_id:
            try:
                emails = self.hubspot.get_recent_emails_sync(contact_id, limit=5)
                if emails:
                    # 출처를 붙입니다 — 대화 기록이 아니라 연락처에 달린 CRM 메일의 앞부분이고, 누가 보낸 것인지
                    # 이 목록은 모릅니다. 대화 기록(허브스팟 스레드)이 이미 들어온 티켓에서는 초안이 이 블록을 뺍니다.
                    snippets = []
                    for e in emails:
                        subj = e.subject or "(no subject)"
                        body = (e.body or "")[:200]
                        when = e.timestamp.date().isoformat() if e.timestamp else "날짜 미상"
                        snippets.append(f"- [{when}] {subj}: {body}")
                    if snippets:
                        info["recent_emails"] = "\n".join(
                            ["(참고 — 허브스팟 CRM 메일 기록: 대화 기록이 아니며 본문 앞 200자, 보낸 사람 미표시)",
                             *snippets])
                    # 이 티켓에 붙은 메일이 있으면 그 스레드의 제목 — 이메일로 들어온 문의의 첫 회신은 접수 때
                    # 우리 DB 에 아직 스레드가 없어서(수집은 몇 분 뒤) 이것이 「RE: <고객이 쓴 제목>」의 유일한 출처입니다.
                    ticket = str(info.get("ticket_id") or "")
                    on_ticket = [e.subject for e in emails
                                 if ticket and str(e.ticket_id or "") == ticket and (e.subject or "").strip()]
                    if on_ticket:
                        info["crm_thread_subject"] = on_ticket[-1]
            except Exception:
                logger.warning("HubSpot email history fetch failed.", exc_info=True)

            try:
                deals = self.hubspot.get_associated_deals_sync(contact_id)
                if deals:
                    parts = []
                    for d in deals:
                        parts.append(
                            f"- {d.name or 'Unnamed'} (stage: {d.stage or 'unknown'}, amount: {d.amount or 'N/A'})"
                        )
                    info["deal_summary"] = "\n".join(parts)
            except Exception:
                logger.warning("HubSpot deals fetch failed.", exc_info=True)

        email = info.get("email", "")
        # **회사를 알든 모르든 돕니다 — 실측이 그렇게 정했습니다** (2026-09-09).
        #
        # 한 번은 「허브스팟에 회사가 있으면 건너뛰자」로 바꿨다가 되돌렸습니다. 포털을
        # 세어 보니 그 전제가 틀렸습니다 (최근 연락처 100명):
        #
        #     contact.company    18/100 (18%)
        #     contact.industry    0/100 (0%)     ← 「기업 종류」에 쓸 값이 저쪽에 없습니다
        #     회사 레코드 연결    37/100
        #       그중 industry      1/36 (3%)
        #       그중 description   1/36 (3%)
        #       그중 website      33/36 (92%)
        #
        # 즉 **허브스팟은 「어떤 회사인지」를 사실상 모릅니다** — 이름조차 82%가 비어
        # 있고, 업종은 0%입니다. 건너뛰기로 아끼는 것은 문의 다섯 건에 하나 정도인데,
        # 그 하나에서 잃는 것은 초안이 참고할 유일한 회사 정보입니다.
        #
        # **비싸지도 않습니다**: 결과가 도메인당 90일 캐시라 새 회사에서만 돕니다
        # (`INBOUND_DOMAIN_REANALYZE_DAYS`). 실측으로 3일에 문의 5건 중 분석은 1건이었고
        # 나머지는 캐시 적중이었습니다.
        #
        # 허브스팟이 아는 것은 이미 넘기고 있습니다 — `hint_company` 로 들어가서 모델이
        # 홈페이지와 대조합니다. 그 힌트가 82% 비어 있다는 것이, 홈페이지가 사실상 유일한
        # 정보원이라는 뜻입니다.
        if email and settings.INBOUND_DOMAIN_ENRICHMENT_ENABLED:
            dom = _domain_from_email(email)
            if not is_personal_domain(dom):
                try:
                    from .domain_enrichment import analyze_domain

                    profile = analyze_domain(dom, llm=self.llm, hint_company=info.get("company"))
                    if profile is not None:
                        info["domain_profile"] = {
                            "domain": profile.domain,
                            "company_name": profile.company_name,
                            "industry": profile.industry,
                            "services": profile.services,
                            "target_market": profile.target_market,
                            "size_hint": profile.size_hint,
                            "confidence": profile.confidence,
                            "notes": profile.notes,
                        }
                except Exception:
                    logger.warning("Domain enrichment failed for %s", dom, exc_info=True)

    def _company_type(self, contact_info: dict) -> str:
        """기업 종류 for the workbook: what kind of organisation asked.

        HubSpot carries the contact's Company Name but not the category the sales team
        files by, so the name is evidence rather than the answer — and a personal address
        with no company is itself evidence (a 크리에이터, usually). The model picks from a
        closed list; anything off it, or any failure, files as 확인 안 됨 rather than
        inventing a value the column cannot be filtered on.
        """
        domain_profile = contact_info.get("domain_profile") or {}
        try:
            result = self.llm.complete(
                "inbound/company_type",
                {
                    "company": contact_info.get("company") or "",
                    "domain": contact_info.get("domain") or "",
                    "industry_hint": domain_profile.get("services")
                    or domain_profile.get("industry")
                    or "",
                    "inquiry": (contact_info.get("last_message") or "")[:1500],
                },
                schema=CompanyTypeResult,
            )
        except Exception:
            logger.warning("기업 종류 classification failed; filing as 확인 안 됨.", exc_info=True)
            return UNKNOWN_COMPANY_TYPE
        value = (result.company_type or "").strip()
        if value not in COMPANY_TYPES:
            logger.info("기업 종류 %r is not one the column offers; filing as 확인 안 됨.", value)
            return UNKNOWN_COMPANY_TYPE
        return value

    def _classify(self, contact_info: dict) -> ClassifyResult:
        return self.llm.complete(
            "inbound/classify",
            {
                "contact_name": contact_info["full_name"],
                "company": contact_info["company"],
                "country": contact_info["country"],
                "lifecycle_stage": contact_info["lifecycle_stage"],
                "last_message": contact_info["last_message"],
                "enrichment_context": _build_enrichment_context(contact_info),
            },
            schema=ClassifyResult,
        )

    def _pick_channel(self, contact_info: dict) -> str:
        # Email is the only reply channel.
        return "email" if contact_info.get("email") else "none"

    def _is_first_reply(self, conv_id: int | None, *, events=None) -> bool:
        """True if no real reply has gone out in this thread yet (auto-ack excluded).

        Drives which situation the draft is written for and which documents apply
        (``FIRST`` / ``FOLLOWUP``). Pending drafts don't count as "already replied" — only
        an actually-sent reply does.

        **허브스팟 화면에서 보낸 회신도 셉니다** (2026-09-07). ``messages`` 만 세던 동안
        저쪽에서 사람이 답한 티켓이 「첫 회신」으로 판정됐고, 그래서 금액 금지 가드가
        엉뚱한 자리에 걸렸습니다 — 이 표에는 이 콘솔이 보낸 것만 있습니다.

        **챗봇 답과 CS 안내는 세지 않습니다** (2026-10-06) — 셀 것은 우리 영업이 보낸 이메일
        하나입니다(`last_sent_reply`). 그 줄들로 「이미 답함」이 되면 첫 회신 문서를 잃고 후속
        회신 문서를 받습니다.

        「첫 회신인가」의 정의는 여기 **한 곳**입니다 — `_draft_reply` 도 이것을 씁니다.
        """
        if not conv_id:
            return True
        return last_sent_reply(conv_id, events=events) is None

    def _build_conversation_context(
        self,
        conv_id: int | None,
        latest_message: str | None,
        *,
        limit: int = 12,
        max_chars: int = 15000,
        events=None,
    ) -> str:
        """Return the rolling summary and latest completed turns for drafting.

        The current inbound message is already supplied separately to the prompt,
        so its newest matching row is omitted here. Keeping a small, recent window
        prevents long threads from crowding out the customer's latest intent.
        """
        if not conv_id:
            return ""
        try:
            with SessionLocal() as session:
                conv = session.get(Conversation, conv_id)
                summary = (conv.summary or "").strip() if conv else ""
                requests = (conv.customer_requests or "").strip() if conv else ""

            latest = text_wash(latest_message)
            skipped_latest = False
            prior: list[_Turn] = []
            # 최신부터 거꾸로 담습니다 — 긴 스레드에서 잘려 나가야 할 쪽은 오래된 쪽입니다.
            for turn in reversed(thread_events(conv_id) if events is None else events):
                if (
                    not skipped_latest
                    and latest
                    and turn.direction == "inbound"
                    and text_wash(turn.body) == latest
                ):
                    skipped_latest = True
                    continue
                prior.append(turn)
                if len(prior) >= limit:
                    break

            # Reserve the budget for recent original turns BEFORE derived summaries.
            # Label provenance; a customer's assertion is never system verification.
            # 말머리는 **누가** 말했나다(`_ROLE_LABELS`) — 챗봇 답 · CS 안내를 「우리」로 실으면 모델이
            # 그것을 이미 보낸 우리 영업 회신으로 읽고 이어 쓴다.
            parts: list[str] = []
            used = 0
            for turn in prior:  # newest first
                state = "고객 주장·미검증" if turn.direction == "inbound" else "대화 기록"
                label = _ROLE_LABELS.get(turn.role) or ("고객" if turn.direction == "inbound" else "기록")
                head = f"{label} [{turn.at.isoformat()} UTC; {turn.source_ref}; {state}]: "
                raw = text_wash(turn.body)
                remaining = max_chars - used - len(head) - 30
                if remaining <= 0:
                    break
                body = raw[:min(2500, remaining)]
                if len(body) < len(raw):
                    body += " [원문 일부 생략]"
                part = head + body
                parts.append(part)
                used += len(part) + 2
            parts.reverse()
            for label, value in (("파생 대화 요약·원문 우선", summary),
                                 ("파생 고객 요청사항·원문 우선", requests)):
                if value:
                    remaining = max_chars - used - len(label) - 25
                    if remaining <= 0:
                        break
                    part = f"{label}:\n{value[:remaining]}"
                    if len(value) > remaining:
                        part += " [요약 일부 생략]"
                    parts.append(part)
                    used += len(part) + 2
            return "\n\n".join(parts)[:max_chars]
        except Exception:
            logger.warning("Conversation context lookup failed for conv %s.", conv_id, exc_info=True)
            raise

    def _draft_reply(
        self,
        contact_info: dict,
        classification: ClassifyResult,
        conv_id: int | None = None,
        inquiry_lang: str | None = None,
    ) -> DraftResult:
        """Draft the reply — in the inquiry's language, with a Korean reading beside it.

        **What to write is the console's** (2026-10-06): the company documents, organized by
        ``llm.organizer`` into the parts that fit THIS situation, are the system text of the call,
        and the prompt (``inbound/draft_reply``) holds mechanics only. Code adds facts — which
        situation this is (``first_reply`` · ``answer_reply`` · ``nudge``), when our last sales
        e-mail went out, whether a chatbot or the CS address wrote — never sales advice. The
        pricing/promotion/follow-up instructions that lived here contradicted the documents.

        The model writes the whole body and code does not rewrite it. Code only:
        - puts it in the language it will be SENT in (``ensure_language``) and normalizes it the
          way approval does (``approval.prepare_reviewed_body`` — tokens, contact links, layout),
          so what the operator sees is what goes out;
        - picks the subject (``choose_reply_subject``) — the e-mail thread's own subject when one
          exists, never the HubSpot ticket name;
        - stores the Korean reading once (``korean_reading`` → ``messages.body_ko``).

        Checks do not block (2026-09-22) and are not shown (2026-10-06): one repair pass for the
        grounding checks (``check_draft`` + ``answer_point_gaps``), then whatever is left goes to
        the manifest as codes, no text — the operator reads every draft and asked for no warnings
        about it. The only ``DraftEvidenceError`` is an empty body — there is nothing to show, so
        the job stays terminal.
        """
        from ..llm.language import language_name
        from ..llm.reply import ensure_language, korean_reading
        from ..llm.translate import is_mostly_korean
        from .approval import prepare_reviewed_body

        generated_at = utcnow_naive()
        events = thread_events(conv_id)
        previous = last_sent_reply(conv_id, events=events)
        first_reply = self._is_first_reply(conv_id, events=events)
        stage = FIRST if first_reply else FOLLOWUP
        policy = PolicySnapshot.capture(stage)
        # 답할 글. 허브스팟이 준 `last_message` 는 **티켓 본문** = 최초 문의라, 후속 회신에서 그대로 두면 처음
        # 문의에 다시 답합니다 — 우리 마지막 이메일 뒤에 고객이 쓴 말이 있으면 그것으로 갈아 끼웁니다.
        answered = _answered_turn(events, first_reply)
        last_message = (answered.body if answered is not None else "") or contact_info["last_message"]
        situation = FIRST_REPLY if first_reply else (ANSWER_REPLY if answered is not None else NUDGE)

        context = self._build_conversation_context(conv_id, last_message, events=events)
        selection_trace: dict = {}
        select_relevant_docs(
            inquiry=last_message,
            category=classification.category,
            llm=self.llm,
            # 한국어 문의에는 KR 문서, 그 외에는 ENG 문서. 기본 메일 템플릿이 두 벌이라
            # 언어를 안 가리면 두 벌이 같이 붙습니다.
            language=inquiry_lang,
            # 「후속 회신에만」 문서는 첫 회신의 인덱스에 아예 안 실립니다 (0108). 모델에게
            # 「고르지 마라」라고 부탁하는 대신 보여 주지 않습니다.
            stage=stage,
            candidates=policy.candidates,
            conversation_context=context,
            selection_trace=selection_trace,
        )
        policy.assert_current()
        # 초안이 보는 문서 — 그 단계의 규칙 문서 전부와 라우터가 고른 참고 문서. 정리된 지식도 인용 검사도 이것으로.
        allowed_docs = [doc for doc in policy.documents if doc.mode == "rules"
                        or doc.id in selection_trace.get("selected_ids", [])]
        knowledge, knowledge_trace = _company_knowledge(allowed_docs, situation)
        precedents = _precedents(conv_id, last_message, situation, self.llm)
        # 대화 기록에 허브스팟 스레드 · 개인함 메일이 이미 들어와 있으면 CRM 메일 사본은 같은 메일을 한 번 더 싣는
        # 것입니다(같은 메일이 허브스팟에 객체 두 개로 삽니다 — CRM 이메일과 스레드 메시지).
        enrichment = dict(contact_info)
        if any(turn.origin in ("hubspot", "crm", "gmail") for turn in events):
            enrichment["recent_emails"] = ""
        reply_format = get_reply_format(inquiry_lang)
        draft_fields = {
                "contact_name": contact_info["full_name"],
                "company": contact_info["company"],
                "country": contact_info["country"],
                "category": classification.category,
                "last_message": last_message,
                "situation": _situation_text(situation, previous, events, generated_at),
                "conversation_context": context,
                "enrichment_context": _build_enrichment_context(enrichment),
                # The reply SHAPE (opening / middle / closing), edited in the console.
                # Read per draft, so a change there lands on the next reply.
                "reply_format": reply_format,
                "precedents": precedents["text"],
                # 초안이 나갈 언어. 참고 문서에 그 언어로 된 완성 메일이 있으면 모델이
                # 그 문장을 그대로 살려 쓸 수 있어야 합니다 — 한 번 한국어를 거치면
                # 그 문장은 되돌아오지 못합니다.
                "reply_language": language_name(inquiry_lang),
                "evidence_feedback": "",
        }
        customer_text = "\n".join(turn.body for turn in events if turn.direction == "inbound")
        customer_text += "\n" + last_message
        # **검사에 걸린 초안도 사람에게 간다** (2026-09-22 운영자 지시: 「미완성이라도
        # 사람한테 뜨면 좋겠는데. 그래야 내용보고 후에 고도화를 하든 하지」). 재작성
        # 한 번 뒤에도 남는 문제는 `issues` 에 들고 가서 매니페스트에만 적는다 — 화면에
        # 경고로 띄우지 않는다(2026-10-06 운영자: 「답변 작성에 대한 경고문 이런건 필요없어」).
        # 이 검사는 의미 인증이 아니라 문자열 검사라 과잉 판정이 있고, 판단은 사람이 본문을
        # 보고 한다. `DraftEvidenceError` 가 남는 자리는 본문이 비어 **보여 줄 글이 없을 때** 하나다.
        issues: list[str] = []
        problems: list[ReviewProblem] = []
        for attempt in range(2):
            draft = self.llm.complete(
                "inbound/draft_reply", draft_fields, schema=DraftResult, tier="pro",
                max_tokens=_DRAFT_MAX_TOKENS, thinking_level=_DRAFT_THINKING_LEVEL,
                # **회사 문서가 실리는 유일한 호출입니다** (2026-09-10). 「첫 회신에만」·「그 이후
                # 회신에」 문서가 `stage` 로 갈리고, 실리는 글은 그 스냅샷의 문서를 정리한 것입니다.
                # 나머지 호출(분류·라우팅·요약·번역)은 `stage` 를 안 주므로 아무것도 안 받습니다.
                stage=stage, policy_snapshot=policy, knowledge=knowledge,
            )
            if not (draft.body or "").strip():
                raise DraftEvidenceError("초안 본문이 비어 있습니다.")
            issues = check_draft(draft.body, draft.policy_quotes, documents=allowed_docs,
                                 customer_text=customer_text)
            issues += answer_point_gaps(draft.body, draft.answer_points)
            if attempt:
                break
            problems = self._review_draft(draft, draft_fields, stage=stage, policy=policy, knowledge=knowledge)
            if not issues and not problems:
                break
            draft_fields["evidence_feedback"] = repair_instruction(issues, problems=problems,
                                                                   previous=draft.body)

        # 나갈 언어로. **언어 라벨은 결과를 보고 붙입니다** — 번역이 실패하면 본문은 한국어로
        # 남는데, 라벨만 'en' 으로 찍으면 발송 관문이 통과시켜 한국어 메일이 영어 고객에게 갑니다.
        draft.body = ensure_language(draft.body, inquiry_lang, llm=self.llm)
        draft.language = "ko" if is_mostly_korean(draft.body) else (inquiry_lang or "ko")
        # 승인이 하는 다듬기와 **같은 함수**입니다 — 토큰 채우기 · 연락 링크 · 줄 정리. 번역 뒤입니다:
        # 번역이 주소를 고쳐 쓸 수 있습니다. 미리본 글 = 저장된 글 = 나가는 글.
        draft.body = prepare_reviewed_body(draft.body, inquiry_lang)

        # 제목 — 이어지는 이메일 스레드가 있으면 그 제목에 RE: 하나, 없으면 검사를 지난 모델 제안 · 고객이 폼에
        # 쓴 제목 · 기본 제목 순(`choose_reply_subject`). 허브스팟 티켓 이름은 후보가 아닙니다: CS 가 붙인 내부
        # 이름과 챗봇 꼬리표가 힌디어·영어 고객에게 그대로 나갔습니다(msg 72 · 110).
        thread_subject, form_subject = subject_sources(conv_id)
        crm_subject = None if thread_subject else contact_info.get("crm_thread_subject")
        draft.subject, subject_source = choose_reply_subject(
            thread_subject=thread_subject or crm_subject,
            proposed=draft.subject,
            customer_subject=form_subject,
            target=inquiry_lang,
            first_reply=first_reply,
            console_text=knowledge + "\n" + reply_format,
        )
        if subject_source == "thread" and crm_subject:
            subject_source = "crm_thread"

        # 운영자가 읽을 한국어 대역. **맨 마지막**입니다: 링크·토큰 정리가 끝난 뒤라야 두 벌이 같은 문장,
        # 같은 링크를 들고 대조가 됩니다. 한 번만 돌고 행에 저장되므로 화면을 열 때마다 모델을 부르지 않습니다.
        draft.body_ko = korean_reading(draft.body, llm=self.llm)
        # 변환(언어 보정·정리) 뒤의 검사는 **기록만 하고 막지 않습니다** (2026-09-22). 두 검사의
        # 문제를 합쳐 매니페스트에 적습니다 — 코드만 적고 고객 글은 안 적습니다.
        final_issues = check_draft(draft.body, draft.policy_quotes, documents=allowed_docs,
                                   customer_text=customer_text)
        final_issues += answer_point_gaps(draft.body, draft.answer_points)
        issues = sorted(set(issues) | set(final_issues))
        if issues:
            # info 입니다 — 경고 이상은 콘솔 「운영 로그」 탭에 뜨고, 이건 운영자에게 띄우지 않기로 한 표시입니다.
            logger.info("초안 근거 검사에 걸렸습니다 (conv=%s, issues=%s) — 매니페스트에 적고 그대로 대기열에 둡니다.",
                        conv_id, ",".join(issues))
        policy.assert_current()
        draft._policy_snapshot = policy
        draft._context_manifest = {
            "version": 1, "conversation_id": conv_id,
            "policy": policy.manifest(), "selection": selection_trace,
            # 「대화가 바뀌었나」의 기준 시각. 해시(`thread_sha256`)는 기록용이지 판정 기준이
            # 아닙니다 — `customer_turns_since` 의 docstring 이 그 이유입니다.
            "generated_at": generated_at.isoformat(),
            "thread_sha256": fingerprint(events),
            "turn_refs": [turn.source_ref for turn in events],
            "input_sha256": fingerprint({"latest": last_message, "context": context,
                                         "contact": contact_info}),
            "body_sha256": fingerprint(draft.body),
            "model": settings.GEMINI_MODEL_PRO, "api_surface": "vertex-ai",
            "prompt_sha256": fingerprint(load_prompt("inbound/draft_reply")),
            "schema_sha256": fingerprint(DraftResult.model_json_schema()),
            "limited_evidence_checks": {"status": "FAIL" if issues else "PASS", "issues": issues,
                                        "generation_attempts": attempt + 1,
                                        "quoted_source_ids": sorted({q.source_id for q in draft.policy_quotes}),
                                        "answer_points_count": len(draft.answer_points)},
            "semantic_validation": "NOT_RUN", "delivery_permission": "DRAFT_ONLY",
            # 2026-10-06 — 어느 상황으로 썼나 · 어떤 정리본을 읽었나(구간 id, 글은 안 적는다) · 제목의 출처 ·
            # 참고한 지난 메일.
            "situation": situation,
            "review_problems": len(problems),
            "knowledge": knowledge_trace,
            "subject_source": subject_source,
            "precedents": precedents["ids"],
        }
        return draft

    def _review_draft(self, draft, fields: dict, *, stage, policy, knowledge) -> list[ReviewProblem]:
        """보내기 전 검토 — 같은 회사 문서와 같은 대화로 초안을 한 번 더 읽습니다 (2026-10-06).

        2026-10-06 평가에서 초안의 치명적 실수는 대부분 **콘솔 문서에 이미 적힌 규칙**을 놓친 것이었습니다(물량으로
        연 약정을 단정 · 분할을 먼저 제안 · 고객이 답한 것을 다시 물음 · 하지 않은 처리를 했다고 씀). 그 규칙을 코드에
        옮기지 않습니다 — 검토가 읽는 기준도 초안이 읽은 그 문서(``knowledge``)라, 운영자가 문서를 고치면 검토의
        기준도 같이 바뀝니다. 찾은 문제는 초안의 재작성 한 번(``repair_instruction``)으로 고칩니다.

        실패하면 검토 없이 갑니다 — 초안은 그대로 쓸 수 있고, 검토는 고쳐 쓸 기회일 뿐입니다.
        """
        try:
            review = self.llm.complete(
                "inbound/review_draft",
                {
                    **{key: fields[key] for key in ("contact_name", "company", "country", "reply_language",
                                                    "last_message", "situation", "conversation_context",
                                                    "reply_format")},
                    # 제목은 대조하지 않습니다 — 이어지는 스레드가 있으면 그 제목이 이기고(`choose_reply_subject`),
                    # 실험에서 제목 줄의 말머리를 제목의 일부로 읽은 오탐이 재작성을 불렀습니다.
                    "draft_body": draft.body,
                },
                schema=DraftReview, tier="pro", max_tokens=_REVIEW_MAX_TOKENS,
                thinking_level=_REVIEW_THINKING_LEVEL,
                stage=stage, policy_snapshot=policy, knowledge=knowledge,
            )
            return [problem for problem in review.problems if problem.problem.strip()]
        except Exception:
            logger.info("초안 검토를 못 해 검토 없이 씁니다.", exc_info=True)
            return []

    def _persist_placeholder(
        self,
        contact_info: dict,
        channel: str,
        inquiry_lang: str,
        *,
        resume_message_id: int | None = None,
        inbound_job_id: int | None = None,
    ) -> tuple[int | None, int, int | None]:
        """Persist the inquiry and a drafting reply placeholder before the AI draft.

        Returns ``(reply_message_id, conversation_id, inbound_message_id)``.
        ``inbound_message_id`` is set only when this call actually wrote the customer's
        message — one ticket gets several events, and only the first of them stores the
        body. 티켓 요약에 불릿을 덧붙이는 쪽이 그 한 번을 알아야 합니다. The
        card appears on the site immediately as "작성중"; _finalize_draft fills it in
        once the reply is ready.

        ``reply_message_id`` is **None** when this thread already has a reply of its own:
        the automatic draft is the first reply only, and everything after it is registered
        by a person. The customer's message and the progress entry are still written — the
        thread stays complete, it just does not grow a second draft nobody asked for.
        """
        session = SessionLocal()
        try:
            email = contact_info.get("email", "")
            norm = _normalize_email(email) if email else ""

            contact = (
                session.query(Contact).filter_by(normalized_email=norm).first() if norm else None
            )
            if not contact:
                # Only store a domain for REAL company domains. Personal/free-email
                # senders (gmail, naver, …) must not be grouped together as one
                # "company" — that would leak one customer's history to another.
                dom = _domain_from_email(email) if email else None
                if dom and is_personal_domain(dom):
                    dom = None
                anonymous_key = (
                    contact_info.get("object_id")
                    or contact_info.get("ticket_id")
                    or hashlib.sha256(
                        f"{contact_info.get('full_name')}|{contact_info.get('last_message')}".encode()
                    ).hexdigest()[:20]
                )
                contact = Contact(
                    hubspot_contact_id=contact_info.get("object_id") or None,
                    email=email or None,
                    normalized_email=norm or f"unknown:{anonymous_key}",
                    full_name=contact_info["full_name"],
                    company=contact_info.get("company"),
                    domain=dom,
                    country=contact_info.get("country"),
                    lifecycle_stage=contact_info.get("lifecycle_stage"),
                    phone=contact_info.get("phone") or None,
                )
                session.add(contact)
                session.flush()
            else:
                contact.hubspot_contact_id = (
                    contact.hubspot_contact_id or contact_info.get("object_id") or None
                )
                contact.email = email or contact.email
                contact.full_name = contact_info.get("full_name") or contact.full_name
                contact.company = contact_info.get("company") or contact.company
                contact.country = contact_info.get("country") or contact.country
                contact.lifecycle_stage = (
                    contact_info.get("lifecycle_stage") or contact.lifecycle_stage
                )
                if not contact.domain and email:
                    dom = _domain_from_email(email)
                    contact.domain = None if is_personal_domain(dom) else dom
                if contact_info.get("phone"):
                    contact.phone = contact_info["phone"]

            profile = session.get(CustomerProfile, contact.id)
            if profile is None:
                session.add(
                    CustomerProfile(
                        contact_id=contact.id,
                        customer_state="negotiation",
                        pipeline_stage="new",
                        source="hubspot" if contact_info.get("object_id") else "local",
                    )
                )

            # The customer's own subject line = the ticket name. Captured at ingest; it
            # used to be left None here and filled with the AI category later.
            inbound_subject = (contact_info.get("subject") or "").strip() or None

            # Ticket-based inbound: one ticket = one inquiry = one conversation.
            ticket_id = contact_info.get("ticket_id")
            if ticket_id:
                conv = session.query(Conversation).filter_by(hubspot_ticket_id=ticket_id).first()
                if not conv:
                    conv = Conversation(
                        contact_id=contact.id,
                        inquiry_subject=inbound_subject,
                        stage="new",
                        hubspot_ticket_id=ticket_id,
                        inquiry_language=inquiry_lang,
                    )
                    session.add(conv)
                    session.flush()
            else:
                conv = (
                    session.query(Conversation)
                    .filter_by(contact_id=contact.id, hubspot_ticket_id=None)
                    .first()
                )
                if not conv:
                    conv = Conversation(
                        contact_id=contact.id,
                        inquiry_subject=inbound_subject,
                        stage="new",
                        inquiry_language=inquiry_lang,
                    )
                    session.add(conv)
                    session.flush()

            # The thread language is set from the first inbound and kept stable.
            if not conv.inquiry_language and inquiry_lang:
                conv.inquiry_language = inquiry_lang
            # Same repair for the ticket name. It used to be written by the constructor
            # only, so a row created by an event that carried no subject — a ticket fetch
            # that failed, a contact-only webhook — stayed nameless forever even though
            # every later event for that ticket had it. One ticket gets several events.
            if not conv.inquiry_subject and inbound_subject:
                conv.inquiry_subject = inbound_subject

            # **문의가 들어온 시점의 플랜을 이 티켓에 박습니다** (0110, 2026-09-07 운영자
            # 지시). 이 값이 없으면 티켓 상세의 「플랜 정보」와 MQL/PQL 이 `customer_profiles`
            # 를 읽는데, 그 행은 고객이 플랜을 올릴 때마다 바뀝니다 — 몇 달 전 문의를
            # 판단하려고 여는 화면에서 그때 값이 사라집니다.
            #
            # **한 번만 찍습니다.** 티켓 하나에 이벤트가 여러 번 오므로(웹훅 + 10분 폴러 +
            # 티켓 변경) 매번 덮으면 「문의 시점」이 아니라 「마지막 이벤트 시점」이 됩니다.
            # 바로 위에서 연락처를 저장한 뒤라, 이 문의가 들고 온 값이 이미 반영돼 있습니다.
            # **아직 아무것도 모르면 안 찍습니다.** 처음 보는 고객은 이 시점에 프로필이
            # 비어 있고 플랜 값은 그 뒤 연락처 스윕이 채웁니다 — 그때 같은 함수가 다시
            # 찍습니다(`contact_sync.apply_contact_fields`).
            try:
                from ..integrations.hubspot_record import stamp_plan_snapshot

                stamp_plan_snapshot(session, conv)
            except Exception:
                # 못 찍어도 접수는 갑니다 — NULL 은 「그때 값을 모른다」이고, 화면이 그때는
                # 지금 값을 그리며 그렇게 적습니다.
                logger.warning("플랜 스냅샷을 못 남겼습니다 (conv=%s)", conv.id, exc_info=True)

            # First inbound in the thread? (count BEFORE inserting this one.)
            prior_inbound = (
                session.query(Message)
                .filter(Message.conversation_id == conv.id, Message.direction == "inbound")
                .count()
            )
            is_first_inbound = prior_inbound == 0

            # Snapshot the inbound body so the approval UI shows what we're replying to
            # (the subject is kept separate — fixes "가끔 제목만 오더라").
            inbound_body = (contact_info.get("last_message") or "").strip()
            inbound_message_id: int | None = None
            # A durable retry of the same HubSpot ticket may replace a failed
            # draft, but it must not append the customer's inquiry a second time.
            #
            # **이어 쓰는 작업은 문의를 만들지 않습니다** (2026-09-07). 위의 `is_first_inbound`
            # 로는 부족합니다 — 백필로 들여온 티켓 300여 건에는 `messages` 행이 **하나도**
            # 없어서 언제나 「첫 문의」로 보이고, 그러면 「메일 발송」을 누를 때마다 문의 행이
            # 새로 서고 `conv.last_incoming_at` 이 채워집니다. 그 칸은 **워크북 append 대기열의
            # 방아쇠**라(`sheet_sync.sync_pending_inbound_rows`), 그 티켓이 영업팀 공용 시트로
            # 실려 나갑니다. 운영은 시트 쓰기가 켜져 있습니다.
            #
            # 이어 쓰기(`resume_message_id`)에서 잃는 것은 없습니다: 그 초안이 이미 있다는 것은
            # 이 대화가 이미 우리 화면에 있다는 뜻입니다.
            if inbound_body and not resume_message_id and (not ticket_id or is_first_inbound):
                # If this is a later customer message, the latest detailed reply was
                # answered. Auto acknowledgements do not count as sales replies.
                # "test_sent" is the safe-mode counterpart of "sent" (the mail was
                # dispatched, just force-routed to the pre-launch test address). Matching
                # only "sent" left Message.replied permanently False before go-live,
                # which zeroed the reply-rate report and the /messages?status=replied view.
                latest_reply = (
                    session.query(Message)
                    .filter(
                        Message.conversation_id == conv.id,
                        Message.direction == "outgoing",
                        Message.status.in_(("sent", "test_sent")),
                        (Message.prompt_variant.is_(None))
                        | (Message.prompt_variant != "auto_ack"),
                    )
                    .order_by(Message.sent_at.desc(), Message.id.desc())
                    .first()
                )
                if latest_reply:
                    latest_reply.replied = True
                inquiry = Message(
                    conversation_id=conv.id,
                    direction="inbound",
                    channel=channel,
                    from_address=email or None,
                    to_address=None,
                    subject=inbound_subject,
                    body=inbound_body,
                    language=inquiry_lang or "en",
                    status="received",
                )
                session.add(inquiry)
                conv.last_incoming_at = datetime.now(timezone.utc)
                session.flush()
                inbound_message_id = inquiry.id
                # Append-only progress entry: inquiry received.
                excerpt = (inbound_subject or inbound_body).replace("\n", " ").strip()
                add_progress(
                    conv.id,
                    "inbound",
                    f"고객 문의 접수: {excerpt[:140]}",
                    session=session,
                )

            to_addr = email or None
            msg = session.get(Message, resume_message_id) if resume_message_id else None
            resumable = bool(
                msg
                and msg.conversation_id == conv.id
                and msg.direction == "outgoing"
                and msg.prompt_variant != "auto_ack"
                and msg.status in {"drafting", "draft_failed"}
            )
            # 자동 초안은 **한 대화에 하나**입니다. 두 번째 문의부터는 사람이 직접 회신을
            # 등록합니다 — 운영자의 결정입니다.
            #
            # 조건이 "두 번째 inbound" 가 아니라 "이미 회신 줄기가 있다" 인 이유: 티켓 하나에
            # 이벤트가 여러 번 옵니다(웹훅 + 10분 폴러 + 티켓 변경). 고객이 두 번 썼을 때만
            # 막으면, 같은 첫 문의가 다시 들어와 초안이 하나 더 생깁니다. 접수확인은 회신이
            # 아니므로 세지 않습니다.
            #
            # resumable 이 먼저입니다: 죽은 durable job 이 자기 초안을 이어 쓰는 것은 두 번째
            # 초안이 아니라 같은 초안입니다.
            if not resumable:
                prior_reply = (
                    session.query(Message.id)
                    .filter(
                        Message.conversation_id == conv.id,
                        Message.direction == "outgoing",
                        (Message.prompt_variant.is_(None))
                        | (Message.prompt_variant != "auto_ack"),
                    )
                    .first()
                    is not None
                )
                if prior_reply:
                    session.commit()  # 고객 문의와 진행 기록은 남깁니다.
                    return None, conv.id, inbound_message_id
            if not resumable:
                msg = Message(
                    conversation_id=conv.id,
                    direction="outgoing",
                    channel=channel,
                    from_address=None,
                    to_address=to_addr,
                    subject=None,
                    body="",
                    status="drafting",
                    signature_key=_default_signature(),
                    target_language=inquiry_lang,
                    draft_provider=settings.LLM_PROVIDER,
                )
                session.add(msg)
                session.flush()
            else:
                # Resume the exact placeholder linked to this durable job.  A
                # separate ticket-change job has no such link and remains blocked.
                msg.status = "drafting"
                # 「메일 발송」이 연 후속 초안은 서명 없이 섭니다(그 라우트는 서명을 고르지 않습니다) — 첫 회신은
                # 목록의 첫 서명으로 시작하는데 후속 초안만 「서명 없음」으로 승인 화면에 올라왔습니다(평가의 후속
                # 26건 전부). **아직 한 번도 안 쓰인 초안에만** 채웁니다: 본문이 있는 초안을 다시 쓰는 것이면 그
                # 「서명 없음」은 운영자가 고른 것일 수 있습니다.
                if msg.prompt_variant == _MANUAL_VARIANT and not msg.signature_key and not (msg.body or "").strip():
                    msg.signature_key = _default_signature()
                msg.subject = None
                msg.body = ""

            if inbound_job_id:
                job = session.get(InboundJob, inbound_job_id)
                if job and job.status == "processing":
                    payload = dict(job.payload or {})
                    payload["draft_message_id"] = msg.id
                    job.payload = payload
            session.commit()
            return msg.id, conv.id, inbound_message_id
        finally:
            session.close()

    def _finalize_draft(
        self,
        message_id: int,
        contact_info: dict,
        classification: ClassifyResult,
        draft: DraftResult,
        conv_id: int | None = None,
        inquiry_lang: str | None = None,
    ) -> bool:
        """Finalize the draft. Returns whether it is actually waiting for a human.

        False means the ticket moved past New while we were writing (미팅 링크 발송,
        HubSpot 에서의 답변, 콘솔 보드 이동) and the finished draft was closed on the
        spot instead of being put in 발송 대기. That is the one window the stage gates
        cannot cover: ``handle()`` checked the stage minutes ago, at ingest.
        """
        if draft._policy_snapshot is not None:
            draft._policy_snapshot.assert_current()
            since = datetime.fromisoformat(draft._context_manifest["generated_at"])
            if customer_turns_since(thread_events(conv_id), since,
                                    seen=draft._context_manifest["turn_refs"]):
                raise RuntimeError("초안 생성 중 대화가 변경되었습니다. 다시 생성해 주세요.")
        session = SessionLocal()
        try:
            msg = session.get(Message, message_id)
            if not msg:
                return False
            msg.subject = draft.subject
            msg.body = draft.body
            # 이 초안의 한국어 대역. **매번 새로 씁니다** — 「초안 다시 쓰기」로 들어오면
            # 지난 초안의 대역이 남아 있고, 그러면 화면이 새 초안 옆에 다른 초안을
            # 「한국어」라며 붙입니다. 본문이 이미 한국어면 대역은 없습니다.
            msg.body_ko = draft.body_ko or None
            msg.language = draft.language or "ko"
            msg.target_language = inquiry_lang or msg.target_language
            # Every reply waits for a human. The old score-based auto-approval branch
            # and the immediate receipt acknowledgement have both been removed, so no
            # configuration value can make an inbound message send by itself.
            msg.status = "pending_approval"
            if draft._context_manifest:
                session.add(Event(kind="reply_context", payload={
                    **draft._context_manifest, "message_id": message_id,
                }))
            conv = session.get(Conversation, msg.conversation_id)
            if conv:
                # 목록이 보여줄 값. 유형은 스레드의 성질이라 대화에 답니다 — 그리고 이것이
                # "검토 필요" 문구를 대신합니다: CS 문의인지 스팸인지가 "확인이 필요합니다"
                # 보다 무엇을 먼저 열어야 하는지를 정확히 말해 줍니다.
                conv.inquiry_category = classification.category
                # No progress entry: "초안 작성 완료. 검토 대기." is exactly what the
                # pending_approval status already says, on the same screen.

            # 초안을 쓰는 동안 단계가 움직였으면 여기서 바로 종료합니다. flush 가 먼저인
            # 이유: SessionLocal 은 autoflush=False 라, 위에서 준 pending_approval 이
            # DB 에 없으면 아래 조회가 이 행을 못 봅니다.
            session.flush()
            retired = bool(conv and _retire_superseded_drafts(session, conv.id, conv.stage))
            session.commit()
            return not retired
        finally:
            session.close()

    # ----- Customer requests -----

    def _extract_requests(self, conv_id: int | None, contact_info: dict) -> None:
        """고객이 명시적으로 요청한 것만 뽑아 `customer_requests` 에 넣습니다(best-effort).

        **고객이 쓴 글만 읽습니다.** 예전에는 이 자리에서 대화 전체를 읽어 요약 문단까지
        같이 썼는데, 이 호출은 초안이 만들어진 **직후**에 돌고 초안도 메시지 행이라 아무도
        보내지 않은 글이 「우리가 이렇게 답했다」로 요약에 들어갔습니다(2026-08-20 지적).
        요약은 이제 `summaries.append_summary_line` 이 실제로 일어난 일마다 한 줄씩
        덧붙입니다 — 여기서는 요청사항만 봅니다.
        """
        if not conv_id:
            return
        try:
            started = utcnow_naive()
            events = thread_events(conv_id)
            # Use the same customer turns as drafting, including synced corrections.
            newest_first: list[str] = []
            used = 0
            for turn in reversed(events):
                if turn.direction != "inbound":
                    continue
                part = f"고객 주장·미검증 [{turn.at.isoformat()} UTC; {turn.source_ref}]: {turn.body}"
                remaining = 8000 - used
                if remaining <= 0:
                    break
                if len(part) > remaining:
                    if newest_first:
                        break
                    part = part[:max(0, remaining - 15)] + " [원문 일부 생략]"
                newest_first.append(part)
                used += len(part) + 2
            thread_text = "\n\n".join(reversed(newest_first))
            if not thread_text:
                return

            result = self.llm.complete(
                "inbound/extract_requests",
                {
                    "contact_name": contact_info.get("full_name", ""),
                    "company": contact_info.get("company", ""),
                    "thread_text": thread_text,
                },
                schema=_RequestsResult,
                tier="flash",
                max_tokens=1200,
            )
            if customer_turns_since(thread_events(conv_id), started,
                                    seen=[turn.source_ref for turn in events]):
                return  # A stale derived summary must not overwrite a correction.
            with SessionLocal() as session:
                conv = session.get(Conversation, conv_id)
                if conv:
                    conv.customer_requests = (result.customer_requests or "").strip() or None
                    session.commit()
        except Exception:
            logger.warning(
                "Customer-request extraction failed for conv %s (non-fatal).",
                conv_id,
                exc_info=True,
            )

    def _mark_draft_failed(self, message_id: int) -> None:
        """Flip a stuck 'drafting' placeholder to 'draft_failed' so it doesn't spin."""
        try:
            with SessionLocal() as session:
                msg = session.get(Message, message_id)
                if msg and msg.status == "drafting":
                    msg.status = "draft_failed"
                    session.commit()
        except Exception:
            logger.warning("Could not mark draft %s failed", message_id, exc_info=True)


def cache_korean_inquiries(limit: int = 20, conversation_id: int | None = None) -> int:
    """한국어가 아닌 **고객 문의**를 한국어로 옮겨 행에 넣어 둡니다. 처리한 건수를 돌려줍니다.

    번역을 미리 해 두는 것은 이것 하나뿐입니다. 회신 초안은 원래 한국어로 쓰이고, 보낼
    언어로 바꾸는 것은 운영자가 검토 화면에서 `번역하기` 를 누를 때입니다 — 미리 할 이유가
    없고, 운영자가 고친 본문을 번역해야 하므로 미리 할 수도 없습니다.

    **화면을 여는 길에서는 절대 부르지 않습니다.** 예전에는 티켓을 열 때 번역했는데, 그러면
    그 티켓을 처음 여는 사람이 말풍선마다 모델을 기다렸다가 화면을 봤습니다(영어 세 줄이면
    여섯 번). 답을 쓰려고 여는 창에서 할 일이 아닙니다.

    두 곳에서 부릅니다: 접수 직후 그 대화만(``conversation_id``), 그리고 10분 폴러가
    한도만큼(옛 행과 그때 실패한 것을 조금씩 메웁니다). 어느 쪽도 결과를 기다리는 사람이
    없습니다.

    **모델이 안 되면 비워 둡니다.** ``to_korean`` 은 실패해도 예외를 던지지 않고 빈 문자열을
    돌려주므로(translate.py), 그 자리에 원문을 넣으면 영어가 「한국어 번역」이라는 이름을 달고
    행에 굳고 폴러는 그 행을 다시 집지 않습니다 — 되돌릴 방법이 없습니다(검토 화면의
    `번역하기` 는 **회신 초안**을 보낼 언어로 바꾸는 버튼이라 고객 문의에는 안 닿습니다).
    NULL 은 "아직 안 옮겼다" 하나만 뜻하게 두고, 다음 순회에 다시 집히게 합니다. 영원히
    도는 것은 아닙니다: 이미 한국어거나 글자가 없는 본문은 아래 else 로 빠져 값이 채워지고,
    남는 것은 진짜 모델 장애뿐이라 10분에 ``limit`` 건으로 묶여 있습니다.
    """
    from ..llm.translate import needs_korean, to_korean

    with SessionLocal() as session:
        query = session.query(Message).filter(
            Message.direction == "inbound",
            Message.body_ko.is_(None),
        )
        if conversation_id is not None:
            query = query.filter(Message.conversation_id == conversation_id)
        rows = query.order_by(Message.id.desc()).limit(limit).all()

        done = 0
        for row in rows:
            try:
                body = row.body or ""
                row.body_ko = (to_korean(body) or None) if needs_korean(body) else body
                subject = row.subject
                if subject and row.subject_ko is None:
                    row.subject_ko = (
                        (to_korean(subject) or None) if needs_korean(subject) else subject
                    )
                done += 1
            except Exception:
                # 한 건이 막혀도 나머지는 채웁니다. 다음 순회에서 다시 집힙니다.
                logger.warning("문의 번역 실패 (msg=%s)", row.id, exc_info=True)
        if done:
            session.commit()
    return done
