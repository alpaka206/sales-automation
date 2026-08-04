"""Not re-sending JSON the console already has.

The review list polls every 15s, the board every 30, and every write invalidates every
open tab — so the same payload went out again and again to clients already holding an
identical copy.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import app


def test_a_poll_that_finds_nothing_new_transfers_nothing():
    """The review list polls every 15s and every write invalidates every open tab, so the
    same JSON was going out again and again to clients that already held it."""
    with TestClient(app) as client:
        first = client.get("/api/ui/messages")
        assert first.status_code == 200
        etag = first.headers["etag"]
        assert first.headers["cache-control"] == "no-cache"

        again = client.get("/api/ui/messages", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.content == b""


def test_the_clock_in_the_payload_does_not_defeat_the_cache():
    """`now` rides in the list so every row is dated against one instant. At microsecond
    precision it also changed the body on every request, which made the ETag useless for
    exactly the screens that poll — 304 could never fire.
    """
    from src.api.routes.messages import list_now

    assert list_now().second == 0
    assert list_now().microsecond == 0

    with TestClient(app) as client:
        first = client.get("/api/ui/dashboard")
        second = client.get("/api/ui/dashboard")
    assert first.headers["etag"] == second.headers["etag"]


def test_the_event_stream_is_never_buffered_for_an_etag():
    """Hashing a response body means reading it to the end. The SSE stream has no end."""
    import inspect

    from src.api import main

    source = inspect.getsource(main.conditional_get_middleware)
    assert '"/api/ui/events"' in source
