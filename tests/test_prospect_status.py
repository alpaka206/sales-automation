"""Tests for prospect status enum, transition matrix, and migration."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.agents.outbound.status import (
    VALID_TRANSITIONS,
    InvalidStatusTransition,
    KR_LABELS,
    ProspectStatus,
    transition,
)
from src.db.base import Base
from src.db import models as _models  # noqa: F401
from src.db.models import Prospect


@pytest.fixture()
def db_engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def db_session(db_engine):
    session = sessionmaker(bind=db_engine, expire_on_commit=False)()
    yield session
    session.close()


def _make_prospect(session, status: str = "collected") -> Prospect:
    p = Prospect(
        source="test",
        full_name="Test User",
        status=status,
    )
    session.add(p)
    session.flush()
    return p


class TestProspectStatusEnum:
    def test_all_values(self):
        expected = {
            "collected", "analyzed", "sent", "replied",
            "in_progress", "won", "lost",
            "skipped_lowscore", "skipped_dup", "bounced",
        }
        assert {s.value for s in ProspectStatus} == expected

    def test_str_enum(self):
        assert ProspectStatus.COLLECTED == "collected"
        assert str(ProspectStatus.WON) == "ProspectStatus.WON"

    def test_kr_labels_complete(self):
        for s in ProspectStatus:
            assert s.value in KR_LABELS, f"Missing KR label for {s.value}"


class TestTransitionMatrix:
    def test_all_statuses_in_matrix(self):
        for s in ProspectStatus:
            assert s in VALID_TRANSITIONS, f"{s} missing from VALID_TRANSITIONS"

    def test_terminal_states_have_no_transitions(self):
        terminals = {
            ProspectStatus.WON,
            ProspectStatus.LOST,
            ProspectStatus.SKIPPED_LOWSCORE,
            ProspectStatus.SKIPPED_DUP,
            ProspectStatus.BOUNCED,
        }
        for t in terminals:
            assert VALID_TRANSITIONS[t] == set(), f"{t} should be terminal"

    def test_collected_can_reach_analyzed(self):
        assert ProspectStatus.ANALYZED in VALID_TRANSITIONS[ProspectStatus.COLLECTED]

    def test_analyzed_can_reach_sent(self):
        assert ProspectStatus.SENT in VALID_TRANSITIONS[ProspectStatus.ANALYZED]

    def test_sent_can_reach_replied(self):
        assert ProspectStatus.REPLIED in VALID_TRANSITIONS[ProspectStatus.SENT]


class TestTransitionFunction:
    def test_valid_transition(self, db_session):
        p = _make_prospect(db_session, "collected")
        result = transition(db_session, p.id, ProspectStatus.ANALYZED)
        assert result.status == "analyzed"

    def test_valid_transition_with_string(self, db_session):
        p = _make_prospect(db_session, "collected")
        result = transition(db_session, p.id, "analyzed")
        assert result.status == "analyzed"

    def test_invalid_transition_raises(self, db_session):
        p = _make_prospect(db_session, "collected")
        with pytest.raises(InvalidStatusTransition):
            transition(db_session, p.id, ProspectStatus.SENT)

    def test_terminal_state_rejects_all(self, db_session):
        p = _make_prospect(db_session, "won")
        for target in ProspectStatus:
            with pytest.raises(InvalidStatusTransition):
                transition(db_session, p.id, target)

    def test_nonexistent_prospect_raises(self, db_session):
        with pytest.raises(ValueError, match="not found"):
            transition(db_session, 99999, ProspectStatus.ANALYZED)

    def test_reason_logged(self, db_session):
        p = _make_prospect(db_session, "sent")
        result = transition(db_session, p.id, ProspectStatus.REPLIED, reason="reply_detected")
        assert result.status == "replied"

    def test_full_happy_path(self, db_session):
        p = _make_prospect(db_session, "collected")
        transition(db_session, p.id, ProspectStatus.ANALYZED)
        transition(db_session, p.id, ProspectStatus.SENT)
        transition(db_session, p.id, ProspectStatus.REPLIED)
        transition(db_session, p.id, ProspectStatus.IN_PROGRESS)
        transition(db_session, p.id, ProspectStatus.WON)
        assert p.status == "won"


class TestMigration:
    def test_legacy_values_mapped(self, db_engine):
        with db_engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO prospects (source, full_name, status, follow_up_count, created_at) "
                "VALUES ('test', 'A', 'candidate', 0, '2025-01-01'), "
                "('test', 'B', 'drafted', 0, '2025-01-01')"
            ))

        spec = importlib.util.spec_from_file_location(
            "migration_0007",
            Path("src/db/migrations/0007_prospect_status_enum.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        up = mod.up
        up(db_engine)

        with db_engine.connect() as conn:
            rows = conn.execute(text("SELECT full_name, status FROM prospects ORDER BY full_name")).fetchall()

        assert rows[0][1] == "collected"
        assert rows[1][1] == "analyzed"

    def test_unknown_values_unchanged(self, db_engine):
        with db_engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO prospects (source, full_name, status, follow_up_count, created_at) "
                "VALUES ('test', 'C', 'skipped_dup', 0, '2025-01-01')"
            ))

        spec = importlib.util.spec_from_file_location(
            "migration_0007",
            Path("src/db/migrations/0007_prospect_status_enum.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        up = mod.up
        up(db_engine)

        with db_engine.connect() as conn:
            row = conn.execute(text("SELECT status FROM prospects WHERE full_name = 'C'")).fetchone()

        assert row[0] == "skipped_dup"


class TestModelDefault:
    def test_prospect_default_status(self, db_session):
        p = Prospect(source="test", full_name="Default User")
        db_session.add(p)
        db_session.flush()
        assert p.status == "collected"
