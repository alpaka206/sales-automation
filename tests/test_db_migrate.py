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
