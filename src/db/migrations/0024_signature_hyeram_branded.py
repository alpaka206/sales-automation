"""Consolidate the branded email signature to a single 이혜람 / Hyeram Lee footer
and clean up unused email templates.

The compose-screen picker (``list_signature_templates``) lists any active
``signature_html_*`` template. We want ONE branded footer, not a ko/en split:

- INSERT ``signature_html_hyeram`` — "이혜람 (Perso Dubbing)", language "all".
  Body references images at ``/static/signatures/...`` (dropped in later); the
  text/layout render immediately.
- DELETE the superseded / unused templates:
  * ``signature_html_ko`` / ``signature_html_en`` — old image-light starters (0022).
  * ``signature_html_hyeram_ko`` / ``signature_html_hyeram_en`` — the earlier
    per-language split, now consolidated.
  * ``greeting`` — seeded once but referenced by no code path.

KEPT (referenced by code): ``auto_ack``, ``footer_note``, ``signature_ko`` /
``signature_en`` (the plain-text default signature fed to ``{{__signature__}}``).

Additive + idempotent (skips the insert if the key exists; DELETEs are
IF-present). SQLite + Postgres safe.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_SEEDS_DIR = Path(__file__).resolve().parents[1] / "seeds"

_KEY = "signature_html_hyeram"
_NAME = "이혜람 (Perso Dubbing)"
_SEED_FILE = "signature_html_hyeram.html"
_FALLBACK = (
    '<div style="font-family:Pretendard;font-size:13px;color:#222633;">'
    "이혜람 · Hyeram Lee — Perso Dubbing<br>"
    '<a href="mailto:leehyeram@estsoft.com" style="color:#222633;">leehyeram@estsoft.com</a></div>'
)

# Templates to remove: old starters, the per-language split, and the unused greeting.
_REMOVE = (
    "signature_html_ko",
    "signature_html_en",
    "signature_html_hyeram_ko",
    "signature_html_hyeram_en",
    "greeting",
)


def _seed_body() -> str:
    try:
        return (_SEEDS_DIR / _SEED_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("0024: seed file %s missing — using minimal fallback.", _SEED_FILE)
        return _FALLBACK


def up(engine: Engine) -> None:
    if "email_templates" not in set(inspect(engine).get_table_names()):
        logger.info("0024: email_templates not present — skipping.")
        return

    is_sqlite = engine.dialect.name == "sqlite"
    ts_default = "CURRENT_TIMESTAMP" if is_sqlite else "now()"

    with engine.begin() as conn:
        existing = {r[0] for r in conn.execute(text("SELECT key FROM email_templates"))}
        if _KEY not in existing:
            conn.execute(
                text(
                    "INSERT INTO email_templates (key, name, language, channel, body, "
                    "description, status, version, created_at, updated_at) VALUES "
                    "(:key, :name, 'all', 'email', :body, :description, 'active', 1, "
                    f"{ts_default}, {ts_default})"
                ),
                {
                    "key": _KEY,
                    "name": _NAME,
                    "body": _seed_body(),
                    "description": (
                        "회신 작성 화면 '서명' 드롭다운의 브랜드 HTML 푸터 (이혜람). "
                        "이미지는 /static/signatures/ 에 파일을 넣으면 표시됩니다."
                    ),
                },
            )
            logger.info("0024: seeded branded footer %s", _KEY)

        for key in _REMOVE:
            conn.execute(text("DELETE FROM email_templates WHERE key=:key"), {"key": key})
        logger.info("0024: removed unused templates %s", ", ".join(_REMOVE))

    logger.info("0024: single 이혜람 branded footer ready.")
