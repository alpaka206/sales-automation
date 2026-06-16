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

# Token inside company_rules/*.md replaced at load time with the web-editable
# email signature (DB-backed). Lets ops edit the outgoing signature from the web
# console without a redeploy. See src/db/email_templates.py + routes/email_templates.py.
_SIGNATURE_TOKEN = "{{__signature__}}"
_DEFAULT_SIGNATURE = "김규원\nPERSO AI | Intern (Developer Relations)\ndevrel.365@gmail.com"


@lru_cache
def _company_rules_raw() -> str:
    """Concatenate every *.md in company_rules/ in filename order (cached)."""
    if not COMPANY_RULES_DIR.exists():
        return ""
    parts: list[str] = []
    for path in sorted(COMPANY_RULES_DIR.glob("*.md")):
        parts.append(f"# from {path.name}\n{path.read_text(encoding='utf-8').strip()}")
    if not parts:
        return ""
    return "## Company rules (must follow)\n\n" + "\n\n".join(parts)


def _current_signature() -> str:
    """Web-editable signature body (DB) with a static fallback.

    Read fresh on every call (cheap single-row query) so an edit in the web
    console takes effect on the next draft without restarting the process.
    """
    try:
        from ..db.email_templates import get_email_template

        body = get_email_template("signature_ko")
    except Exception:
        body = None
    return (body or _DEFAULT_SIGNATURE).strip()


def get_company_rules() -> str:
    """Company rules with the live email signature injected into the placeholder."""
    raw = _company_rules_raw()
    if not raw:
        return ""
    return raw.replace(_SIGNATURE_TOKEN, _current_signature())


# Preserve the previous public API: callers (e.g. llm/knowledge.reset_cache) call
# get_company_rules.cache_clear() to drop cached rules. The signature is read
# fresh every call, so clearing the static-file cache is all that's needed.
get_company_rules.cache_clear = _company_rules_raw.cache_clear  # type: ignore[attr-defined]


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
