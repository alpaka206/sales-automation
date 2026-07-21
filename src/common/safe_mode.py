"""Global pre-launch safety switch — the operator's hard "대전제".

Until the service goes live, NOTHING may touch the outside world in a way that
could harm real data or reach a real customer:

- **HubSpot writes are hard-blocked.** No ticket-stage move, contact update,
  inbound-status write, or timeline email is ever sent. The real HubSpot account
  cannot change no matter what testing / migration happens.
- **Google Sheets writes are disabled**, so test rows never pollute the shared
  sales workbook.
- **Every outbound email is force-routed to a single test recipient**
  (``ronald@estsoft.com`` unless ``SEND_OVERRIDE_EMAIL`` overrides it), so no
  customer can ever be emailed — even if the env override is later cleared.

Reads stay ON (HubSpot GET, Gemini, homepage fetch) so the whole pipeline can be
exercised against real inbound data with zero external side effects.

The SAFE state is the DEFAULT. Going live is a deliberate, single opt-in:
set ``LIVE_EXTERNAL_WRITES=true`` (and clear ``SEND_OVERRIDE_EMAIL`` for real
delivery). If the flag is unset, misspelled, or config fails to load, the system
stays SAFE. This is enforced in deterministic code, not prompts — every external
write/send chokepoint routes through this module; ``tests/test_safe_mode.py``
pins the guaranteed behavior.
"""

from __future__ import annotations

import logging

from .config import settings

logger = logging.getLogger(__name__)

# Hard-coded pre-launch recipient. All outbound email is force-routed here while
# external writes are disabled, so a customer can never be emailed during testing
# — this holds even if SEND_OVERRIDE_EMAIL is empty/cleared.
PRELAUNCH_TEST_RECIPIENT = "ronald@estsoft.com"


class ExternalWriteBlocked(RuntimeError):
    """Raised when an external write (HubSpot/Sheets) is attempted in safe mode."""


def live_external_writes() -> bool:
    """True only when the operator has explicitly enabled real external writes."""
    return bool(settings.LIVE_EXTERNAL_WRITES)


def safe_mode() -> bool:
    """True while the pre-launch safety guard is active (the default)."""
    return not live_external_writes()


def guard_external_write(action: str) -> None:
    """Hard block: refuse any external write while in safe mode.

    ``action`` is a short label ("hubspot:update_ticket_stage", ...) used only for
    logging. Callers wrap this where a raised block should be swallowed gracefully
    (the web pipeline-move action, post-send bookkeeping, etc.).
    """
    if safe_mode():
        logger.warning(
            "SAFE MODE: blocked external write '%s' — set LIVE_EXTERNAL_WRITES=true to go live.",
            action,
        )
        raise ExternalWriteBlocked(f"safe mode active — refused external write: {action}")


def resolve_send_override() -> str:
    """The address ALL outbound email must be rerouted to, or '' for real delivery.

    - Safe mode (pre-launch): always non-empty — SEND_OVERRIDE_EMAIL if set, else
      the built-in ``ronald@estsoft.com``. Guarantees no customer email even if the
      env override is cleared.
    - Live mode: honors SEND_OVERRIDE_EMAIL as-is ('' = real delivery to customers).
    """
    explicit = settings.SEND_OVERRIDE_EMAIL.strip()
    if live_external_writes():
        return explicit
    return explicit or PRELAUNCH_TEST_RECIPIENT
