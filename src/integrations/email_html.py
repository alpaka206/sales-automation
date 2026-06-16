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

_URL_RE = re.compile(r"(https?://[^\s<>\"']+)")
# Detect operator-authored HTML. Deliberately excludes single-letter tags (b, i) so
# plain prose like "a < b" isn't misread as markup; use <strong>/<em> for those.
_HTMLISH_RE = re.compile(
    r"<\s*/?\s*(p|div|br|table|tr|td|a|h[1-6]|ul|ol|li|span|img|strong|em|blockquote)\b",
    re.IGNORECASE,
)

_CONTENT_TOKEN = "@@CONTENT@@"

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
    "style=\"padding:28px 32px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',"
    "Roboto,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;font-size:15px;"
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


def text_to_html_fragment(text: str) -> str:
    """Convert a plain-text body to an HTML fragment (paragraphs, <br>, links).

    Passes the text through untouched when it already looks like HTML.
    """
    text = (text or "").strip()
    if not text:
        return "<p></p>"
    if _HTMLISH_RE.search(text):
        return text  # operator-authored HTML — trust it
    paragraphs = re.split(r"\n\s*\n", text)
    out = []
    for para in paragraphs:
        escaped = _html.escape(para).replace("\n", "<br>")
        out.append(f'<p style="margin:0 0 14px;">{_linkify(escaped)}</p>')
    return "\n".join(out)


def to_html_email(text: str) -> str:
    """Return a full HTML document for the given (plain-text or HTML) body."""
    return _SHELL.replace(_CONTENT_TOKEN, text_to_html_fragment(text))
