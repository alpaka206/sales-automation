"""Add messages.signature_key + seed branded HTML signature templates.

- ``messages.signature_key``: operator-selected outgoing signature. NULL/'' keeps
  the default text signature; 'none' = no signature; otherwise an email_templates
  key (e.g. 'signature_html_ko') whose branded HTML card is attached at send time.
- Seeds two editable branded signature templates (signature_html_ko / _en) from
  the starter files in src/db/seeds/. The operator pastes the full version (photo,
  logo, G2 badge) into the web console editor afterwards.

Additive + idempotent. Works on SQLite and Postgres. A missing/inactive template
just means no card is attached, so this can never break the send path.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_SEEDS_DIR = Path(__file__).resolve().parents[1] / "seeds"

# (key, name, language, seed filename, minimal fallback if the file is missing)
_BRANDED = [
    (
        "signature_html_ko",
        "브랜드 서명 (한국어)",
        "ko",
        "signature_html_ko.html",
        '<div style="font-family:sans-serif;font-size:13px;color:#222633;">'
        "이혜람 · Perso Dubbing<br>"
        '<a href="mailto:leehyeram@estsoft.com" style="color:#222633;">leehyeram@estsoft.com</a></div>',
    ),
    (
        "signature_html_en",
        "Branded signature (English)",
        "en",
        "signature_html_en.html",
        '<div style="font-family:sans-serif;font-size:13px;color:#222633;">'
        "Hyeram Lee · Perso Dubbing<br>"
        '<a href="mailto:leehyeram@estsoft.com" style="color:#222633;">leehyeram@estsoft.com</a></div>',
    ),
]


def _seed_body(filename: str, fallback: str) -> str:
    path = _SEEDS_DIR / filename
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("0022: seed file %s missing — using minimal fallback.", filename)
        return fallback


def up(engine: Engine) -> None:
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    is_sqlite = engine.dialect.name == "sqlite"
    ts_default = "CURRENT_TIMESTAMP" if is_sqlite else "now()"

    # --- 1. messages.signature_key column ---
    if "messages" in tables:
        cols = {c["name"] for c in insp.get_columns("messages")}
        if "signature_key" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE messages ADD COLUMN signature_key VARCHAR"))
            logger.info("0022: added messages.signature_key")

    # --- 2. seed branded signature templates (skip keys that already exist) ---
    if "email_templates" not in tables:
        logger.info("0022: email_templates not present yet — skipping signature seed.")
        return

    with engine.begin() as conn:
        existing = {r[0] for r in conn.execute(text("SELECT key FROM email_templates"))}
        for key, name, language, filename, fallback in _BRANDED:
            if key in existing:
                continue
            conn.execute(
                text(
                    "INSERT INTO email_templates (key, name, language, channel, body, "
                    "description, status, version, created_at, updated_at) VALUES "
                    "(:key, :name, :language, 'email', :body, :description, 'active', 1, "
                    f"{ts_default}, {ts_default})"
                ),
                {
                    "key": key,
                    "name": name,
                    "language": language,
                    "body": _seed_body(filename, fallback),
                    "description": (
                        "회신 작성 화면의 '서명' 드롭다운에서 선택하는 브랜드 HTML 서명. "
                        "전체 버전(사진·로고·G2 배지)을 여기에 붙여넣으세요."
                    ),
                },
            )
            logger.info("0022: seeded branded signature template %s", key)

    logger.info("0022: message signature column + branded seeds ready.")
