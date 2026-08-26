"""Render an outgoing email body as a styled HTML email.

Drafts are authored/stored as plain text (reliable for the LLM + approval UI).
At send time we ALSO attach an HTML alternative so the email renders like a normal
formatted email (readable width, clickable links) instead of raw monospace text.

If the body already contains HTML markup (an operator hand-wrote HTML), it is passed
through into the shell as-is rather than escaped.
"""

from __future__ import annotations

import html as _html
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

_URL_RE = re.compile(r"(https?://[^\s<>\"']+)")
# Detect operator-authored HTML. Deliberately excludes single-letter tags (b, i) so
# plain prose like "a < b" isn't misread as markup; use <strong>/<em> for those.
_HTMLISH_RE = re.compile(
    r"<\s*/?\s*(p|div|br|table|tr|td|a|h[1-6]|ul|ol|li|span|img|strong|em|blockquote)\b",
    re.IGNORECASE,
)

_CONTENT_TOKEN = "@@CONTENT@@"

_ALLOWED_TAGS = {
    "a", "blockquote", "br", "div", "em", "h1", "h2", "h3", "h4", "h5", "h6",
    "hr", "img", "li", "ol", "p", "span", "strong", "table", "tbody", "td", "tfoot",
    "th", "thead", "tr", "ul",
}
_VOID_TAGS = {"br", "hr", "img"}
_DROP_CONTENT_TAGS = {
    "base", "button", "embed", "form", "head", "iframe", "input", "link", "math", "meta",
    "object", "option", "script", "select", "style", "svg", "textarea",
}
_GLOBAL_ATTRS = {"class", "id", "role", "style", "title", "aria-label"}
_TAG_ATTRS = {
    "a": {"href", "rel", "target"},
    "img": {"alt", "height", "src", "width"},
    "table": {"align", "border", "cellpadding", "cellspacing", "width"},
    "td": {"align", "colspan", "height", "rowspan", "valign", "width"},
    "th": {"align", "colspan", "height", "rowspan", "valign", "width"},
}
_UNSAFE_CSS_RE = re.compile(r"(?:expression\s*\(|url\s*\(|@import|-moz-binding|javascript:)", re.I)


def _safe_url(value: str, *, image: bool = False) -> bool:
    value = value.strip()
    if not value or "\r" in value or "\n" in value:
        return False
    if value.startswith("#") and not image:
        return True
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    scheme = parsed.scheme.lower()
    allowed = {"http", "https", "cid"} if image else {"http", "https", "mailto", "tel"}
    if scheme not in allowed or parsed.username is not None or parsed.password is not None:
        return False
    return bool(parsed.hostname) if scheme in {"http", "https"} else bool(parsed.path)


class _EmailHTMLSanitizer(HTMLParser):
    """Allowlist sanitizer for customer-facing HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.output: list[str] = []
        self.open_tags: list[str] = []
        self.drop_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self.drop_depth:
            if tag in _DROP_CONTENT_TAGS:
                self.drop_depth += 1
            return
        if tag in _DROP_CONTENT_TAGS:
            self.drop_depth = 1
            return
        if tag not in _ALLOWED_TAGS:
            return

        clean: list[str] = []
        allowed_attrs = _GLOBAL_ATTRS | _TAG_ATTRS.get(tag, set())
        for raw_name, raw_value in attrs:
            name = raw_name.lower()
            value = raw_value or ""
            if name not in allowed_attrs or name.startswith("on"):
                continue
            if name == "style" and _UNSAFE_CSS_RE.search(value):
                continue
            if name == "href" and not _safe_url(value):
                continue
            if name == "src" and not _safe_url(value, image=True):
                continue
            clean.append(f' {name}="{_html.escape(value, quote=True)}"')
        self.output.append(f"<{tag}{''.join(clean)}>")
        if tag not in _VOID_TAGS:
            self.open_tags.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.drop_depth:
            if tag in _DROP_CONTENT_TAGS:
                self.drop_depth -= 1
            return
        if tag not in self.open_tags:
            return
        while self.open_tags:
            opened = self.open_tags.pop()
            self.output.append(f"</{opened}>")
            if opened == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self.drop_depth:
            self.output.append(_html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        if not self.drop_depth:
            self.output.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self.drop_depth:
            self.output.append(f"&#{name};")

    def result(self) -> str:
        while self.open_tags:
            self.output.append(f"</{self.open_tags.pop()}>")
        return "".join(self.output)


def sanitize_email_html(fragment: str) -> str:
    parser = _EmailHTMLSanitizer()
    parser.feed(fragment or "")
    parser.close()
    return parser.result()

# **틀을 씌우지 않습니다.** 예전에는 회색 배경 위에 흰 카드(600px · 둥근 모서리 · 테두리)를
# 얹었는데, 그러면 사람이 쓴 메일이 아니라 **발송 시스템이 만든 알림처럼** 보입니다 —
# 운영자가 실제로 받아 보고 "미리보기처럼 나갔다" 고 했습니다(2026-08-26). 영업 회신은
# 담당자가 직접 쓴 메일로 보여야 하고, HubSpot 에서 사람이 「Create an email」로 보내는
# 메일에도 그런 카드가 없습니다.
#
# **그래도 HTML 자체는 남습니다.** 서명이 운영자가 콘솔에 쓴 HTML 이고(로고·표), 링크도
# 앵커여야 120자짜리 예약 URL 이 본문에 그대로 실리지 않습니다. 즉 없애는 것은 **꾸밈**이지
# 마크업이 아닙니다 — 글꼴·색·너비를 지정하지 않고 문단만 넘겨 수신자의 메일 클라이언트가
# 제 기본값으로 그리게 둡니다. 그것이 「텍스트로 온 메일」의 모양입니다.
_SHELL = (
    "<!DOCTYPE html>\n"
    '<html lang="ko"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
    "<body>" + _CONTENT_TOKEN + "</body></html>"
)


# 본문에서 쓸 수 있는 표기는 이 넷뿐입니다. 마크다운 전체가 아니라 **부분집합**인 것이
# 요점입니다 — 검토 화면은 그냥 textarea 라, 운영자가 화면에서 보는 글자와 고객이 받는 메일이
# 최대한 같아야 합니다. 표기가 늘수록 그 둘이 벌어집니다.
#
# 링크에 라벨이 필요해서 시작했습니다. 예약 URL 은 120자짜리 base64 라, 맨 URL 을 그대로
# 앵커로 만들면 메일 본문 한복판에 그 덩어리가 그대로 실립니다.
_MD_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
# 겹친 것(`***`)을 **먼저** 잡습니다. 안 그러면 굵게 규칙이 별 세 개 중 둘만 먹고 남은
# 하나를 기울임이 가져가면서 `<strong><em>x</strong></em>` 처럼 태그가 어긋납니다.
_BOLD_ITALIC_RE = re.compile(r"\*\*\*(?=\S)([^*<>]+?)(?<=\S)\*\*\*")
_BOLD_RE = re.compile(r"\*\*(?=\S)([^*]+?)(?<=\S)\*\*")
_UNDERLINE_RE = re.compile(r"__(?=\S)([^_]+?)(?<=\S)__")
# 안쪽에 `<`·`>` 를 두지 않는 이유: 앞 단계가 만든 태그를 가로질러 열고 닫으면 어긋납니다.
_ITALIC_RE = re.compile(r"(?<!\*)\*(?=\S)([^*<>\n]+?)(?<=\S)\*(?!\*)")
_HOLD = "\x00%d\x00"


def _inline(escaped_text: str) -> str:
    """링크·굵게·기울임·밑줄. **이미 HTML escape 된 글자**에 대고 씁니다.

    순서가 중요합니다. 마크다운 링크를 먼저 꺼내 자리표시자로 빼 두지 않으면, 그 뒤의 맨
    URL 처리가 방금 만든 `<a href="...">` 안의 주소를 다시 링크로 감싸 태그가 겹칩니다.
    """
    held: list[str] = []

    def _hold(html: str) -> str:
        held.append(html)
        return _HOLD % (len(held) - 1)

    def _link(match: re.Match) -> str:
        label, url = match.group(1), match.group(2)
        # href 는 sanitizer 와 같은 잣대로 봅니다 — 표기 하나 늘렸다고 javascript: 가
        # 들어올 구멍을 만들 수는 없습니다.
        if not _safe_url(_html.unescape(url)):
            return match.group(0)
        return _hold(f'<a href="{url}" style="color:#2563eb;">{label}</a>')

    text = _MD_LINK_RE.sub(_link, escaped_text)
    text = _URL_RE.sub(
        lambda m: _hold(f'<a href="{m.group(1)}" style="color:#2563eb;">{m.group(1)}</a>'),
        text,
    )
    text = _BOLD_ITALIC_RE.sub(lambda m: f"<strong><em>{m.group(1)}</em></strong>", text)
    text = _BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", text)
    text = _UNDERLINE_RE.sub(lambda m: f"<u>{m.group(1)}</u>", text)
    text = _ITALIC_RE.sub(lambda m: f"<em>{m.group(1)}</em>", text)
    for index, html in enumerate(held):
        text = text.replace(_HOLD % index, html)
    return text


def _linkify(escaped_text: str) -> str:
    """예전 이름. 부르는 곳이 남아 있어 남겨 둡니다 — 하는 일은 `_inline` 입니다."""
    return _inline(escaped_text)


_BULLET_RE = re.compile(r"^\s*-\s+(.*)$")


def _render_paragraph(para: str) -> str:
    """Render one blank-line-delimited paragraph, turning ``- `` lines into a list.

    Consecutive ``- item`` lines become an indented <ul>; runs of normal lines
    become a <p> with <br> between them. A paragraph can mix the two (e.g. a lead
    line followed by bullet rows), which is exactly the layout we want for the
    "기능 나열은 - 불릿, 들여쓰기" rule.
    """
    blocks: list[str] = []
    text_buf: list[str] = []
    bullet_buf: list[str] = []

    def flush_text() -> None:
        if text_buf:
            joined = "<br>".join(_linkify(_html.escape(line)) for line in text_buf)
            blocks.append(f'<p style="margin:0 0 14px;">{joined}</p>')
            text_buf.clear()

    def flush_bullets() -> None:
        if bullet_buf:
            items = "".join(
                f'<li style="margin:0 0 4px;">{_linkify(_html.escape(b))}</li>' for b in bullet_buf
            )
            blocks.append(f'<ul style="margin:0 0 14px;padding-left:22px;">{items}</ul>')
            bullet_buf.clear()

    for line in para.split("\n"):
        m = _BULLET_RE.match(line)
        if m:
            flush_text()
            bullet_buf.append(m.group(1))
        else:
            flush_bullets()
            text_buf.append(line)
    flush_text()
    flush_bullets()
    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# Signatures (operator-selected, attached at send/preview time).
#
# The operator picks a signature on the review screen and presses 발송; the chosen
# body is rendered into the HTML email below the reply. Nothing writes a signature
# into the draft body any more (0061) — that used to happen in the prompt, which is
# why there was a second machine here to take it back off when the operator picked
# a different one. The card lives in the editable email_templates store, so it is
# never hard-coded here.
# ---------------------------------------------------------------------------

# A signature is wrapped with a thin divider so it reads as a signature block.
_SIGNATURE_WRAP = (
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
    'style="border-collapse:collapse;margin:20px 0 0;"><tr>'
    '<td style="border-top:1px solid #e6e8eb;padding-top:18px;">@@SIG@@</td>'
    "</tr></table>"
)


def branded_signature_html(signature_key: str | None) -> str | None:
    """The chosen signature's body, or None for 서명 없음.

    A missing or inactive template also returns None so the email still goes out (just
    unsigned) rather than failing — which also covers the stored ``"none"`` that older
    drafts carry, since no template answers to that key.
    """
    if not isinstance(signature_key, str) or not signature_key:
        return None
    try:
        from ..db.email_templates import get_email_template

        body = get_email_template(signature_key)
    except Exception:
        body = None
    return (body or "").strip() or None


def _content_fragments(text: str) -> tuple[list[str], list[str]]:
    """Return (rendered HTML blocks, raw paragraphs) in parallel order."""
    if _HTMLISH_RE.search(text):
        return [sanitize_email_html(text)], [text]
    paras = re.split(r"\n\s*\n", text)
    return [_render_paragraph(p) for p in paras], paras


def to_html_email(text: str, signature_html: str | None = None) -> str:
    """Return a full HTML document for the given (plain-text or HTML) body.

    When ``signature_html`` is given, the branded signature is appended at the end.
    With no signature the output is unchanged.
    """
    text = (text or "").strip()
    if not text and not signature_html:
        return _SHELL.replace(_CONTENT_TOKEN, "<p></p>")

    rendered, paras = _content_fragments(text) if text else ([], [])
    if signature_html:
        insert_at = len(rendered)
        for i in range(len(paras) - 1, -1, -1):
            if paras[i].lstrip().startswith("---"):
                insert_at = i
                break
        # Same treatment as the body: HTML passes through the sanitizer, plain text gets
        # its line breaks. A signature typed as three lines in 새로 만들기 used to arrive
        # as one run-on line, and nothing on the screen said why.
        rendered.insert(
            insert_at,
            _SIGNATURE_WRAP.replace("@@SIG@@", "\n".join(_content_fragments(signature_html)[0])),
        )

    content = "\n".join(rendered) or "<p></p>"
    return _SHELL.replace(_CONTENT_TOKEN, content)
