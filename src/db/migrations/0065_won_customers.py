"""수주 고객 — 고객(Client) 하나 아래 계약이 쌓이는 장부.

지금까지 수주는 ``contract_records`` 한 줄이 전부였습니다. 운영자가 실제로 굴리는 장부는
그보다 두 단계 깊습니다: 고객 아래 계약이 여럿이고(재계약), 계약 아래 크레딧 지급 회차와
분납 회차와 클레임이 각각 여럿입니다. 한 줄로는 "다음 지급일", "수금율", "월간 매출"을
계산할 수가 없어서 사람이 시트에서 손으로 세고 있었습니다.

**고객을 ``contacts`` 로 대신할 수 없습니다.** Contact 는 이메일 신원이라 한 회사에 담당자가
셋이면 셋이고, Outbound·Interactive·AX 고객은 문의를 보낸 적이 없어 아예 없습니다. 그래서
``clients`` 가 따로 있고 인바운드 고객만 Contact 를 가리킵니다.

소통 히스토리는 새 테이블을 만들지 않았습니다. 협상 단계 대화가 계약보다 먼저 쌓이고 그게
그대로 이어져야 하는데, 그건 이미 ``customer_interactions`` 가 하는 일입니다 — 계약 차수 칸
하나만 더합니다.

``contract_records`` 는 지우지 않습니다. 운영 DB 에 0 행이라 옮길 것이 없고, 견적서·계약서
인쇄가 아직 그 테이블을 읽습니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_TABLES: dict[str, str] = {
    "clients": """
        CREATE TABLE clients (
            client_id INTEGER PRIMARY KEY,
            company VARCHAR(255) NOT NULL,
            industry VARCHAR(64),
            country VARCHAR(64),
            department VARCHAR(32),
            contact_name VARCHAR(120),
            contact_info VARCHAR(255),
            first_won_on VARCHAR(10),
            plan_status VARCHAR(16) NOT NULL DEFAULT '세팅중',
            owner VARCHAR(120),
            contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
            created_at TIMESTAMP NOT NULL DEFAULT {now},
            updated_at TIMESTAMP NOT NULL DEFAULT {now}
        )
    """,
    "client_contracts": """
        CREATE TABLE client_contracts (
            id {pk},
            client_id INTEGER NOT NULL REFERENCES clients(client_id) ON DELETE CASCADE,
            seq INTEGER NOT NULL DEFAULT 1,
            ticket_id VARCHAR(64),
            deal_type VARCHAR(8) NOT NULL DEFAULT 'MRR',
            starts_on VARCHAR(10),
            ends_on VARCHAR(10),
            doc_types {json},
            credits INTEGER,
            currency VARCHAR(8) NOT NULL DEFAULT 'KRW',
            amount_incl_vat NUMERIC(18,2),
            amount_excl_vat NUMERIC(18,2),
            unit_price NUMERIC(12,4),
            unit_currency VARCHAR(8),
            unit_fx_rate NUMERIC(12,4),
            payment_method VARCHAR(32),
            payment_type VARCHAR(16),
            installments INTEGER,
            first_payment_on VARCHAR(10),
            billing_email VARCHAR(255),
            note TEXT,
            renewal_plan VARCHAR(32),
            stop_reason TEXT,
            memo TEXT,
            revenue_from VARCHAR(7),
            plan VARCHAR(32),
            plan_name VARCHAR(120),
            perso_email VARCHAR(255),
            plan_starts_on VARCHAR(10),
            plan_ends_on VARCHAR(10),
            invite_limit INTEGER,
            queue_limit INTEGER,
            concurrent_jobs INTEGER,
            space_count INTEGER,
            space_seq TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT {now},
            updated_at TIMESTAMP NOT NULL DEFAULT {now}
        )
    """,
    "contract_credit_grants": """
        CREATE TABLE contract_credit_grants (
            id {pk},
            contract_id INTEGER NOT NULL REFERENCES client_contracts(id) ON DELETE CASCADE,
            no INTEGER NOT NULL DEFAULT 1,
            total INTEGER NOT NULL DEFAULT 1,
            grant_on VARCHAR(10),
            amount INTEGER,
            granted_by VARCHAR(120),
            done BOOLEAN NOT NULL DEFAULT FALSE,
            memo TEXT
        )
    """,
    "contract_payments": """
        CREATE TABLE contract_payments (
            id {pk},
            contract_id INTEGER NOT NULL REFERENCES client_contracts(id) ON DELETE CASCADE,
            no INTEGER NOT NULL DEFAULT 1,
            total INTEGER NOT NULL DEFAULT 1,
            paid_on VARCHAR(10),
            amount NUMERIC(18,2),
            done BOOLEAN NOT NULL DEFAULT FALSE,
            fx_rate NUMERIC(12,4),
            fx_on VARCHAR(10)
        )
    """,
    "contract_claims": """
        CREATE TABLE contract_claims (
            id {pk},
            contract_id INTEGER NOT NULL REFERENCES client_contracts(id) ON DELETE CASCADE,
            kind VARCHAR(200) NOT NULL,
            happened_on VARCHAR(10),
            compensation VARCHAR(200),
            progress VARCHAR(32) NOT NULL DEFAULT '접수',
            action_on VARCHAR(10)
        )
    """,
    "pending_won": """
        CREATE TABLE pending_won (
            id {pk},
            ticket_id VARCHAR(64) NOT NULL UNIQUE,
            company VARCHAR(255),
            client_id INTEGER,
            conversation_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
            won_type VARCHAR(32),
            won_on VARCHAR(10),
            status VARCHAR(16) NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP NOT NULL DEFAULT {now}
        )
    """,
}

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_clients_contact ON clients (contact_id)",
    "CREATE INDEX IF NOT EXISTS ix_client_contracts_client ON client_contracts (client_id)",
    "CREATE INDEX IF NOT EXISTS ix_client_contracts_ticket ON client_contracts (ticket_id)",
    # 같은 고객에 같은 차수가 둘 있으면 어느 것이 2차인지 화면이 못 정합니다.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_client_contract_seq "
    "ON client_contracts (client_id, seq)",
    "CREATE INDEX IF NOT EXISTS ix_credit_grants_contract "
    "ON contract_credit_grants (contract_id)",
    "CREATE INDEX IF NOT EXISTS ix_payments_contract ON contract_payments (contract_id)",
    "CREATE INDEX IF NOT EXISTS ix_claims_contract ON contract_claims (contract_id)",
    "CREATE INDEX IF NOT EXISTS ix_pending_won_ticket ON pending_won (ticket_id)",
    "CREATE INDEX IF NOT EXISTS ix_pending_won_client ON pending_won (client_id)",
)


def up(engine: Engine) -> None:
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    is_sqlite = engine.dialect.name == "sqlite"
    fmt = {
        "now": "CURRENT_TIMESTAMP" if is_sqlite else "NOW()",
        "pk": "INTEGER PRIMARY KEY AUTOINCREMENT" if is_sqlite else "SERIAL PRIMARY KEY",
        "json": "JSON" if is_sqlite else "JSONB",
    }

    with engine.begin() as conn:
        for name, ddl in _TABLES.items():
            if name in existing:
                continue
            conn.execute(text(ddl.format(**fmt)))
            logger.info("0065: created %s", name)
        for statement in _INDEXES:
            conn.execute(text(statement))

    # 소통 히스토리는 고객 단위라 기존 테이블을 그대로 씁니다. 계약 차수만 붙입니다 —
    # 비어 있으면 "협상 단계(계약 전)" 기록입니다.
    if "customer_interactions" in existing:
        columns = {c["name"] for c in inspector.get_columns("customer_interactions")}
        if "contract_seq" not in columns:
            with engine.begin() as conn:
                conn.execute(
                    text("ALTER TABLE customer_interactions ADD COLUMN contract_seq INTEGER")
                )
            logger.info("0065: customer_interactions.contract_seq added.")
