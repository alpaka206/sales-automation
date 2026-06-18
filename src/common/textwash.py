"""Deterministic text cleanup ("text washing") for outgoing messages.

Rules the operator defined are enforced in CODE, not left to the LLM. This module
is the final whitespace/format normalizer applied to a reply body right before it
is shown for translation and again right before it is sent, so what goes out is
always clean regardless of what the model produced.

What it does (all deterministic, no LLM):
- normalizes line endings to ``\n`` and strips a UTF-8 BOM / zero-width chars
- trims trailing whitespace on every line
- collapses 3+ consecutive blank lines down to a single blank line
- strips leading/trailing blank lines
- normalizes bullet markers (•, ·, *, –, — at line start) to "- " so the HTML
  renderer can turn them into an indented list
- collapses runs of 2+ inner spaces to one (preserving a line's leading indent)

It deliberately does NOT touch sentence content, punctuation, URLs, numbers, or
language — only layout/whitespace — so it can never corrupt a translated reply.
"""

from __future__ import annotations

import re

# Zero-width / BOM characters that sneak in from copy-paste or LLM output and
# render as invisible junk in email clients.
_ZERO_WIDTH = re.compile("[﻿​‌‍⁠]")
# Leading bullet glyphs we normalize to a plain "- " marker.
_BULLET_LINE = re.compile(r"^(\s*)([•·*▪◦‣–—])\s+")
# 2+ spaces that are NOT at the start of the line (leading indent is preserved).
_INNER_SPACES = re.compile(r"(?<=\S) {2,}")
# 3+ newlines (with optional surrounding spaces) → exactly one blank line.
_EXTRA_BLANKS = re.compile(r"\n[ \t]*\n[ \t]*(\n[ \t]*)+")


def text_wash(text: str | None) -> str:
    """Return a whitespace/format-normalized copy of ``text`` ("" for blank)."""
    if not text:
        return ""
    # Normalize line endings and drop invisible characters.
    out = text.replace("\r\n", "\n").replace("\r", "\n")
    out = _ZERO_WIDTH.sub("", out)

    lines: list[str] = []
    for line in out.split("\n"):
        line = line.rstrip()
        line = _BULLET_LINE.sub(lambda m: f"{m.group(1)}- ", line)
        line = _INNER_SPACES.sub(" ", line)
        lines.append(line)
    out = "\n".join(lines)

    # Collapse 3+ newlines to a single blank line, then trim surrounding blanks.
    out = _EXTRA_BLANKS.sub("\n\n", out)
    return out.strip()
