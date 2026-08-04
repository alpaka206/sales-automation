"""The JSON the React screens read, and the live-update stream.

Two rules this file exists to hold. First, ``/api/ui`` is a second door onto the same
screens — never a way around what guards them. Second, a change made on one screen has to
reach the others, which React state cannot do on its own: only the server knows.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.web.routes import ui_api


def test_the_json_screens_are_browser_paths_not_token_api_routes():
    """They carry the operator's session cookie like every other screen. Left out of
    WEB_UI_PREFIXES the auth middleware reads them as JSON API routes and demands an
    internal token, and the whole console answers 401."""
    from src.api.security import is_web_ui_path

    assert is_web_ui_path("/api/ui/dashboard")
    assert is_web_ui_path("/app")
    assert is_web_ui_path("/app/messages/12")
    # …and a real API route still is one.
    assert not is_web_ui_path("/api/inbound/run")


def test_the_log_json_answers_exactly_as_its_screen_does():
    """Same gate, same answer — asserted against the page rather than a fixed code.

    A JSON copy of a screen must not be a way around that screen's gate, and must not
    refuse what the screen allows. Pinning the codes together is what catches either
    drift; this app has two different admin checks, so picking the wrong one is easy.
    """
    with TestClient(app) as client:
        assert client.get("/api/ui/logs").status_code == client.get("/logs").status_code


def test_an_unknown_message_is_a_404_not_a_blank_screen():
    with TestClient(app) as client:
        assert client.get("/api/ui/messages/999999").status_code == 404
        assert client.get("/api/ui/customers/999999").status_code == 404
        assert client.get("/api/ui/pipeline/nonsense/cards").status_code == 404


def test_every_screen_the_sidebar_offers_has_working_json():
    """The cutover's other half: a screen with no data endpoint is a blank page. Each of
    these 500'd or 404'd would be a menu entry that opens nothing."""
    with TestClient(app) as client:
        for path in (
            "/api/ui/dashboard",
            "/api/ui/messages",
            "/api/ui/customers",
            "/api/ui/companies/acme.com",
            "/api/ui/email-templates",
            "/api/ui/policy-docs",
            "/api/ui/operations",
            "/api/ui/logs",
            "/api/ui/recovery",
        ):
            assert client.get(path).status_code == 200, path


def test_a_personal_domain_is_never_grouped_as_one_company():
    """gmail/naver addresses share a domain but not a customer — grouping them would
    show one customer's conversations to an unrelated one."""
    with TestClient(app) as client:
        payload = client.get("/api/ui/companies/gmail.com").json()
    assert payload["personal_domain"] is True
    assert payload["conversations"] == []



# ---- live updates ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_write_reaches_every_open_console():
    """publish() is what a stage move calls; each subscriber is one open tab."""
    first: asyncio.Queue[str] = asyncio.Queue()
    second: asyncio.Queue[str] = asyncio.Queue()
    ui_api._subscribers.update({first, second})
    try:
        ui_api.publish("pipeline")
        assert first.get_nowait() == "pipeline"
        assert second.get_nowait() == "pipeline"
    finally:
        ui_api._subscribers.difference_update({first, second})


@pytest.mark.asyncio
async def test_a_stalled_tab_cannot_block_a_write():
    """A browser that stopped reading fills its queue. Dropping it is right; blocking the
    operator's save because a dead tab is full is not."""
    full: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
    full.put_nowait("earlier")
    ui_api._subscribers.add(full)
    try:
        ui_api.publish("pipeline")  # must not raise, must not hang
        assert full not in ui_api._subscribers
    finally:
        ui_api._subscribers.discard(full)


def test_broadcasting_never_breaks_the_write_it_follows(monkeypatch):
    """_announce is fire-and-forget: a stage move must succeed even if nothing is
    listening or the broadcast itself explodes."""
    from src.api.web.routes import customer_ops

    def boom(_topic: str) -> None:
        raise RuntimeError("no listeners")

    monkeypatch.setattr(ui_api, "publish", boom)
    customer_ops._announce("pipeline")  # swallowed, by design
