"""Send reviewed inbound replies through HubSpot Conversations."""

from __future__ import annotations

import logging
from email.utils import getaddresses
from functools import partial

from ...common.textwash import text_wash
from ...db.models import Message
from ..delivery import DeliveryPermanentError, SendingDisabled

logger = logging.getLogger(__name__)


class SendLanguageMismatch(RuntimeError):
    """Raised when an approved message has not completed operator-reviewed translation."""


def _canonicalize_reply_links(message: Message, language: str) -> None:
    if getattr(message, "prompt_variant", None) == "auto_ack":
        return
    if not isinstance(message.body, str):
        return
    from ...llm.prompts import canonicalize_contact_links

    message.body = text_wash(canonicalize_contact_links(message.body, language))


def enforce_send_language(message: Message) -> None:
    """Final guard: only an already reviewed target-language body may leave.

    The operator's hard rule is that a reply must go out in the inquiry's language.
    Our code sets ``message.language`` at every step (draft = the language it was
    actually written in, translate button = target), and ``message.target_language``
    holds the language it MUST be sent in. So:

    - every reply body is whitespace/format-normalized (text wash);
    Translation belongs to the explicit review-screen button. If an old API client or
    stale approved row bypasses the approval guard, fail closed instead of translating
    unseen text during delivery.
    """
    if isinstance(message.body, str):
        message.body = text_wash(message.body)

    target = message.target_language if isinstance(message.target_language, str) else ""
    target = target.lower()
    if not target:
        language = getattr(message, "language", "")
        _canonicalize_reply_links(message, language if isinstance(language, str) else "")
        return
    current = message.language if isinstance(message.language, str) else ""
    current = current.lower()

    from ...llm.translate import is_mostly_korean

    if current != target or (target != "ko" and is_mostly_korean(message.body)):
        raise SendLanguageMismatch(
            f"message {message.id} requires reviewed translation "
            f"(current={current or '?'}, target={target})"
        )
    _canonicalize_reply_links(message, target)


def enforce_first_reply_no_price(message: Message) -> None:
    """Final code guard for the "no price in the FIRST reply" rule.

    The draft-time strip can be bypassed (operator types a price into the draft, or
    the translate step re-renders one), so we re-strip prices here — the single send
    chokepoint — when this is the first real reply in the thread. We skip the
    auto-ack. Runs AFTER translation so a translated-in price is caught too.
    """
    if not isinstance(message.target_language, str) or not message.target_language:
        return
    if getattr(message, "prompt_variant", None) == "auto_ack":
        return
    conv_id = getattr(message, "conversation_id", None)
    if not isinstance(conv_id, int):
        return

    from ...db.models import Message as _Message
    from ...db.session import SessionLocal

    try:
        with SessionLocal() as session:
            prior_sent = (
                session.query(_Message)
                .filter(
                    _Message.conversation_id == conv_id,
                    _Message.direction == "outgoing",
                    _Message.status == "sent",
                    _Message.id != message.id,
                    (_Message.prompt_variant.is_(None)) | (_Message.prompt_variant != "auto_ack"),
                )
                .count()
            )
    except Exception:
        logger.warning("First-reply price guard: conv lookup failed; skipping.", exc_info=True)
        return
    if prior_sent:
        return  # not the first reply — later replies may quote KB prices

    from ...common.pricing_guard import strip_price_sentences

    cleaned, removed = strip_price_sentences(message.body)
    if removed:
        message.body = cleaned
        logger.warning(
            "Send guard: stripped %d price line(s) from the FIRST reply (msg %s): %s",
            len(removed),
            message.id,
            " | ".join(removed)[:200],
        )



async def send(message: Message) -> None:
    """Reply on the ticket's existing HubSpot Conversations email thread."""
    from ...common.safe_mode import email_delivery_enabled

    if not email_delivery_enabled():
        raise SendingDisabled(
            "Email delivery is disabled: enable LIVE_EXTERNAL_WRITES and the "
            "code-level EMAIL_SENDING_ENABLED switch."
        )

    # Code-enforced language + text wash, then the first-reply no-price rule.
    if message.direction == "outgoing":
        enforce_send_language(message)
        enforce_first_reply_no_price(message)

    if any(char in (message.subject or "") for char in ("\r", "\n")):
        raise DeliveryPermanentError("Email subject contains illegal CR/LF characters")
    if any(char in (message.to_address or "") for char in ("\r", "\n")):
        raise DeliveryPermanentError("Recipient contains illegal CR/LF characters")
    recipients = [address for _name, address in getaddresses([message.to_address or ""]) if address]
    if len(recipients) != 1 or "@" not in recipients[0]:
        raise DeliveryPermanentError("Exactly one valid recipient email is required")

    try:
        ticket_id = message.conversation.hubspot_ticket_id
    except Exception as exc:
        raise DeliveryPermanentError("The message has no loaded HubSpot ticket") from exc
    if not ticket_id:
        raise DeliveryPermanentError("The message has no HubSpot ticket ID")

    from ..email_html import branded_signature_html, to_html_email
    from ..hubspot import HubSpotClient, cross_inbox_attempt

    signature_html = branded_signature_html(getattr(message, "signature_key", None))
    rich_text = to_html_email(message.body or "", signature_html=signature_html)
    client = HubSpotClient()
    try:
        # 발신 주소는 세 단계로 정해집니다 (이관 0105):
        #   ① 운영자가 이 초안에서 고른 것
        #   ② 아무도 안 골랐으면 설정의 기본 발신 주소
        #   ③ 그것도 없으면 예전처럼 스레드가 정하는 값
        chosen = (getattr(message, "channel_account_id", None) or "").strip()
        if chosen:
            # **고른 것은 안 물러섭니다.** 「이 주소로 보낸다」고 고른 것이라, 다른 주소로
            # 나가면 고른 의미가 없고 나간 뒤에는 못 되돌립니다.
            context = await client.find_conversation_reply_context(
                ticket_id, recipients[0], preferred_account_id=chosen
            )
        else:
            # 안 골랐을 때의 정책(설정의 기본 발신 → 안 되면 스레드)은 한 곳에 있습니다 —
            # **고르개가 화면에 적는 「자동 — …」이 같은 함수를 씁니다.** 예전에는 여기에만
            # 있어서 화면과 실제 발송이 갈렸습니다(2026-09-03).
            context = await client.find_default_reply_context(ticket_id, recipients[0])

        send = partial(
            client.send_conversation_message,
            recipient_email=recipients[0],
            subject=message.subject or "",
            text=message.body or "",
            rich_text=rich_text,
        )
        attempt = None if chosen else cross_inbox_attempt(context)
        if attempt is None:
            hubspot_message_id = await send(context)
        else:
            # **한 번 두드려 보고, 거절하면 원래 주소로 보냅니다** (2026-09-03 운영자 요청).
            #
            # 「같은 인박스여야 한다」는 **허브스팟의 규칙이 아니라 우리가 건 안전장치**입니다
            # (CLAUDE.md). 폼으로 들어온 문의는 대화가 `Inbox` 인박스에만 서는데 기본 발신
            # 주소는 `GTM Marketing` 에 있어서, 그 안전장치 때문에 **한 건도** 그 주소로 못
            # 나갔습니다. 읽기 조회로는 가릴 수 없습니다 — actor 때와 같습니다(조회 200,
            # 발송 400). 그래서 발송이 직접 답하게 합니다.
            #
            # **스레드는 안 바꿉니다.** 계정만 바꿔 같은 자리에 붙입니다 — 엉뚱한 스레드에
            # 답이 붙으면 고객이 보는 대화가 두 갈래가 됩니다.
            #
            # 되돌아올 수 있는 이유: 4xx(429 제외)는 `_lookup_error` 가 영구 실패로 올리는데
            # 그건 **거절이라 아무것도 안 나갔다**는 뜻입니다. 5xx·타임아웃은
            # `DeliveryUnknown` 이라 여기서 안 잡습니다 — 이미 나갔을 수 있어서 다시 보내면
            # 고객이 같은 메일을 두 번 받습니다.
            try:
                hubspot_message_id = await send(attempt)
                context = attempt
                logger.info(
                    "티켓 %s: 다른 인박스의 발신 주소(%s)를 허브스팟이 받아 주었습니다.",
                    ticket_id, attempt.channel_account_id,
                )
            except DeliveryPermanentError as exc:
                logger.warning(
                    "티켓 %s: 허브스팟이 발신 주소 %s 를 거절해 %s 로 보냅니다 — %s",
                    ticket_id, attempt.channel_account_id, context.channel_account_id, exc,
                )
                hubspot_message_id = await send(context)
    finally:
        await client.close()

    message.hubspot_thread_id = context.thread_id
    message.hubspot_message_id = hubspot_message_id
    logger.info(
        "Message %d sent through HubSpot Conversations (thread=%s, message=%s).",
        message.id,
        context.thread_id,
        hubspot_message_id,
    )
