"""Verify TLS against the OS trust store as well as certifi's bundle.

The ESTsoft office network runs a TLS-inspecting appliance that re-signs HTTPS with a
private root. Windows trusts that root; Python does not, because it ships its own
certifi bundle. So every outbound call from the app — Google Sheets, HubSpot, Vertex,
Slack — fails with CERTIFICATE_VERIFY_FAILED on that network while the same URL opens
fine in a browser.

``truststore`` makes Python read the same store the browser reads. Verification stays
ON; this is not a bypass, and nothing here should ever grow a ``verify=False``.

On a normal network and on Render this is a no-op: certifi already validates those
chains, and the OS store validates them too.

Call :func:`use_os_trust_store` once, as early as possible in a process — it patches
``ssl`` globally, so it must run before any client library builds its SSL context.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_injected = False


def use_os_trust_store() -> bool:
    """Route TLS verification through the OS trust store. Idempotent.

    Returns True when the patch is in effect. A missing ``truststore`` is not fatal:
    off the intercepting network certifi is enough, so the app should still start.
    """
    global _injected
    if _injected:
        return True
    try:
        import truststore
    except ImportError:
        logger.warning(
            "truststore is not installed — HTTPS will fail on a TLS-inspecting network."
        )
        return False
    truststore.inject_into_ssl()
    _injected = True
    return True
