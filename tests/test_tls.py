"""OS trust store injection — see src/common/tls.py.

The office network re-signs HTTPS with a private root, so without this every outbound
call (Sheets, HubSpot, Vertex) dies with CERTIFICATE_VERIFY_FAILED while the same URL
opens fine in a browser. It is easy to delete by accident because nothing fails on a
normal network or on Render.
"""

from __future__ import annotations

import ssl

from src.common import tls


def test_injection_is_active_and_idempotent():
    assert tls.use_os_trust_store() is True
    assert tls.use_os_trust_store() is True


def test_the_app_entrypoint_injects_before_serving():
    """Importing the app must be enough — no caller has to remember to opt in."""
    import src.api.main  # noqa: F401

    assert tls._injected is True


def test_sheets_healthcheck_reports_a_missing_connection_as_fail(monkeypatch):
    """With no UI showing the connection, this check is the only signal it broke."""
    from src.common import healthcheck
    from src.integrations import google_sheets

    monkeypatch.setattr(google_sheets, "is_configured", lambda: False)

    result = healthcheck._check_google_sheets()

    assert result.status == "FAIL"
    assert "GOOGLE_SHEETS_OAUTH_REFRESH_TOKEN" in result.detail


def test_sheets_healthcheck_names_a_dead_refresh_token(monkeypatch):
    """invalid_grant needs its own message: retrying never fixes it, only reconnecting.

    Google expires refresh tokens after 7 days while the OAuth app sits in "Testing",
    which is the most likely way this connection dies on its own.
    """
    from src.common import healthcheck
    from src.integrations import google_sheets

    monkeypatch.setattr(google_sheets, "is_configured", lambda: True)
    monkeypatch.setattr(
        google_sheets,
        "_build_service",
        lambda: (_ for _ in ()).throw(RuntimeError("('invalid_grant: Bad Request', {})")),
    )

    result = healthcheck._check_google_sheets()

    assert result.status == "FAIL"
    assert "connect_google_sheets.py" in result.detail


def test_verification_stays_on():
    """This is a trust-store swap, not a bypass. CERT_NONE here would be a silent
    downgrade of every outbound call in the process."""
    tls.use_os_trust_store()
    context = ssl.create_default_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
