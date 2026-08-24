"""
Prompt loader.

Reads the prompt scaffolding from markdown files under `src/llm/prompts/` — that part is
code and belongs in the repo. Everything an operator or a policy owner rewrites lives in
the database instead and is read per call, so an edit lands on the next draft:

- the always-applied rules (tone, CS policy) — `policy_sources` rows, mode='rules',
  written in the console;
- the reply skeleton and the links it ends on — `email_templates` rows.

The signature is NOT here. It used to be injected into the rules at `{{__signature__}}`,
which made the model write somebody's name and address into the body — and then the send
path needed a second machine to take it back off when the operator picked a different
one. The operator picks the signature on the draft and presses 발송; it is attached to the
mail at that point (0061).

Placeholders use Jinja-style `{{ var_name }}`.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
# company_rules/ is gone: the always-applied policy is rows in `policy_sources`, seeded
# from src/db/seeds/policy/ by migration 0043 and edited on 정책 문서 since.

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _rules_from_db() -> str:
    """The always-applied policy, from ``policy_sources`` (mode='rules').

    Read per call, not cached: the whole point of moving these out of the repo is that an
    edit in the console takes effect on the next draft. One indexed query against a
    handful of rows is cheaper than the confusion of a stale cache.

    Nothing here reaches the network — the rows ARE the policy, not a cache of it.
    """
    try:
        from ..db.models import PolicySource
        from ..db.session import SessionLocal

        with SessionLocal() as session:
            rows = (
                session.query(PolicySource)
                .filter(PolicySource.mode == "rules", PolicySource.status == "active")
                .order_by(PolicySource.order_index, PolicySource.id)
                .all()
            )
            parts = [
                f"# {row.label}\n{(row.body or '').strip()}" for row in rows if (row.body or "").strip()
            ]
    except Exception:
        # Logged loudly: with the rules in the database this is the one failure that can
        # silently strip tone and CS policy out of every prompt.
        logger.warning("Company rules could not be read from the database.", exc_info=True)
        return ""
    return "\n\n".join(parts)


def get_company_rules() -> str:
    """The always-applied policy, with the section header the prompts refer to."""
    body = _rules_from_db()
    if not body:
        return ""
    return "## Company rules (must follow)\n\n" + body


# The shape every reply must take — opening, middle, closing — as opposed to what it
# says, which is the model's job. Deliberately a DB row and NOT a file: this is the
# part the operator rewrites most often, and every edit here used to need a deploy.
_REPLY_FORMAT_KEY = "reply_format"


def get_reply_format(language: str | None = None) -> str:
    """The web-editable reply skeleton, or '' when the operator has not set one.

    Read fresh on every draft (one indexed single-row query) so an edit in the console
    applies to the very next reply — the lru_cache on the rules file is exactly the
    behaviour we do not want here.

    **국문과 영문은 다른 서식입니다.** 접미사 없는 행이 국문이고, 영문 문의는 ``_en`` 행을
    먼저 봅니다(``auto_ack`` / ``auto_ack_en`` 과 같은 규칙). 한 벌만 두었더니 국문 문의에
    영문용 문장이 그대로 따라왔습니다 — WhatsApp 안내가 한국어 회신에 붙은 것이 그것입니다.
    영문 행이 없으면 국문 행으로 떨어집니다: 서식이 아예 없는 것보다는 낫습니다.
    """
    try:
        from ..db.email_templates import get_email_template

        english = bool(language) and not language.lower().startswith("ko")
        keys = [f"{_REPLY_FORMAT_KEY}_en", _REPLY_FORMAT_KEY] if english else [_REPLY_FORMAT_KEY]
        for key in keys:
            body = (get_email_template(key) or "").strip()
            if body:
                return body
    except Exception:  # a template outage must never block drafting
        return ""
    return ""


# Tokens the model is told to emit verbatim, swapped for the real values afterwards. The
# booking URL is ~120 characters of opaque base64 — exactly the kind of string a model
# silently truncates or "tidies", and a broken booking link is a lost meeting. The sender
# name is here for a different reason: the KR template introduces the writer by name
# ("이스트소프트 OOO입니다"), and a model asked to fill that in will invent one.
_EDITABLE_TOKENS = {
    "{{MEETING_LINK}}": "meeting_link",
    "{{WHATSAPP}}": "whatsapp_link",
    "{{SENDER_NAME}}": "sender_name",
}

# 이름은 번역할 대상이 아니라 표기가 둘인 것입니다: "배운태" 와 "Untae Bae". 한 칸만 두면
# 둘 중 하나는 반드시 틀리고, 초안이 한국어로 쓰였다가 발송 전에 번역되는 구조라 모델이
# 알아서 로마자로 바꾸게 됩니다 — 매번 다르게. 키에 접미사를 붙여 갈라 둡니다.
# 링크도 언어마다 다릅니다. 주소가 달라서가 아니라 **표기가 달라서**입니다: 국문은
# 「미팅 링크」 라는 글자에 걸고, 영문은 `Calendly` · `WhatsApp` 각각에 겁니다. 행에
# `[미팅 링크](https://…)` 처럼 적어 두면 렌더러가 앵커로 만듭니다 — 맨 URL 을 그대로
# 실으면 120자 base64 예약 주소가 본문 한복판에 그대로 보입니다.
_PER_LANGUAGE_TOKENS = {"{{SENDER_NAME}}", "{{MEETING_LINK}}", "{{WHATSAPP}}"}


def apply_editable_tokens(body: str, language: str | None = None) -> str:
    """Replace the tokens in a drafted body with their web-editable values.

    A token whose row is missing or blank is left untouched rather than replaced with an
    empty string: a visible ``{{MEETING_LINK}}`` in the review screen tells the operator
    the link is unset, where a silent blank would ship as a sentence promising a link
    that is not there. The same holds for ``{{SENDER_NAME}}`` — "이스트소프트 입니다" reads
    as a bug, but it reads as a SENT bug, whereas the token gets noticed before 발송.
    """
    if not body:
        return body
    from ..db.email_templates import get_email_template

    # 고르는 기준은 본문의 언어가 아니라 **고객의 언어**입니다. 초안 본문은 검토용으로 항상
    # 한국어인데, 그 초안이 영어 고객에게 갈 것이면 처음부터 영문 표기가 들어가야 번역
    # 단계가 그것을 건드리지 않습니다.
    english = bool(language) and not language.lower().startswith("ko")
    for token, key in _EDITABLE_TOKENS.items():
        if token not in body:
            continue
        keys = [f"{key}_en", key] if english and token in _PER_LANGUAGE_TOKENS else [key]
        value = ""
        for candidate in keys:
            try:
                value = (get_email_template(candidate) or "").strip()
            except Exception:
                value = ""
            if value:
                break
        if value:
            body = body.replace(token, value)
    return body


_LINK_URL_RE = re.compile(r"https?://[^\s)>\]]+")
_CONTACT_LINK_MARKDOWN_RE = re.compile(
    r"\[(?:Calendly|WhatsApp|미팅\s*링크)\]\([^\n]+?\)", re.IGNORECASE
)


def _url_from_template(value: str | None) -> str:
    found = _LINK_URL_RE.search(value or "")
    return found.group(0).rstrip(".,;:") if found else ""


def canonicalize_contact_links(body: str, language: str | None = None) -> str:
    """Put Calendly and WhatsApp in an exact, deterministic two-line footer.

    The model may decide how the prose reads, but it must not decide where contact
    links sit. Existing prose such as "schedule at Calendly or contact us via
    WhatsApp" is removed as one line, then the configured URLs are appended with
    fixed labels. The function is intentionally a no-op for messages with no contact
    link at all (for example the automatic acknowledgement).
    """
    if not body:
        return body
    from ..db.email_templates import get_email_template

    english = bool(language) and not language.lower().startswith("ko")
    values: dict[str, str] = {}
    for key in ("meeting_link", "meeting_link_en", "whatsapp_link", "whatsapp_link_en"):
        try:
            values[key] = (get_email_template(key) or "").strip()
        except Exception:
            values[key] = ""
    urls = {_url_from_template(value) for value in values.values()}
    urls.discard("")
    has_link = (
        "{{MEETING_LINK}}" in body
        or "{{WHATSAPP}}" in body
        or bool(_CONTACT_LINK_MARKDOWN_RE.search(body))
        or any(url in body for url in urls)
    )
    if not has_link:
        return body

    meeting_keys = ("meeting_link_en", "meeting_link") if english else (
        "meeting_link", "meeting_link_en"
    )
    whatsapp_keys = ("whatsapp_link_en", "whatsapp_link") if english else (
        "whatsapp_link", "whatsapp_link_en"
    )
    meeting_url = next((_url_from_template(values[key]) for key in meeting_keys if values[key]), "")
    whatsapp_url = next(
        (_url_from_template(values[key]) for key in whatsapp_keys if values[key]), ""
    )
    if not meeting_url and not whatsapp_url:
        return body

    markers = ["{{MEETING_LINK}}", "{{WHATSAPP}}", *urls]
    kept = [
        line
        for line in body.splitlines()
        if not any(marker and marker in line for marker in markers)
        and not _CONTACT_LINK_MARKDOWN_RE.search(line)
    ]
    cleaned = "\n".join(kept).strip()
    footer = []
    if meeting_url:
        footer.append(f"[Calendly]({meeting_url})")
    if whatsapp_url:
        footer.append(f"[WhatsApp]({whatsapp_url})")
    footer_text = "\n".join(footer)
    return f"{cleaned}\n\n{footer_text}" if cleaned else footer_text


# Preserve the previous public API: callers (e.g. llm/knowledge.reset_cache) call
# get_company_rules.cache_clear() to drop cached rules. Nothing is cached any more —
# rules and signature are both read per call — so this is a no-op kept for those callers.
get_company_rules.cache_clear = lambda: None  # type: ignore[attr-defined]


def load_prompt(name: str, variables: dict[str, object] | None = None, *, include_rules: bool = True) -> str:
    """
    Load a prompt by dotted/slashed name (e.g. 'inbound/draft_reply' or 'inbound.draft_reply').

    Substitutes {{ key }} placeholders with `variables[key]`. Unknown placeholders are left as-is
    so the model can complain rather than silently dropping context.
    """
    rel = name.replace(".", "/")
    path = PROMPTS_DIR / f"{rel}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt not found: {path}")

    raw = path.read_text(encoding="utf-8")
    if variables:

        def _sub(match: re.Match[str]) -> str:
            key = match.group(1)
            return str(variables[key]) if key in variables else match.group(0)

        raw = _PLACEHOLDER.sub(_sub, raw)

    if not include_rules:
        return raw
    rules = get_company_rules()
    if rules:
        return f"{rules}\n\n---\n\n{raw}"
    return raw
