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

# Table-based shell with inline styles — the layout that survives across email clients.
_SHELL = (
    "<!DOCTYPE html>\n"
    '<html lang="ko"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
    '<body style="margin:0;padding:0;background:#f4f5f7;">'
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
    'style="background:#f4f5f7;"><tr><td align="center" style="padding:24px 12px;">'
    '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
    'style="max-width:600px;width:100%;background:#ffffff;border-radius:10px;'
    'border:1px solid #e6e8eb;"><tr><td '
    "style=\"padding:28px 32px;font-family:'Pretendard Variable',Pretendard;font-size:15px;"
    'line-height:1.7;color:#1f2329;word-break:break-word;">'
    + _CONTENT_TOKEN
    + "</td></tr></table></td></tr></table></body></html>"
)


def _linkify(escaped_text: str) -> str:
    """Wrap bare URLs in anchor tags (operates on already-HTML-escaped text)."""
    return _URL_RE.sub(
        lambda m: f'<a href="{m.group(1)}" style="color:#2563eb;">{m.group(1)}</a>',
        escaped_text,
    )


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
# Branded HTML signatures (operator-selected, attached at send/preview time).
#
# The LLM writes a small plain-text signature into the draft body. When the
# operator picks a *branded* HTML signature on the approval screen, that text
# signature is stripped (so it doesn't appear twice) and the branded card is
# rendered into the HTML email below the reply body. The card lives
# in the editable email_templates store (keys: signature_html_ko / _en), so it
# is never hard-coded here.
# ---------------------------------------------------------------------------

# signature_key values that mean "keep the default plain-text signature".
_TEXT_SIG_KEYS = {None, "", "default", "text"}

# A branded card is wrapped with a thin divider so it reads as a signature block.
_SIGNATURE_WRAP = (
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
    'style="border-collapse:collapse;margin:20px 0 0;"><tr>'
    '<td style="border-top:1px solid #e6e8eb;padding-top:18px;">@@SIG@@</td>'
    "</tr></table>"
)

# Closing lines the LLM appends right above the signature ("감사합니다." etc.).
_CLOSING_RE = re.compile(
    r"\n*[ \t]*(감사합니다\.?|고맙습니다\.?|Best regards,?|Kind regards,?|"
    r"Warm regards,?|Sincerely,?|Thank you\.?|Thanks,?|Regards,?)[ \t]*$",
    re.IGNORECASE,
)
_EMAIL_IN_SIG_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w{2,}")


def strips_text_signature(signature_key: str | None) -> bool:
    """True when the chosen signature replaces the default text signature.

    Branded keys (signature_html_*) and the explicit ``"none"`` both mean "do not
    keep the LLM's plain-text signature"; the empty/``default`` keys keep it.
    """
    return isinstance(signature_key, str) and signature_key not in _TEXT_SIG_KEYS


def branded_signature_html(signature_key: str | None) -> str | None:
    """Return the active branded signature card HTML for a branded key, else None.

    ``"none"`` (no signature) and the text-signature keys return None. A missing
    or inactive template also returns None so the email still goes out (just
    without a card) rather than failing.
    """
    if not isinstance(signature_key, str) or signature_key in _TEXT_SIG_KEYS or signature_key == "none":
        return None
    try:
        from ..db.email_templates import get_email_template

        body = get_email_template(signature_key)
    except Exception:
        body = None
    return (body or "").strip() or None


def _signature_candidates() -> list[str]:
    """Every plain-text signature block we might need to strip, longest first.

    Used when a branded HTML signature replaces the text one: the text block has to come
    off the body or the mail carries two. Longest first so an exact tail match is greedy.

    Sourced from the templates table, INCLUDING paused ones — a draft written last month
    ends with the signature that was current then, and pausing a template must not make
    its text unstrippable. A named person used to be appended here as a literal, which
    only ever stripped that one person's block and went stale the day it changed.
    """
    cands: list[str] = []
    try:
        from ..db.email_templates import get_email_template

        for key in ("signature_ko", "signature_en"):
            body = get_email_template(key)
            if body and body.strip():
                cands.append(body.strip())
    except Exception:
        pass
    cands.append("이혜람\nGrowth, Perso Dubbing | ESTsoft\nleehyeram@estsoft.com")
    seen: set[str] = set()
    out: list[str] = []
    for cand in sorted(cands, key=len, reverse=True):
        if cand and cand not in seen:
            seen.add(cand)
            out.append(cand)
    return out


def strip_known_signature(body: str) -> str:
    """Remove a trailing default text-signature block from ``body``.

    Called when a branded HTML signature replaces it. Best-effort and designed to
    run on the reply body before the HTML signature is appended:

    1. Exact tail match against an active signature template — perfect for the
       common untranslated Korean draft.
    2. Fallback: anchor on the signature's email address, which survives
       translation (proper nouns are preserved), and cut the trailing block.

    Returns ``body`` unchanged if nothing matches, so it can never corrupt a body
    that has no recognizable signature.
    """
    if not body or not body.strip():
        return body
    text = body.rstrip()
    for cand in _signature_candidates():
        if text.endswith(cand):
            head = text[: len(text) - len(cand)]
            return _CLOSING_RE.sub("", head.rstrip()).rstrip()
    # Fallback: anchor on the signature email address (preserved across translation).
    emails = {
        m.group(0).lower() for cand in _signature_candidates() for m in _EMAIL_IN_SIG_RE.finditer(cand)
    }
    low = text.lower()
    cut_at = max((low.rfind(e) for e in emails), default=-1)
    if cut_at == -1:
        return body
    blank = text.rfind("\n\n", 0, cut_at)
    start = blank if blank != -1 else text.rfind("\n", 0, cut_at)
    if start == -1:
        return body
    return _CLOSING_RE.sub("", text[:start].rstrip()).rstrip()


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
        rendered.insert(
            insert_at,
            _SIGNATURE_WRAP.replace("@@SIG@@", sanitize_email_html(signature_html)),
        )

    content = "\n".join(rendered) or "<p></p>"
    return _SHELL.replace(_CONTENT_TOKEN, content)
