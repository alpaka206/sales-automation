"""화면에 보이는 것만 남기고, 판 번호를 1부터 다시 시작합니다 (2026-08-27 운영자 지시).

운영자가 정한 규칙은 한 줄입니다: **「다 사이트에서 추가하는 걸로만 할 거라 — 만약 필요한
거면 사이트에서도 뜨게 해 줘야 해.」** 그러면 이 표들의 기준도 한 줄이 됩니다: 콘솔에서 보이지
않는 행은 남을 이유가 없습니다. 보이지 않으면 고칠 수도, 지울 수도, 왜 그런 회신이 나갔는지
확인할 수도 없기 때문입니다.

## 1. ``knowledge_documents`` — 21행 → 8행

정책 문서 8편의 **사본만** 남습니다(``policy_sources`` 와 1:1). 지우는 것은 둘입니다.

- **``perso_refund_policy``** — ``status='active'``, ``scope='both'`` 로 살아 있어서 **초안
  라우터가 지금도 고를 수 있었습니다.** 그런데 ``policy_sources`` 에 짝이 없어(사본 slug 는
  ``notion-<해시>`` 입니다) 콘솔 어디에도 안 뜨고 고칠 수도 없었습니다. 이관 0077 이 정확히
  이 상태를 없애려고 저장소 씨앗 문서를 재웠는데, 그 목록(11개)에 이것 하나가 빠져 있었습니다
  — ``perso_faq_refund_cancellation`` 은 있고 ``perso_refund_policy`` 는 없습니다.
- **재워 둔 12개** — 0077 이 재운 11개와 그 전에 재워진 ``pricing_example``. 라우터는
  ``status='active'`` 만 보므로 무해했지만, 화면에 없는 행입니다.

**본문은 전부 저장소에 있습니다**: 0077 이 옮긴 11개는 ``data/knowledge-seeds-removed-
2026-08-19/``, DB 에만 있던 둘은 ``data/knowledge-rows-removed-2026-08-27/``. 되살릴 내용이
있으면 「정책 문서 → 직접 추가」로 넣으면 됩니다 — 그러면 보이는 자리에 놓입니다.

**남길 것을 규칙으로 가립니다** (``notion-`` 접두사 + ``policy_sources`` 에 짝이 있는 행).
0077 은 「지울 것」의 이름을 그대로 적었는데, 그래서 목록에서 빠진 하나가 살아남았습니다.
이번에는 반대로 「남을 것」을 정의합니다 — 빠지면 지워지지, 살아남지 않습니다.

## 2. ``document_revisions`` — 전부 비우고 판 번호를 1로

지금 쌓인 17건은 **판본 기록 화면이 생기기 전**의 것이라 아무도 본 적이 없고, 그중 절반은
마이그레이션이 남긴 것입니다. 운영자가 「사이트에서 보이는 것 빼고는 1로 하고 그 이전 문서는
다 삭제해도 된다」고 정했으므로, 여기서 한 번 끊고 **콘솔에서 저장하는 순간부터** 쌓기
시작합니다. 그래야 화면의 「v3」이 곧 「이 화면에서 세 번 저장했다」는 뜻이 됩니다.

지우기 전 전량을 ``data/document-revisions-removed-2026-08-27/`` 에 떠 두었습니다 —
0069·0086 이 운영자가 쓴 서식을 고치기 **전**의 본문이 거기 있고, 다른 사본이 없습니다.
"""

from __future__ import annotations

import hashlib
import logging

from sqlalchemy import Engine, bindparam, inspect, text

logger = logging.getLogger(__name__)


def _live_copy_slugs(conn) -> set[str]:
    """``policy_sources`` 가 지금 가리키는 사본 slug. ``policy_sync.knowledge_slug`` 와 같은 식."""
    slugs: set[str] = set()
    for (doc_key,) in conn.execute(text("SELECT doc_key FROM policy_sources")).fetchall():
        key = (doc_key or "").strip()
        if not key:
            continue
        digest = key if len(key) == 32 else hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        slugs.add(f"notion-{digest}")
    return slugs


def up(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())

    with engine.begin() as conn:
        # --- 1. 화면에 없는 지식 문서 ---
        if "knowledge_documents" in tables:
            live = _live_copy_slugs(conn) if "policy_sources" in tables else set()
            rows = conn.execute(
                text("SELECT id, slug, status FROM knowledge_documents ORDER BY id")
            ).fetchall()
            doomed = [
                row
                for row in rows
                if not (row.slug or "").startswith("notion-") and row.slug not in live
            ]
            if doomed:
                conn.execute(
                    text("DELETE FROM knowledge_documents WHERE id IN :ids").bindparams(
                        bindparam("ids", expanding=True)
                    ),
                    {"ids": [row.id for row in doomed]},
                )
                logger.info(
                    "0097: knowledge_documents %d행 → %d행. 지운 것: %s",
                    len(rows),
                    len(rows) - len(doomed),
                    ", ".join(f"{row.slug}({row.status})" for row in doomed),
                )
            else:
                logger.info("0097: knowledge_documents 는 이미 사본뿐입니다 (%d행).", len(rows))

        # --- 2. 판본 기록을 비우고 판 번호를 1로 ---
        if "document_revisions" in tables:
            removed = conn.execute(text("DELETE FROM document_revisions")).rowcount or 0
            logger.info("0097: document_revisions %d행을 비웠습니다.", removed)
        for table in ("email_templates", "policy_sources"):
            if table in tables:
                conn.execute(text(f"UPDATE {table} SET version = 1"))
                logger.info("0097: %s.version 을 전부 1로 되돌렸습니다.", table)
