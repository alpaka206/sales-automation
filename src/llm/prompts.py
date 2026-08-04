"""
Prompt loader.

Reads the prompt scaffolding from markdown files under `src/llm/prompts/` — that part is
code and belongs in the repo. Everything an operator or a policy owner rewrites lives in
the database instead and is read per call, so an edit lands on the next draft:

- the always-applied rules (tone, CS policy) — `policy_sources` rows, mode='rules',
  synced from Notion;
- the reply skeleton and the links it ends on — `email_templates` rows;
- the email signature — the `signature_ko` row, injected at `{{__signature__}}`.

Placeholders use Jinja-style `{{ var_name }}`.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
# company_rules/ is gone: the always-applied policy is rows in `policy_sources`, synced
# from Notion, seeded from src/db/seeds/policy/ by migration 0043.

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

# Token inside the rules text, replaced at load time with the web-editable
# email signature (DB-backed). Lets ops edit the outgoing signature from the web
# console without a redeploy. See src/db/email_templates.py + routes/email_templates.py.
_SIGNATURE_TOKEN = "{{__signature__}}"
_DEFAULT_SIGNATURE = "이혜람\nGrowth, Perso Dubbing | ESTsoft\nleehyeram@estsoft.com"


def _rules_from_db() -> str:
    """The always-applied policy, from ``policy_sources`` (mode='rules').

    Read per call, not cached: the whole point of moving these out of the repo is that an
    edit — in Notion, synced here — takes effect on the next draft. One indexed query
    against a handful of rows is cheaper than the confusion of a stale cache.

    Only the SYNCED COPY is read. A Notion outage cannot empty the rules, because nothing
    here talks to Notion.
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


def _company_rules_raw() -> str:
    """The always-applied policy, with the section header the prompts refer to."""
    body = _rules_from_db()
    if not body:
        return ""
    return "## Company rules (must follow)\n\n" + body


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


# The shape every reply must take — opening, middle, closing — as opposed to what it
# says, which is the model's job. Deliberately a DB row and NOT a file: this is the
# part the operator rewrites most often, and every edit here used to need a deploy.
_REPLY_FORMAT_KEY = "reply_format"


def get_reply_format() -> str:
    """The web-editable reply skeleton, or '' when the operator has not set one.

    Read fresh on every draft (one indexed single-row query) so an edit in the console
    applies to the very next reply — the lru_cache on the rules file is exactly the
    behaviour we do not want here.
    """
    try:
        from ..db.email_templates import get_email_template

        body = get_email_template(_REPLY_FORMAT_KEY)
    except Exception:  # a template outage must never block drafting
        return ""
    return (body or "").strip()


# Tokens the model is told to emit verbatim, swapped for the real URLs afterwards. The
# booking URL is ~120 characters of opaque base64 — exactly the kind of string a model
# silently truncates or "tidies", and a broken booking link is a lost meeting.
_LINK_TOKENS = {
    "{{MEETING_LINK}}": "meeting_link",
    "{{WHATSAPP}}": "whatsapp_link",
}


def apply_link_tokens(body: str) -> str:
    """Replace the link tokens in a drafted body with their web-editable values.

    A token whose row is missing or blank is left untouched rather than replaced with an
    empty string: a visible ``{{MEETING_LINK}}`` in the review screen tells the operator
    the link is unset, where a silent blank would ship as a sentence promising a link
    that is not there.
    """
    if not body:
        return body
    from ..db.email_templates import get_email_template

    for token, key in _LINK_TOKENS.items():
        if token not in body:
            continue
        try:
            value = (get_email_template(key) or "").strip()
        except Exception:
            value = ""
        if value:
            body = body.replace(token, value)
    return body


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
