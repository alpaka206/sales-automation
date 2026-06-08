"""Slack integration for approval cards."""

from __future__ import annotations

import logging

import httpx

from ..common.config import settings

logger = logging.getLogger(__name__)


class SlackNotConfigured(RuntimeError):
    pass


def _approval_url(message_id: int) -> str:
    """Build the operator-facing approval URL for the local web UI."""
    base = settings.PUBLIC_BASE_URL.rstrip("/") if settings.PUBLIC_BASE_URL else f"http://{settings.APP_HOST}:{settings.APP_PORT}"
    return f"{base}/messages/{int(message_id)}"


def _escape_mrkdwn(text: str) -> str:
    """Escape user content to prevent Slack mrkdwn injection."""
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _quote_mrkdwn(text: str, limit: int) -> str:
    """Escape and render text as a Slack blockquote, truncated to ``limit`` chars."""
    text = (text or "").strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + " …"
    safe = _escape_mrkdwn(text)
    # Prefix every line with "> " so multi-line content stays inside the quote.
    return "\n".join(f"> {line}" if line else ">" for line in safe.splitlines()) or "> —"


def post_approval_card(
    message_id: int,
    subject: str,
    body_snippet: str,
    score: int | None,
    category: str,
    channel_type: str,
    *,
    title: str | None = None,
    inquiry: str | None = None,
    contact_name: str | None = None,
    contact_company: str | None = None,
    contact_email: str | None = None,
) -> None:
    """Post a Korean approval card to the configured Slack channel.

    The card answers "who / what did they ask / what will we send" at a glance and
    links straight to the message screen for review:
      - ``title``     : Korean header (e.g. "새 인바운드 문의 — 회신 검토 요청").
      - ``inquiry``   : the customer's inbound message (omitted for outbound cold mail).
      - ``contact_*`` : who the prospect/customer is.
      - ``body_snippet`` : the drafted reply that will go out after approval.

    Note: this card is informational — there are no action buttons because the app
    does not run a Slack Interactivity endpoint with signing-secret verification. The
    operator approves via the web UI link (localhost-trusted) or the /approve API with
    a per-message HMAC token.
    """
    if not settings.SLACK_BOT_TOKEN or not settings.SLACK_APPROVAL_CHANNEL_ID:
        raise SlackNotConfigured("SLACK_BOT_TOKEN or SLACK_APPROVAL_CHANNEL_ID not set.")

    url = _approval_url(message_id)
    header = title or "회신 검토 요청"

    # 문의자 한 줄: 이름 (회사) — 비어 있는 항목은 자연스럽게 생략.
    who_bits = [b for b in (contact_name, contact_company) if b]
    who_line = _escape_mrkdwn(" · ".join(who_bits)) if who_bits else "—"
    email_line = _escape_mrkdwn(contact_email) if contact_email else "—"
    subj_safe = _escape_mrkdwn(subject) if subject else "—"
    cat_safe = _escape_mrkdwn(category)

    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📨 {header}"[:150]}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*문의자:* {who_line}"},
                {"type": "mrkdwn", "text": f"*이메일:* {email_line}"},
                {"type": "mrkdwn", "text": f"*분류:* {cat_safe}"},
                {"type": "mrkdwn", "text": f"*점수:* {score if score is not None else 'N/A'}"},
            ],
        },
    ]

    # 문의 내용 — 인바운드일 때만(아웃바운드 콜드메일은 받은 문의가 없음).
    if inquiry and inquiry.strip():
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*🗨️ 문의 내용*\n{_quote_mrkdwn(inquiry, 800)}"},
        })

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"*✍️ 나갈 답변 초안* (제목: {subj_safe})"},
    })
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"```{_escape_mrkdwn(body_snippet[:1500])}```"},
    })
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"<{url}|🔗 메시지 화면에서 검토·승인하기 →>"},
    })

    with httpx.Client(timeout=10) as client:
        r = client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {settings.SLACK_BOT_TOKEN}"},
            json={
                "channel": settings.SLACK_APPROVAL_CHANNEL_ID,
                "blocks": blocks,
                # Fallback text for notifications/screen readers.
                "text": f"{header} (메시지 #{message_id}) — {url}",
            },
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            logger.error("Slack API error: %s", data.get("error"))

    logger.info("Posted approval card for message %d to Slack.", message_id)


def post_message(channel: str, text: str) -> None:
    """Post a plain mrkdwn text message to a Slack channel."""
    if not settings.SLACK_BOT_TOKEN:
        raise SlackNotConfigured("SLACK_BOT_TOKEN not set.")

    with httpx.Client(timeout=10) as client:
        r = client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {settings.SLACK_BOT_TOKEN}"},
            json={"channel": channel, "text": text},
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            logger.error("Slack API error: %s", data.get("error"))

    logger.info("Posted message to Slack channel %s.", channel)
