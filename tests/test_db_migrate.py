"""Tests for src.db.migrate — migration runner."""

from __future__ import annotations

import importlib
import pkgutil
import types
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, inspect, text


@pytest.fixture()
def mem_engine():
    """Fresh in-memory SQLite engine per test."""
    return create_engine("sqlite:///:memory:")


class TestEnsureTracker:
    def test_creates_tracker_table(self, mem_engine):
        from src.db.migrate import _ensure_tracker

        _ensure_tracker(mem_engine)
        assert "_migrations" in inspect(mem_engine).get_table_names()

    def test_idempotent(self, mem_engine):
        from src.db.migrate import _ensure_tracker

        _ensure_tracker(mem_engine)
        _ensure_tracker(mem_engine)
        assert "_migrations" in inspect(mem_engine).get_table_names()


class TestApplied:
    def test_empty_when_no_rows(self, mem_engine):
        from src.db.migrate import _applied, _ensure_tracker

        _ensure_tracker(mem_engine)
        assert _applied(mem_engine) == set()

    def test_returns_applied_names(self, mem_engine):
        from src.db.migrate import _applied, _ensure_tracker

        _ensure_tracker(mem_engine)
        with mem_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO _migrations (name, applied_at) VALUES (:n, :t)"
                ),
                {"n": "0001_initial", "t": datetime.now(timezone.utc)},
            )
        assert _applied(mem_engine) == {"0001_initial"}


class TestRunMigrations:
    def _make_migration_module(self, called: list[str], name: str):
        """Return a fake migration module with an `up()` that logs its call."""
        mod = types.ModuleType(f"src.db.migrations.{name}")

        def up(engine):
            called.append(name)

        mod.up = up
        return mod

    def test_applies_new_migrations(self, mem_engine):
        from src.db.migrate import _applied, _ensure_tracker

        called: list[str] = []
        fake_a = self._make_migration_module(called, "0001_a")
        fake_b = self._make_migration_module(called, "0002_b")

        fake_pkg = types.ModuleType("src.db.migrations")
        fake_pkg.__path__ = []

        iter_modules_return = [
            pkgutil.ModuleInfo(None, "0001_a", False),
            pkgutil.ModuleInfo(None, "0002_b", False),
        ]

        with (
            patch("src.db.migrate.engine", mem_engine),
            patch("src.db.migrate.pkgutil.iter_modules", return_value=iter_modules_return),
            patch(
                "src.db.migrate.importlib.import_module",
                side_effect=lambda name: {"src.db.migrations.0001_a": fake_a, "src.db.migrations.0002_b": fake_b}[name],
            ),
        ):
            _ensure_tracker(mem_engine)

            from src.db.migrate import run_migrations

            result = run_migrations()

        assert result == ["0001_a", "0002_b"]
        assert called == ["0001_a", "0002_b"]
        assert _applied(mem_engine) == {"0001_a", "0002_b"}

    def test_skips_already_applied(self, mem_engine):
        from src.db.migrate import _ensure_tracker

        _ensure_tracker(mem_engine)
        with mem_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO _migrations (name, applied_at) VALUES (:n, :t)"
                ),
                {"n": "0001_a", "t": datetime.now(timezone.utc)},
            )

        called: list[str] = []
        fake_a = self._make_migration_module(called, "0001_a")
        fake_b = self._make_migration_module(called, "0002_b")

        fake_pkg = types.ModuleType("src.db.migrations")
        fake_pkg.__path__ = []

        iter_modules_return = [
            pkgutil.ModuleInfo(None, "0001_a", False),
            pkgutil.ModuleInfo(None, "0002_b", False),
        ]

        with (
            patch("src.db.migrate.engine", mem_engine),
            patch("src.db.migrate.pkgutil.iter_modules", return_value=iter_modules_return),
            patch(
                "src.db.migrate.importlib.import_module",
                side_effect=lambda name: {"src.db.migrations.0001_a": fake_a, "src.db.migrations.0002_b": fake_b}[name],
            ),
        ):
            from src.db.migrate import run_migrations

            result = run_migrations()

        assert result == ["0002_b"]
        assert called == ["0002_b"]

    def test_skips_underscore_prefixed(self, mem_engine):
        from src.db.migrate import _ensure_tracker

        called: list[str] = []
        fake_a = self._make_migration_module(called, "0001_a")

        fake_pkg = types.ModuleType("src.db.migrations")
        fake_pkg.__path__ = []

        iter_modules_return = [
            pkgutil.ModuleInfo(None, "__init__", False),
            pkgutil.ModuleInfo(None, "0001_a", False),
        ]

        with (
            patch("src.db.migrate.engine", mem_engine),
            patch("src.db.migrate.pkgutil.iter_modules", return_value=iter_modules_return),
            patch(
                "src.db.migrate.importlib.import_module",
                side_effect=lambda name: {"src.db.migrations.0001_a": fake_a}[name],
            ),
        ):
            _ensure_tracker(mem_engine)

            from src.db.migrate import run_migrations

            result = run_migrations()

        assert result == ["0001_a"]
        assert called == ["0001_a"]

    def test_empty_when_no_migrations(self, mem_engine):
        fake_pkg = types.ModuleType("src.db.migrations")
        fake_pkg.__path__ = []

        with (
            patch("src.db.migrate.engine", mem_engine),
            patch("src.db.migrate.pkgutil.iter_modules", return_value=[]),
        ):
            from src.db.migrate import run_migrations

            result = run_migrations()

        assert result == []


class TestLegacyProspectStatusMigration:
    def test_skips_when_outbound_table_does_not_exist(self, mem_engine):
        migration = importlib.import_module(
            "src.db.migrations.0007_prospect_status_enum"
        )

        migration.up(mem_engine)

        assert "prospects" not in inspect(mem_engine).get_table_names()

    def test_updates_existing_legacy_rows(self, mem_engine):
        migration = importlib.import_module(
            "src.db.migrations.0007_prospect_status_enum"
        )
        with mem_engine.begin() as conn:
            conn.execute(text("CREATE TABLE prospects (id INTEGER, status TEXT)"))
            conn.execute(
                text(
                    "INSERT INTO prospects (id, status) "
                    "VALUES (1, 'candidate'), (2, 'drafted')"
                )
            )

        migration.up(mem_engine)

        with mem_engine.connect() as conn:
            statuses = conn.execute(
                text("SELECT status FROM prospects ORDER BY id")
            ).scalars().all()
        assert statuses == ["collected", "analyzed"]


class TestRetireLegacyPipelineStages:
    """Migration 0040 — the board dropped from 11 stage keys to 7."""

    MODULE = "src.db.migrations.0040_retire_legacy_pipeline_stages"

    def _seed(self, engine):
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE conversations (id INTEGER, stage TEXT)"))
            conn.execute(
                text("CREATE TABLE customer_profiles (contact_id INTEGER, pipeline_stage TEXT)")
            )
            conn.execute(
                text(
                    "INSERT INTO conversations (id, stage) VALUES "
                    "(1, 'follow_up_needed'), (2, 'contracted'), (3, 'onboarding'), "
                    "(4, 'active'), (5, 'negotiation'), (6, 'won')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO customer_profiles (contact_id, pipeline_stage) VALUES "
                    "(1, 'active'), (2, 'closed_lost')"
                )
            )

    def test_skips_when_tables_do_not_exist(self, mem_engine):
        importlib.import_module(self.MODULE).up(mem_engine)

        assert "conversations" not in inspect(mem_engine).get_table_names()

    def test_remaps_both_stage_columns_and_leaves_kept_stages_alone(self, mem_engine):
        self._seed(mem_engine)

        importlib.import_module(self.MODULE).up(mem_engine)

        with mem_engine.connect() as conn:
            stages = conn.execute(
                text("SELECT stage FROM conversations ORDER BY id")
            ).scalars().all()
            profiles = conn.execute(
                text("SELECT pipeline_stage FROM customer_profiles ORDER BY contact_id")
            ).scalars().all()
        assert stages == ["negotiation", "won", "won", "won", "negotiation", "won"]
        assert profiles == ["won", "closed_lost"]

    def test_is_idempotent(self, mem_engine):
        """migrate.py commits up() and the tracker row separately, and CI runs
        init_db.py twice — a second pass must be a no-op, not a second remap."""
        self._seed(mem_engine)
        migration = importlib.import_module(self.MODULE)

        migration.up(mem_engine)
        migration.up(mem_engine)

        with mem_engine.connect() as conn:
            stages = conn.execute(
                text("SELECT stage FROM conversations ORDER BY id")
            ).scalars().all()
        assert stages == ["negotiation", "won", "won", "won", "negotiation", "won"]

    def test_every_target_survives_the_trim(self):
        """A remap that lands on a key the board no longer renders is worse than none."""
        from src.api.routes.customer_ops import VALID_PIPELINE_STAGES

        migration = importlib.import_module(self.MODULE)
        assert set(migration.STAGE_MAPPING.values()) <= VALID_PIPELINE_STAGES
        assert not set(migration.STAGE_MAPPING) & VALID_PIPELINE_STAGES


class TestConversationInquirySubject:
    """Migration 0041 — conversations.topic held two unrelated things."""

    MODULE = "src.db.migrations.0041_conversation_inquiry_subject"

    def _seed(self, engine):
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE conversations (id INTEGER, topic TEXT)"))
            conn.execute(
                text(
                    "INSERT INTO conversations (id, topic) VALUES "
                    "(1, 'pricing_question'), (2, 'Bulk dubbing quote'), "
                    "(3, 'spam'), (4, NULL)"
                )
            )

    def _subjects(self, engine):
        with engine.connect() as conn:
            return conn.execute(
                text("SELECT inquiry_subject FROM conversations ORDER BY id")
            ).scalars().all()

    def test_skips_when_table_does_not_exist(self, mem_engine):
        importlib.import_module(self.MODULE).up(mem_engine)

        assert "conversations" not in inspect(mem_engine).get_table_names()

    def test_renames_and_clears_only_the_category_values(self, mem_engine):
        """A real ticket subject must survive; an AI category must not become one."""
        self._seed(mem_engine)

        importlib.import_module(self.MODULE).up(mem_engine)

        columns = {c["name"] for c in inspect(mem_engine).get_columns("conversations")}
        assert "inquiry_subject" in columns
        assert "topic" not in columns
        assert self._subjects(mem_engine) == [None, "Bulk dubbing quote", None, None]

    def test_is_idempotent(self, mem_engine):
        """migrate.py commits up() and the tracker row separately, and CI runs
        init_db.py twice — the second pass must find the rename already done."""
        self._seed(mem_engine)
        migration = importlib.import_module(self.MODULE)

        migration.up(mem_engine)
        migration.up(mem_engine)

        assert self._subjects(mem_engine) == [None, "Bulk dubbing quote", None, None]


class TestRetireDraftsPastNew:
    """0066 — 이관 전에 이미 갇힌 초안 정리.

    지금부터는 단계를 옮기는 곳이 전부 초안을 닫습니다. 옮겨진 지 오래된 티켓에는 아무도
    다시 오지 않으므로(10분 폴러의 stage reconcile 은 HubSpot 에서 최근에 바뀐 티켓만
    훑습니다) 그 전에 남은 것은 이 이관이 한 번 치웁니다.
    """

    MODULE = "src.db.migrations.0066_retire_drafts_past_new"

    # (대화 단계, 방향, 상태, prompt_variant) — id 는 순서대로 1번부터 매겨집니다.
    ROWS = [
        ("new", "outgoing", "pending_approval", None),  # 아직 New — 검토해서 보낼 답
        ("meeting_link_sent", "outgoing", "pending_approval", None),  # 갇힌 초안
        ("won", "outgoing", "approved", None),  # 워커가 집어 가 나갈 뻔한 것
        ("meeting_link_sent", "outgoing", "sent", None),  # 이미 나간 답
        ("meeting_link_sent", "outgoing", "drafting", None),  # 워커가 쓰는 중
        ("initial", "outgoing", "pending_approval", None),  # 매핑에 없는 단계
        ("meeting_link_sent", "inbound", "received", None),  # 고객 문의
        ("won", "outgoing", "approved", "auto_ack"),  # 접수확인 — 초안이 아닙니다
    ]

    def _seed(self, engine):
        from sqlalchemy.orm import Session

        from src.db.base import Base
        from src.db.models import Contact, Conversation, Message

        Base.metadata.create_all(engine)
        with Session(engine) as session:
            contact = Contact(normalized_email="buyer@example.com", full_name="Buyer")
            session.add(contact)
            session.flush()
            for stage, direction, status, variant in self.ROWS:
                conversation = Conversation(contact_id=contact.id, stage=stage)
                session.add(conversation)
                session.flush()
                session.add(
                    Message(
                        conversation_id=conversation.id,
                        direction=direction,
                        body="",
                        status=status,
                        prompt_variant=variant,
                    )
                )
            session.commit()

    def _statuses(self, engine):
        with engine.connect() as conn:
            return conn.execute(text("SELECT status FROM messages ORDER BY id")).scalars().all()

    def test_skips_when_tables_do_not_exist(self, mem_engine):
        importlib.import_module(self.MODULE).up(mem_engine)

        assert "messages" not in inspect(mem_engine).get_table_names()

    def test_retires_only_unsent_drafts_on_tickets_past_new(self, mem_engine):
        self._seed(mem_engine)

        importlib.import_module(self.MODULE).up(mem_engine)

        assert self._statuses(mem_engine) == [
            "pending_approval",  # 아직 New — 검토해서 보낼 답입니다
            "superseded",
            "superseded",
            "sent",
            "drafting",  # 워커가 끝내면서 같은 판정을 합니다
            "pending_approval",  # "initial" 은 단계가 옮겨진 것이 아닙니다
            "received",
            "approved",  # 접수확인은 그대로 나갑니다
        ]


class TestRemoveInboundAutoAck:
    MODULE = "src.db.migrations.0087_remove_inbound_auto_ack"

    def test_skips_when_tables_do_not_exist(self, mem_engine):
        importlib.import_module(self.MODULE).up(mem_engine)
        assert "messages" not in inspect(mem_engine).get_table_names()

    def test_retires_unsent_preserves_sent_and_removes_templates(self, mem_engine):
        from sqlalchemy.orm import Session

        from src.db.base import Base
        from src.db.models import Contact, Conversation, EmailTemplate, Message

        Base.metadata.create_all(mem_engine)
        with mem_engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX ux_messages_one_auto_ack_per_conversation "
                    "ON messages (conversation_id) WHERE prompt_variant='auto_ack'"
                )
            )
        with Session(mem_engine) as session:
            contact = Contact(normalized_email="ack@example.com", full_name="Ack")
            session.add(contact)
            session.flush()
            for status in ("approved", "sent", "test_sent", "delivery_unknown", "send_failed"):
                conversation = Conversation(contact_id=contact.id, stage="new")
                session.add(conversation)
                session.flush()
                session.add(
                    Message(
                        conversation_id=conversation.id,
                        direction="outgoing",
                        body="received",
                        status=status,
                        prompt_variant="auto_ack",
                    )
                )
            session.add_all(
                [
                    EmailTemplate(key="auto_ack", name="ack", body="ack"),
                    EmailTemplate(key="auto_ack_en", name="ack en", body="ack"),
                    EmailTemplate(key="auto_ack_footer", name="ack footer", body="logo"),
                    EmailTemplate(key="reply_format", name="reply", body="reply"),
                ]
            )
            session.commit()

        migration = importlib.import_module(self.MODULE)
        migration.up(mem_engine)
        migration.up(mem_engine)

        with mem_engine.connect() as conn:
            statuses = conn.execute(text("SELECT status FROM messages ORDER BY id")).scalars().all()
            keys = conn.execute(text("SELECT key FROM email_templates ORDER BY key")).scalars().all()
        assert statuses == [
            "superseded",
            "sent",
            "test_sent",
            "delivery_unknown",
            "superseded",
        ]
        assert keys == ["reply_format"]
        assert "ux_messages_one_auto_ack_per_conversation" not in {
            row["name"] for row in inspect(mem_engine).get_indexes("messages")
        }


class Test0103ContactMovesToTheContract:
    """담당자는 고객이 아니라 **계약**의 것입니다 (2026-08-31 운영자 지시).

    옮기지 복사하지 않습니다 — 두 자리에 두면 콘솔이 계약 쪽을 쓰기 시작한 날부터 고객
    쪽은 낡은 값이고, 어느 쪽이 맞는지 화면만 봐서는 알 수 없습니다.
    """

    MODULE = "src.db.migrations.0103_the_contact_belongs_to_the_contract"

    def test_skips_when_the_table_does_not_exist(self, mem_engine):
        importlib.import_module(self.MODULE).up(mem_engine)

    def test_moves_the_value_onto_every_contract_and_drops_the_old_column(self, mem_engine):
        with mem_engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE clients (client_id INTEGER PRIMARY KEY, company VARCHAR(255), "
                "contact_name VARCHAR(120), contact_info VARCHAR(255))"
            ))
            conn.execute(text(
                "CREATE TABLE client_contracts (id INTEGER PRIMARY KEY, client_id INTEGER, seq INTEGER)"
            ))
            conn.execute(text(
                "INSERT INTO clients VALUES (2094, 'A', '박지훈', 'jh@a.kr'), (2095, 'B', NULL, NULL)"
            ))
            conn.execute(text(
                "INSERT INTO client_contracts VALUES (1, 2094, 1), (2, 2094, 2), (3, 2095, 1)"
            ))

        migration = importlib.import_module(self.MODULE)
        migration.up(mem_engine)
        migration.up(mem_engine)   # 두 번 돌려도 같아야 합니다.

        with mem_engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, contact_name, contact_info FROM client_contracts ORDER BY id"
            )).all()
        # 같은 고객의 계약이 둘이면 둘 다 그 값에서 시작하고, 그 뒤로는 따로 움직입니다.
        assert rows == [(1, "박지훈", "jh@a.kr"), (2, "박지훈", "jh@a.kr"), (3, None, None)]
        assert "contact_name" not in {c["name"] for c in inspect(mem_engine).get_columns("clients")}


# ---------- Supabase 의 공개 API 를 잠그는 자물쇠 (2026-09-09) ----------


class _FakeConn:
    """`SELECT`/`ALTER` 를 받아 적기만 하는 접속."""

    def __init__(self, rows, executed, counts=None):
        self._rows, self._executed = rows, executed
        # 카나리아가 세는 값. 기본은 「켜도 그대로 읽힌다」입니다.
        self._counts = list(counts if counts is not None else [3, 3])

    def scalar(self, statement):
        self._executed.append(str(statement))
        return self._counts.pop(0) if self._counts else 0

    def execute(self, statement, *_args):
        sql = str(statement)
        self._executed.append(sql)
        return list(self._rows) if "pg_class" in sql else None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _FakeEngine:
    def __init__(self, name, rows=(), counts=None):
        self.dialect = type("D", (), {"name": name})()
        self.rows, self.executed = rows, []
        self._counts = counts

    def connect(self):
        return _FakeConn(self.rows, self.executed, self._counts)

    def begin(self):
        return _FakeConn(self.rows, self.executed, self._counts)


def test_sqlite_is_left_alone():
    """로컬은 Supabase 가 아닙니다 — 공개 API 도 `anon` 역할도 없습니다."""
    from src.db.rls import enable_rls_on_public_tables

    engine = _FakeEngine("sqlite")
    assert enable_rls_on_public_tables(engine) == []
    assert engine.executed == []


def test_every_public_table_we_own_gets_locked():
    """Supabase 는 `public` 스키마를 **REST API 로 자동 공개**합니다. 우리가 그 API 를
    안 쓰는 것과 그것이 안 열려 있는 것은 다른 이야기입니다 — `anon` 역할은 RLS 가 없는
    표를 전부 읽고 쓰고 지울 수 있고, 그 표에 고객 메일 본문과 계약 금액이 있습니다."""
    from src.db.rls import enable_rls_on_public_tables

    engine = _FakeEngine("postgresql", rows=[("messages", True), ("contacts", True)])

    assert enable_rls_on_public_tables(engine) == ["messages", "contacts"]
    alters = [sql for sql in engine.executed if "ALTER TABLE" in sql]
    # 카나리아(`_migrations`)를 먼저 켜 보고, 계속 읽히니 나머지를 켭니다.
    assert alters == [
        'ALTER TABLE public."_migrations" ENABLE ROW LEVEL SECURITY',
        'ALTER TABLE public."messages" ENABLE ROW LEVEL SECURITY',
        'ALTER TABLE public."contacts" ENABLE ROW LEVEL SECURITY',
    ]


def test_a_table_this_connection_does_not_own_is_never_touched():
    """**여기가 이 파일에서 유일하게 위험한 줄입니다.**

    RLS 를 우회하는 것은 슈퍼유저·`BYPASSRLS`·**표의 소유자**뿐입니다. 소유자가 아닌
    역할로 붙는 배포에서 RLS 를 켜면 그 순간부터 **모든 조회가 0행**입니다 — 에러도 없이
    콘솔이 통째로 빈 화면이 되고, 되돌리려면 DB 에 직접 붙어야 합니다. 그래서 표마다
    `pg_has_role(...)` 를 묻고, 거짓인 표는 이름만 경고로 남기고 지나갑니다.
    """
    from src.db.rls import enable_rls_on_public_tables

    engine = _FakeEngine("postgresql", rows=[("ours", True), ("someone_elses", False)])

    assert enable_rls_on_public_tables(engine) == ["ours"]
    assert not any("someone_elses" in sql for sql in engine.executed if "ALTER" in sql)


def test_the_lock_runs_on_every_deploy_not_once():
    """이관 파일에 두면 **한 번만** 돕니다. 내일 표가 하나 더 생기면 그 표만 열린 채로
    서고, 그건 화면 어디에도 안 보입니다 — Supabase 가 메일을 보낼 뿐입니다."""
    import pathlib

    source = pathlib.Path("src/db/migrate.py").read_text(encoding="utf-8")
    assert "enable_rls_on_public_tables(engine)" in source
    moved = [
        p.name
        for p in pathlib.Path("src/db/migrations").glob("*.py")
        if "enable_rls_on_public_tables" in p.read_text(encoding="utf-8")
    ]
    assert not moved, f"이관 파일로 옮기면 새 표가 안 잠깁니다: {moved}"


def test_it_refuses_to_lock_us_out_of_our_own_database():
    """**켜기 전에 표 하나로 확인합니다** (2026-09-09).

    소유자 우회는 Postgres 의 확정된 동작이고 위 `pg_has_role` 검사도 그것을 묻는다.
    그래도 확인을 한 번 더 하는 이유는 **틀렸을 때의 대가**다: 콘솔이 에러 없이 통째로
    빈 화면이 되고, 되돌리려면 Postgres 에 직접 붙어야 하는데 **사무실 망이 그 포트를
    막고 있다.** 되돌릴 수 없는 자리에서는 확인이 싸다.

    카나리아는 `_migrations` 다 — 이 함수가 이관 직후에 도므로 그 표에는 반드시 행이
    있고, 켠 뒤에 0행이면 그건 빈 표가 아니라 「우리가 우회를 못 한다」는 뜻이다.
    """
    from src.db.rls import enable_rls_on_public_tables

    # 카나리아를 켰더니 3행 → 0행. 우회를 못 하고 있다.
    engine = _FakeEngine("postgresql", rows=[("messages", True)], counts=[3, 0])

    assert enable_rls_on_public_tables(engine) == []
    alters = [sql for sql in engine.executed if "ALTER TABLE" in sql]
    # 카나리아를 켰다가 **그 자리에서 다시 껐고**, 다른 표는 건드리지 않았다.
    assert alters == [
        'ALTER TABLE public."_migrations" ENABLE ROW LEVEL SECURITY',
        'ALTER TABLE public."_migrations" DISABLE ROW LEVEL SECURITY',
    ]
    assert not any("messages" in sql for sql in alters)
