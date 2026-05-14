"""
Prompt loader.

Reads markdown files under `src/llm/prompts/` and prepends concatenated
`company_rules/*.md` once per process.

Placeholders use Jinja-style `{{ var_name }}`.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
COMPANY_RULES_DIR = REPO_ROOT / "company_rules"

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


@lru_cache
def _company_rules_block() -> str:
    """Concatenate every *.md in company_rules/ in filename order."""
    if not COMPANY_RULES_DIR.exists():
        return ""
    parts: list[str] = []
    for path in sorted(COMPANY_RULES_DIR.glob("*.md")):
        parts.append(f"# from {path.name}\n{path.read_text(encoding='utf-8').strip()}")
    if not parts:
        return ""
    return "## Company rules (must follow)\n\n" + "\n\n".join(parts)


def load_prompt(name: str, variables: dict[str, object] | None = None) -> str:
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

    rules = _company_rules_block()
    if rules:
        return f"{rules}\n\n---\n\n{raw}"
    return raw
