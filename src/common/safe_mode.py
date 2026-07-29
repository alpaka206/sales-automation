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

Once live, ``LIVE_HUBSPOT_WRITES`` and ``LIVE_SHEETS_WRITES`` (both default true)
turn the two destinations on and off independently, so one can go live before the
other. They are strictly SUBORDINATE: neither can permit a write while
``LIVE_EXTERNAL_WRITES`` is false, so the master remains the one thing to check.
"""

from __future__ import annotations

import logging

from .config import settings

logger = logging.getLogger(__name__)

# Hard-coded pre-launch recipient. All outbound email is force-routed here while
# external writes are disabled, so a customer can never be emailed during testing
# — this holds even if SEND_OVERRIDE_EMAIL is empty/cleared.
PRELAUNCH_TEST_RECIPIENT = "ronald@estsoft.com"

# --------------------------------------------------------------------------- #
# TEMPORARY HARD KILL SWITCH — no email leaves this process at all.
#
# While this is False, NOTHING is emailed by any path: not a customer, and not even
# PRELAUNCH_TEST_RECIPIENT. It sits below the send-override reroute, so it also
# catches callers that bypass senders.send().
#
# It is deliberately a module constant and NOT an env var. Env is exactly what we
# could not trust: LIVE_EXTERNAL_WRITES / INBOUND_AUTO_ACK_ENABLED live in a Render
# dashboard nobody can audit from here, and scripts/render_env_sync.py can overwrite
# the whole set from a local .env. A constant cannot be flipped by deployment config.
#
# TO RESUME SENDING: set this back to True. That is the entire switch.
# --------------------------------------------------------------------------- #
EMAIL_SENDING_ENABLED = False


def email_sending_enabled() -> bool:
    """False while the operator's temporary no-send switch is engaged.

    Read through this function (not the constant) so tests and the eventual
    re-enable only have one place to touch.
    """
    return bool(EMAIL_SENDING_ENABLED)


class ExternalWriteBlocked(RuntimeError):
    """Raised when an external write (HubSpot/Sheets) is attempted in safe mode."""


def live_external_writes() -> bool:
    """True only when the operator has explicitly enabled real external writes.

    The MASTER switch. Everything below is subordinate to it: while this is false no
    per-channel setting can let a write through, so the 대전제 stays a single
    unambiguous opt-in.
    """
    return bool(settings.LIVE_EXTERNAL_WRITES)


def live_hubspot_writes() -> bool:
    """True when HubSpot writes specifically are allowed.

    Lets ticket-stage moves, contact updates and timeline emails be turned off on
    their own while the Sheet keeps syncing — useful when HubSpot is mid-cleanup and
    a stray stage write would fight whoever is reorganising the pipeline.
    """
    return live_external_writes() and bool(settings.LIVE_HUBSPOT_WRITES)


def live_sheets_writes() -> bool:
    """True when Google Sheets writes specifically are allowed.

    The mirror image: go live on HubSpot while the workbook stays read-only, e.g.
    before the sales team has agreed the sheet is safe to be written by a machine.
    """
    return live_external_writes() and bool(settings.LIVE_SHEETS_WRITES)


def safe_mode() -> bool:
    """True while the pre-launch safety guard is active (the default)."""
    return not live_external_writes()


# Channel name (the part before ":" in an action label) -> its gate. An unrecognised
# channel falls back to the master switch, so a new write path is blocked by default
# in safe mode even if someone forgets to register it here.
_CHANNEL_GATES = {
    "hubspot": (live_hubspot_writes, "LIVE_HUBSPOT_WRITES"),
    "sheets": (live_sheets_writes, "LIVE_SHEETS_WRITES"),
}


def guard_external_write(action: str) -> None:
    """Hard block: refuse an external write unless its channel is live.

    ``action`` is a short label ("hubspot:update_ticket_stage", ...). The part before
    the colon selects the channel gate; the whole label is used for logging. Callers
    wrap this where a raised block should be swallowed gracefully (the web
    pipeline-move action, post-send bookkeeping, etc.).
    """
    gate, channel_flag = _CHANNEL_GATES.get(
        action.split(":", 1)[0], (live_external_writes, "")
    )
    if gate():
        return
    if safe_mode():
        remedy = "set LIVE_EXTERNAL_WRITES=true to go live"
    else:
        remedy = f"{channel_flag} is false"
    logger.warning("SAFE MODE: blocked external write '%s' — %s.", action, remedy)
    raise ExternalWriteBlocked(f"refused external write: {action} ({remedy})")


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
