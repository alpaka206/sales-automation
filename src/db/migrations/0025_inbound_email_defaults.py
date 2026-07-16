"""Align editable email defaults with the inbound-only workflow."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, inspect, text

_SEED = Path(__file__).resolve().parents[1] / "seeds" / "signature_html_hyeram.html"
_ACK = (
    "안녕하세요 {name}님,\n\n"
    "문의 주셔서 감사합니다. 보내주신 메일은 잘 도착했으며, "
    "담당자가 내용을 확인한 뒤 곧 자세한 답변을 보내드리겠습니다.\n\n"
    "감사합니다."
)
_SIG_KO = "이혜람\nGrowth, Perso Dubbing | ESTsoft\nleehyeram@estsoft.com"
_SIG_EN = "Hyeram Lee\nGrowth, Perso Dubbing | ESTsoft\nleehyeram@estsoft.com"


def up(engine: Engine) -> None:
    if "email_templates" not in set(inspect(engine).get_table_names()):
        return
    branded = _SEED.read_text(encoding="utf-8").strip()
    with engine.begin() as conn:
        conn.execute(text("UPDATE messages SET direction='outgoing' WHERE direction='outbound'"))
        conn.execute(text("DELETE FROM knowledge_documents WHERE scope='outbound'"))
        for key, body in (
            ("auto_ack", _ACK),
            ("signature_ko", _SIG_KO),
            ("signature_en", _SIG_EN),
            ("signature_html_hyeram", branded),
        ):
            conn.execute(
                text(
                    "UPDATE email_templates SET body=:body, updated_at=CURRENT_TIMESTAMP "
                    "WHERE key=:key"
                ),
                {"key": key, "body": body},
            )
        conn.execute(text("DELETE FROM email_templates WHERE key='footer_note'"))
        conn.execute(text("DROP TABLE IF EXISTS email_suppression"))
