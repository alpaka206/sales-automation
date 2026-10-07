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


def _rules_from_db(stage: str | None = None) -> str:
    """The rules for THIS reply, from ``policy_sources`` (mode='rules').

    **`scope` 가 여기서 듣습니다** (2026-09-10). 이 필터가 없던 동안 화면은 「첫 회신에만」
    과 「그 이후 회신에」를 고르게 해 놓고 **두 회신에 다 넣고 있었습니다** — 고른 것이
    아무 일도 안 하는데 화면에는 저장됐다고 보입니다. 이 저장소가 가장 싫어하는 종류의
    상태이고, 화면을 로더보다 먼저 내놓아서 생긴 문제입니다.

    ``stage`` 는 ``knowledge.router_docs`` 와 **같은 표**를 씁니다 — 「모두」에 그 단계의
    문서를 더합니다. 표가 둘이면 규칙 문서와 참고 문서가 서로 다른 단계에 붙습니다.

    - ``None`` — 유틸리티(분류·번역·요약·언어 판별). **「모두」만** 봅니다.
      부르는 쪽에서 아예 안 부르는 것이 기본이고(``client.complete``), 이 값은 옛
      호출자를 위한 자리입니다.
    - ``'first'`` / ``'followup'`` — 그 회신의 규칙.
    - 그 밖의 문자열 — **입력 오류입니다.** 조용히 「모두」로 넓히지 않습니다.
      넓히는 쪽으로 틀리면 사람용 문서가 새고, 그건 화면 어디에도 안 보입니다.

    Read per call, not cached: the whole point of moving these out of the repo is that an
    edit in the console takes effect on the next draft. One indexed query against a
    handful of rows is cheaper than the confusion of a stale cache.

    Nothing here reaches the network — the rows ARE the policy, not a cache of it.
    """
    from .policy_context import read_sources

    # A failed lookup is not an empty policy set. Let the draft worker record failure.
    return "\n\n".join(
        f"# {row.label}\n{(row.body or '').strip()}"
        for row in read_sources(stage, mode="rules") if (row.body or "").strip()
    )


def get_company_rules(stage: str | None = None) -> str:
    """The rules for this reply, with the section header the prompts refer to."""
    body = _rules_from_db(stage)
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
    먼저 봅니다(``reply_format`` / ``reply_format_en`` 규칙). 한 벌만 두었더니 국문 문의에
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
# 연락 링크에 다는 글자(공백을 빼고 소문자로). 이 글자를 단 링크만 언어에 맞춰 글자를 바꿉니다 —
# 운영자가 단 다른 글자(「book a call」)는 문장이라 안 건드립니다.
_CONTACT_LABELS = {"calendly": "meeting", "미팅링크": "meeting", "whatsapp": "whatsapp"}
_LINK_TOKENS = {"{{MEETING_LINK}}": "meeting", "{{WHATSAPP}}": "whatsapp"}
_MARKDOWN_LINK_RE = re.compile(r"\[([^\[\]\n]+)\]\(([^()\s]+)\)")
# `[미팅 링크]({{MEETING_LINK}})` 의 토큰 자리에 행 전체(`[Calendly](주소)`)가 들어가면 생기는 겹친 링크.
_NESTED_LINK_RE = re.compile(r"\[([^\[\]\n]+)\]\(\s*\[[^\[\]\n]*\]\(([^()\s]+)\)\s*\)")
_HELD_RE = re.compile("\x00(\\d+)\x00")


def _url_from_template(value: str | None) -> str:
    found = _LINK_URL_RE.search(value or "")
    return found.group(0).rstrip(".,;:") if found else ""


def canonicalize_contact_links(body: str, language: str | None = None) -> str:
    """Normalize the contact links the body **already uses** — and nothing else.

    The model (or the operator) decides how the prose reads, WHERE a link goes and
    WHETHER there is one; this decides only what a contact link points at and, for the
    contact labels, what it is called:

    - ``{{MEETING_LINK}}`` / ``{{WHATSAPP}}`` become ``[label](configured URL)`` in place.
      With no URL configured the token stays visible — and approval refuses it
      (``reply_flags.unfilled_slots``), so it never ships as a raw token.
    - ``[Calendly](…)`` · ``[미팅 링크](…)`` · ``[WhatsApp](…)`` get the configured URL (a
      model shortens a 120-char booking URL) and **the label follows the language**: a
      Korean reply links the words 「미팅 링크」, any other language ``Calendly``.
    - A link with the operator's own label keeps its words; only a configured URL is
      swapped for the language's row.
    - A bare configured URL in a sentence becomes a labeled link **inside that sentence**.
    - ``[미팅 링크]([Calendly](URL))`` — the Korean skeleton's ``[미팅 링크]({{MEETING_LINK}})``
      after token substitution put the whole row inside the parentheses — becomes one link.

    **It never adds a link and never removes a line** (2026-10-06). It used to delete every
    line holding a contact link and write a fixed block in its place, which took the
    operator's sentence with it ("If it's easier, grab a slot here: … or just reply to this
    email." went out as two bare link lines), and it appended WhatsApp to every non-Korean
    reply whether or not anyone wrote it (11 of 11 English drafts in the 2026-10-06
    evaluation). Whether a WhatsApp line belongs is the template's decision: the English
    skeleton says 「다른 링크 토큰을 더하지 마세요」.

    It is not on the send path any more: drafts and operator saves pass through it
    (``approval.prepare_reviewed_body``), so what the operator approves is what goes out.
    No contact link, or no configured URL → the body comes back unchanged.
    """
    # 토큰도 링크도 주소도 없는 글은 행을 읽을 것도 없습니다 — 저장·승인이 부르는 자리라 DB 왕복을 아낍니다.
    if not body or not any(mark in body for mark in ("{{", "](", "http")):
        return body
    from ..db.email_templates import get_email_template

    english = bool(language) and not language.lower().startswith("ko")
    values: dict[str, str] = {}
    for key in ("meeting_link", "meeting_link_en", "whatsapp_link", "whatsapp_link_en"):
        try:
            values[key] = _url_from_template(get_email_template(key))
        except Exception:
            values[key] = ""
    order = ("_en", "") if english else ("", "_en")
    url = {
        kind: next((values[f"{kind}_link{suffix}"] for suffix in order if values[f"{kind}_link{suffix}"]), "")
        for kind in ("meeting", "whatsapp")
    }
    if not url["meeting"] and not url["whatsapp"]:
        return body
    known = {value: key.split("_")[0] for key, value in values.items() if value}  # 설정된 주소 → 종류
    label = {"meeting": "Calendly" if english else "미팅 링크", "whatsapp": "WhatsApp"}

    held: list[str] = []

    def _hold(text: str) -> str:
        held.append(text)
        return f"\x00{len(held) - 1}\x00"

    def _markdown(match: re.Match[str]) -> str:
        text, target = match.group(1), match.group(2)
        contact_label = _CONTACT_LABELS.get(re.sub(r"\s+", "", text).lower())
        kind = _LINK_TOKENS.get(target) or known.get(target) or contact_label
        if kind is None:
            return _hold(match.group(0))
        new_url = url[kind] or ("" if target in _LINK_TOKENS else target)
        if not new_url:  # 토큰인데 주소가 없다 — 보이게 둡니다.
            return _hold(match.group(0))
        return _hold(f"[{label[kind] if contact_label else text}]({new_url})")

    out = _NESTED_LINK_RE.sub(lambda m: f"[{m.group(1)}]({m.group(2)})", body)
    out = _MARKDOWN_LINK_RE.sub(_markdown, out)
    for token, kind in _LINK_TOKENS.items():
        if url[kind]:
            out = out.replace(token, _hold(f"[{label[kind]}]({url[kind]})"))
    # 문장 속 맨 주소. 뒤에 주소 글자가 더 이어지면 다른 주소라 안 건드립니다.
    for configured, kind in sorted(known.items(), key=lambda item: -len(item[0])):
        out = re.sub(
            re.escape(configured) + r"(?=[.,;:!?)\]]*(?:\s|$))",
            lambda _m, kind=kind: _hold(f"[{label[kind]}]({url[kind]})"),
            out,
        )
    return _HELD_RE.sub(lambda m: held[int(m.group(1))], out)


# Preserve the previous public API: callers (e.g. llm/knowledge.reset_cache) call
# get_company_rules.cache_clear() to drop cached rules. Nothing is cached any more —
# rules and signature are both read per call — so this is a no-op kept for those callers.
get_company_rules.cache_clear = lambda: None  # type: ignore[attr-defined]


def load_prompt(
    name: str, variables: dict[str, object] | None = None, *, include_rules: bool = False
) -> str:
    """
    Load a prompt by dotted/slashed name (e.g. 'inbound/draft_reply' or 'inbound.draft_reply').

    Substitutes {{ key }} placeholders with `variables[key]`. Unknown placeholders are left as-is
    so the model can complain rather than silently dropping context.

    **``include_rules`` 의 기본값이 False 입니다** (2026-09-10). 부르는 곳은
    ``client.complete`` 하나이고 거기는 규칙을 ``system`` 으로 따로 싣습니다 — 기본값이
    True 이면 **규칙이 실리는 문이 둘**이 되고, 그중 하나는 단계를 모릅니다. 회신 규칙은
    ``complete(stage=...)`` 한 문으로만 들어갑니다.
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
