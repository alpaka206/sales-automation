"""저장소에 딸려 오던 지식 문서 11개를 재웁니다. 관리는 콘솔 한 곳에서.

`src/db/seeds/knowledge/*.md` 는 손으로 돌리는 스크립트(`scripts/import_knowledge_base.py`)
하나만 읽었고, 배포 경로(`render.yaml` → `init_db.py`)는 마이그레이션만 돌립니다. 그래서 이
파일들은 **런타임의 일부가 아니었습니다** — 다만 과거에 누가 그 스크립트를 한 번 돌렸다면
그 내용이 `knowledge_documents` 에 남아 있고, 콘솔에는 그것을 보여 주는 화면이 없습니다.
초안이 읽는 문서인데 운영자가 못 보고 못 고치는 상태입니다(2026-08-19 운영자 지시:
「다 템플릿에서 관리하는 게 맞다」).

**지우지 않고 재웁니다.** `llm/knowledge` 는 `status == "active"` 만 후보로 올리므로
`archived` 로 두면 초안이 즉시 안 읽습니다. 행은 남으므로 되돌리는 것은 UPDATE 한 줄이고,
무엇이 있었는지도 남습니다 — 지워 버리면 「원래 있었나」를 다시는 알 수 없습니다.

**대상은 그 11개 slug 뿐입니다.** 콘솔의 정책 문서 사본(`refresh_knowledge_copy` 가 만드는
행)은 건드리면 안 되므로, 규칙으로 거르지 않고 이름을 그대로 적습니다. 이름이 곧 파일명이고
(importer 가 `path.stem` 을 slug 로 썼습니다) 그 파일들은 이 커밋에서 저장소에서도 지웠습니다.

지운 파일 원본은 `data/knowledge-seeds-removed-2026-08-19/` 와 git 이력에 있습니다. 그중
남길 내용이 있으면 콘솔의 「정책 문서 → 직접 추가」로 옮기면 됩니다 — 그러면 운영자가 보고
고칠 수 있는 자리에 놓입니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, bindparam, inspect, text

logger = logging.getLogger(__name__)

# importer 가 slug 로 쓰던 파일 이름(확장자 제외) 그대로입니다.
SEED_SLUGS = (
    "perso_business_plan",
    "perso_customer_cases",
    "perso_enterprise_workflow",
    "perso_faq_billing_account",
    "perso_faq_dubbing",
    "perso_faq_enterprise_general",
    "perso_faq_policy_and_errors",
    "perso_faq_refund_cancellation",
    "perso_faq_studio",
    "perso_pricing",
    "perso_product",
)


def up(engine: Engine) -> None:
    if "knowledge_documents" not in set(inspect(engine).get_table_names()):
        logger.info("0077: knowledge_documents 없음, 건너뜁니다")
        return
    with engine.begin() as conn:
        # **무엇이 있었는지 먼저 남깁니다.** 이 로그가 「운영 DB 에 씨앗이 들어가 있었나」라는
        # 질문의 유일한 답입니다 — 재운 뒤에는 화면으로도 확인할 길이 없습니다.
        found = [
            row[0]
            for row in conn.execute(
                # `expanding` 이 없으면 SQLite 가 튜플 하나를 자리표시자 하나로 받아
                # "IN ?" 구문 오류가 납니다.
                text("SELECT slug FROM knowledge_documents WHERE slug IN :slugs").bindparams(
                    bindparam("slugs", expanding=True)
                ),
                {"slugs": list(SEED_SLUGS)},
            )
        ]
        if not found:
            logger.info("0077: 저장소 씨앗 지식 문서가 이 DB 에는 없습니다 (아무것도 안 함).")
            return
        result = conn.execute(
            text(
                "UPDATE knowledge_documents SET status = 'archived' "
                "WHERE slug IN :slugs AND (status IS NULL OR status = 'active')"
            ).bindparams(bindparam("slugs", expanding=True)),
            {"slugs": list(SEED_SLUGS)},
        )
        logger.info(
            "0077: 저장소 씨앗 지식 문서 %d개를 찾아 %d개를 재웠습니다: %s",
            len(found), result.rowcount or 0, ", ".join(sorted(found)),
        )
