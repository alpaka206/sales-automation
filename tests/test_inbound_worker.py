"""Durability and retry checks for the database-backed inbound worker."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.agents.inbound_worker import (
    _finish_job,
    _renew_job_lease,
    enqueue_inbound_ticket,
    inbound_event_key,
    process_one_inbound_job,
    run_inbound_worker,
)
from src.db.models import InboundJob
from src.db.session import SessionLocal


@pytest.fixture(autouse=True)
def _clean_jobs():
    with SessionLocal() as session:
        session.query(InboundJob).delete()
        session.commit()
    yield
    with SessionLocal() as session:
        session.query(InboundJob).delete()
        session.commit()


def test_enqueue_uses_one_stable_key_across_sources() -> None:
    assert enqueue_inbound_ticket("T-1", source="webhook")
    assert not enqueue_inbound_ticket("T-1", source="poller")
    with SessionLocal() as session:
        jobs = session.query(InboundJob).all()
        assert len(jobs) == 1
        assert jobs[0].event_key == inbound_event_key("T-1")


@patch("src.agents.inbound.InboundAgent")
@patch("src.integrations.hubspot.HubSpotClient")
def test_worker_resolves_contact_and_completes(mock_hubspot_cls, mock_agent_cls) -> None:
    enqueue_inbound_ticket("T-2", source="webhook", occurred_at="2026-07-18T00:00:00Z")
    mock_hubspot_cls.return_value.get_ticket_primary_contact_sync.return_value = "C-2"

    assert process_one_inbound_job()

    mock_agent_cls.return_value.handle.assert_called_once_with(
        {
            "event_type": "ticket_created",
            "object_id": "C-2",
            "ticket_id": "T-2",
            "occurred_at": "2026-07-18T00:00:00Z",
            "_inbound_job_id": 1,
            "_draft_message_id": None,
        }
    )
    with SessionLocal() as session:
        job = session.query(InboundJob).one()
        assert job.status == "done"
        assert job.attempts == 1
        assert job.completed_at is not None


@patch("src.integrations.hubspot.HubSpotClient")
def test_worker_retries_when_contact_is_not_associated_yet(mock_hubspot_cls) -> None:
    enqueue_inbound_ticket("T-3", source="poller")
    mock_hubspot_cls.return_value.get_ticket_primary_contact_sync.return_value = None

    assert process_one_inbound_job()

    with SessionLocal() as session:
        job = session.query(InboundJob).one()
        assert job.status == "pending"
        assert job.attempts == 1
        assert job.available_at > datetime.now(timezone.utc).replace(tzinfo=None)
        assert "no associated contact" in (job.last_error or "")


@patch("src.agents.inbound.InboundAgent")
@patch("src.integrations.hubspot.HubSpotClient")
def test_worker_retries_until_ticket_body_is_available(mock_hubspot_cls, mock_agent_cls) -> None:
    enqueue_inbound_ticket("T-3-body", source="webhook")
    mock_hubspot_cls.return_value.get_ticket_primary_contact_sync.return_value = "C-3"
    mock_agent_cls.return_value.handle.return_value = {"status": "skipped_no_body"}

    assert process_one_inbound_job()

    with SessionLocal() as session:
        job = session.query(InboundJob).one()
        assert job.status == "pending"
        assert job.attempts == 1
        assert "body is not available" in (job.last_error or "")


@pytest.mark.parametrize("status", ["skipped_not_new", "skipped_existing_pending"])
@patch("src.agents.inbound.InboundAgent")
@patch("src.integrations.hubspot.HubSpotClient")
def test_terminal_skips_complete_the_job(
    mock_hubspot_cls, mock_agent_cls, status: str
) -> None:
    enqueue_inbound_ticket(f"T-{status}", source="webhook")
    mock_hubspot_cls.return_value.get_ticket_primary_contact_sync.return_value = "C-4"
    mock_agent_cls.return_value.handle.return_value = {"status": status}

    assert process_one_inbound_job()

    with SessionLocal() as session:
        assert session.query(InboundJob).one().status == "done"


@patch("src.agents.inbound.InboundAgent")
@patch("src.integrations.hubspot.HubSpotClient")
def test_stale_processing_lease_is_reclaimed(mock_hubspot_cls, mock_agent_cls) -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        session.add(
            InboundJob(
                event_key=inbound_event_key("T-4"),
                source="webhook",
                payload={"ticket_id": "T-4", "event_type": "ticket_created"},
                status="processing",
                attempts=1,
                available_at=now - timedelta(hours=1),
                locked_at=now - timedelta(hours=1),
            )
        )
        session.commit()
    mock_hubspot_cls.return_value.get_ticket_primary_contact_sync.return_value = "C-4"

    assert process_one_inbound_job()
    mock_agent_cls.return_value.handle.assert_called_once()
    with SessionLocal() as session:
        job = session.query(InboundJob).one()
        assert job.status == "done"
        assert job.attempts == 2


def test_job_owner_heartbeat_and_compare_and_set_completion() -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        session.add(
            InboundJob(
                event_key=inbound_event_key("T-owner"),
                source="webhook",
                payload={"ticket_id": "T-owner"},
                status="processing",
                attempts=1,
                available_at=now,
                locked_at=now - timedelta(minutes=5),
                locked_by="current-owner",
            )
        )
        session.commit()
        job_id = session.query(InboundJob.id).scalar()

    assert not _renew_job_lease(job_id, "stale-owner")
    assert _renew_job_lease(job_id, "current-owner")
    _finish_job(job_id, "stale-owner")
    with SessionLocal() as session:
        assert session.get(InboundJob, job_id).status == "processing"

    _finish_job(job_id, "current-owner")
    with SessionLocal() as session:
        job = session.get(InboundJob, job_id)
        assert job.status == "done"
        assert job.locked_by is None


@pytest.mark.asyncio
async def test_worker_loop_survives_iteration_failure(monkeypatch) -> None:
    calls = 0

    async def fake_to_thread(_fn, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary database outage")
        raise asyncio.CancelledError

    monkeypatch.setattr("src.agents.inbound_worker.asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr("src.agents.inbound_worker.IDLE_SECONDS", 0)

    with pytest.raises(asyncio.CancelledError):
        await run_inbound_worker()
    assert calls == 2
