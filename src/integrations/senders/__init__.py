"""Send reviewed inbound replies through HubSpot Conversations."""

from __future__ import annotations

import asyncio
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



# 한 회신에 걸 수 있는 참조 수. 자동으로 채워지는 값이 아니라 사람이 고르는 값이라 상한이
# 넉넉해도 되지만, 상한이 아예 없으면 붙여넣기 사고 하나가 고객 메일에 주소 수백 개를
# 노출합니다 — 그리고 나간 뒤에는 못 되돌립니다.
MAX_CC = 10


def parse_cc_addresses(value: str | None, *, exclude: str = "") -> list[str]:
    """참조 주소들. **철자를 다듬는 곳은 여기 한 곳입니다** (이관 0112).

    라우트가 저장할 때와 발송이 payload 를 지을 때가 같은 함수를 지납니다. 둘로 나누면
    화면에 적힌 것과 실제로 나가는 것이 갈리고, 그 어긋남은 메일이 나간 뒤에 알게 됩니다.

    `getaddresses` 로 읽으므로 `이름 <a@b.com>` 도 받고, **쉼표·세미콜론·줄바꿈**을 다
    구분자로 봅니다 — 운영자가 메일 클라이언트나 시트에서 그대로 복사해 붙일 수 있어야
    합니다(아웃룩은 세미콜론, 시트는 줄바꿈으로 줍니다).

    거르는 것 넷:

    - `@` 가 없는 것. 주소가 아닙니다.
    - **CR/LF 가 든 것.** 줄바꿈은 위에서 이미 구분자로 갈라지므로 여기까지 오지
      않습니다. 그래도 남겨 둡니다 — 이 값은 결국 메일 헤더가 되고(우리는 JSON 을 주고
      허브스팟이 헤더를 짓습니다), 나중에 누가 구분자 규칙을 고쳐도 그 사실은 안 바뀝니다.
    - **받는 사람과 같은 주소**(`exclude`). 같은 사람이 To 와 Cc 에 같이 서면 메일이
      두 통 가는 것처럼 보입니다.
    - 중복. 대소문자는 무시하고 **처음 적힌 철자를 남깁니다** — 로컬 파트는 원칙적으로
      대소문자를 가리므로 우리가 눕혀 쓸 값이 아닙니다.
    """
    from email.utils import getaddresses

    skip = {exclude.strip().lower()} if exclude.strip() else set()
    out: list[str] = []
    separated = (value or "").replace(";", ",").replace("\r", ",").replace("\n", ",")
    for _name, address in getaddresses([separated]):
        clean = address.strip()
        key = clean.lower()
        if "@" not in clean or "\r" in clean or "\n" in clean or len(clean) > 254:
            continue
        if key in skip:
            continue
        skip.add(key)
        out.append(clean)
        if len(out) >= MAX_CC:
            break
    return out


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

    # **개인 사서함을 고르면 그 문으로 나갑니다** (2026-09-08 운영자 지시).
    #
    # 「어느 주소로 보낼까」와 「어느 경로로 보낼까」는 같은 질문이라 값 하나로 갈립니다:
    # 숫자면 허브스팟 채널 계정, `gmail:` 이면 그 사서함. 두 형식이 절대 안 겹칩니다.
    chosen_raw = (getattr(message, "channel_account_id", None) or "").strip()
    if chosen_raw.startswith("gmail:"):
        await _send_from_mailbox(message, chosen_raw[6:], recipients[0], rich_text)
        return
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
            # **받는 사람은 그대로입니다** — 참조는 얹기만 합니다(이관 0112). 비어 있으면
            # payload 가 예전과 한 글자도 다르지 않습니다.
            cc=parse_cc_addresses(
                getattr(message, "cc_addresses", None), exclude=recipients[0]
            ),
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


async def _send_from_mailbox(
    message: Message, mailbox: str, recipient: str, rich_text: str
) -> None:
    """개인 사서함에서 한 통. **원본이 있으면 답장, 없으면 새 메일입니다.**

    원본을 찾는 자는 「이 티켓에 개인함으로 들어온 마지막 고객 메일」입니다. 허브스팟으로
    온 문의에 개인 주소를 골라 보내는 경우에는 그런 원본이 없고, 그때는 붙일 스레드도
    `In-Reply-To` 도 없으니 **새 메일**이 유일하게 정직한 결과입니다(운영자 확인).

    **나간 뒤 허브스팟 티켓에 노트로 남깁니다.** 안 그러면 고객이 받은 메일이 티켓에
    없고, 그건 이 앱이 지금까지 피해 온 상태입니다 — 대화가 두 갈래가 됩니다.
    """
    from sqlalchemy import select

    from ...db.models import Contact, CustomerInteraction
    from ...db.session import SessionLocal
    from ..gmail import send_mail, source_message

    conversation = message.conversation
    conversation_id = getattr(conversation, "id", None)
    ticket_id = getattr(conversation, "hubspot_ticket_id", None)

    # **원본의 id 만 들고 나옵니다** — 세션 밖에서 ORM 객체를 만지면 그 속성을 읽는
    # 순간 DetachedInstanceError 이고, 그건 발송 직전에 터집니다.
    origin_id = ""
    if conversation_id is not None:
        with SessionLocal() as session:
            found_id = session.scalar(
                select(CustomerInteraction.external_id)
                .where(
                    CustomerInteraction.conversation_id == conversation_id,
                    CustomerInteraction.external_id.like("gmail:%"),
                    CustomerInteraction.direction == "inbound",
                )
                .order_by(CustomerInteraction.happened_at.desc())
                .limit(1)
            )
            origin_id = (found_id or "")[6:]

    thread_id = in_reply_to = ""
    if origin_id:
        try:
            found = await asyncio.to_thread(source_message, mailbox, origin_id)
            thread_id, in_reply_to = found["thread_id"], found["message_id"]
        except Exception:
            # 원본을 못 읽어도 **보냅니다** — 답장이 새 메일이 될 뿐이고, 못 보내는 것보다
            # 낫습니다. 그리고 이유는 로그에 남습니다.
            logger.warning("원본 메일을 못 읽어 새 메일로 보냅니다 (%s)", mailbox,
                           exc_info=True)

    cc = parse_cc_addresses(getattr(message, "cc_addresses", None), exclude=recipient)
    sent_id = await asyncio.to_thread(
        partial(
            send_mail,
            mailbox,
            to=recipient,
            subject=message.subject or "",
            html=rich_text,
            cc=cc,
            thread_id=thread_id,
            in_reply_to=in_reply_to,
        )
    )
    # 허브스팟 메시지가 아니라 **Gmail 메시지**입니다. `hubspot_message_id` 에 넣으면
    # 티켓 화면이 그 id 로 스레드를 찾다가 못 찾습니다 — SMTP 시절 유물인 이 칸이 뜻이
    # 정확히 맞습니다(우리가 보낸 메일의 provider id).
    message.smtp_message_id = sent_id
    message.in_reply_to = in_reply_to or None
    logger.info(
        "개인 사서함 %s 에서 %s 로 보냈습니다 (%s).",
        mailbox, recipient, "답장" if in_reply_to else "새 메일",
    )

    if ticket_id:
        with SessionLocal() as session:
            contact = session.get(Contact, conversation.contact_id)
            hubspot_contact_id = contact.hubspot_contact_id if contact else None
        if hubspot_contact_id:
            from ...agents.mailbox_sync import _note_on_ticket

            await _note_on_ticket(
                hubspot_contact_id,
                ticket_id,
                f"[개인 메일함 {mailbox} 에서 발송] {message.subject or ''}"
                f"\n\n{message.body or ''}",
                None,
            )
