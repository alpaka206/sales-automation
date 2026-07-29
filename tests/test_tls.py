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


def test_verification_stays_on():
    """This is a trust-store swap, not a bypass. CERT_NONE here would be a silent
    downgrade of every outbound call in the process."""
    tls.use_os_trust_store()
    context = ssl.create_default_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
