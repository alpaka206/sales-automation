"""Deterministic text cleanup ("text washing") for outgoing messages.

Rules the operator defined are enforced in CODE, not left to the LLM. This module is
the whitespace/format normalizer a reply body passes through **before** a person sees
it — when the draft is written and when the operator saves, translates or approves an
edit (`approval.prepare_reviewed_body`). **발송은 이것을 다시 부르지 않습니다**
(2026-10-06): 사람이 승인한 글자가 그대로 나가야 하고, 발송 직전에 다듬으면 그 글자가
승인한 것과 달라집니다.

What it does (all deterministic, no LLM):
- normalizes line endings to ``\n`` and strips a UTF-8 BOM / zero-width chars
- trims trailing whitespace on every line
- collapses 3+ consecutive blank lines down to a single blank line
- strips leading/trailing blank lines
- normalizes bullet markers (•, ·, *, ▪, ◦, ‣ at line start) to "- " so the HTML
  renderer can turn them into an indented list. **En/em dashes are not bullets** —
  「— Untae」 같은 맺음 줄이 목록 한 칸이 되어 나갔습니다.
- keeps one blank line before and after a bullet block so both plain-text and
  HTML alternatives remain easy to scan. **An indented line right after a bullet is
  that bullet's continuation** (a hard-wrapped item), not prose: no blank goes in
  front of it or in front of the next bullet. 예전에는 그 사이에 빈 줄을 넣어서 줄을
  맞춰 쓴 항목 하나가 문단 셋으로 쪼개져 나갔습니다(MSG#110).
- collapses runs of 2+ inner spaces to one (preserving a line's leading indent)

It deliberately does NOT touch sentence content, punctuation, URLs, numbers, or
language — only layout/whitespace — so it can never corrupt a translated reply.
"""

from __future__ import annotations

import re

# Zero-width / BOM characters that sneak in from copy-paste or LLM output and
# render as invisible junk in email clients.
_ZERO_WIDTH = re.compile("[﻿​‌‍⁠]")
# Leading bullet glyphs we normalize to a plain "- " marker. –/— are not here: a dash
# opens a sign-off or an aside as often as a list item.
_BULLET_LINE = re.compile(r"^(\s*)([•·*▪◦‣])\s+")
_NORMALIZED_BULLET_LINE = re.compile(r"^\s*-\s+\S")
# A hard-wrapped bullet's next line: indented, not blank.
_CONTINUATION_LINE = re.compile(r"^[ \t]+\S")
# 2+ spaces that are NOT at the start of the line (leading indent is preserved).
_INNER_SPACES = re.compile(r"(?<=\S) {2,}")
# 3+ newlines → exactly one blank line. Lines are already right-stripped, so blank
# lines are empty; matching only newlines keeps the NEXT line's indent.
_EXTRA_BLANKS = re.compile(r"\n{3,}")


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
    # Separate bullet blocks from surrounding prose. Do not add extra blanks
    # inside a consecutive list (an indented continuation is still the list) or
    # when the author already supplied one.
    spaced: list[str] = []
    in_list = False  # the previous line is a bullet or a bullet's continuation
    for line in lines:
        if _NORMALIZED_BULLET_LINE.match(line):
            if spaced and spaced[-1] and not in_list:
                spaced.append("")
            in_list = True
        elif in_list and _CONTINUATION_LINE.match(line):
            pass
        elif line:
            if in_list:
                spaced.append("")
            in_list = False
        else:
            in_list = False
        spaced.append(line)
    out = "\n".join(spaced)

    # Collapse 3+ newlines to a single blank line, then trim surrounding blanks.
    out = _EXTRA_BLANKS.sub("\n\n", out)
    return out.strip()
