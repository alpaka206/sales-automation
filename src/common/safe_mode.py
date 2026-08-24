"""External-write and outbound-email safety controls.

The Render production blueprint enables live external writes and real-recipient SMTP.
The in-code settings remain fail-safe for other deployments: disabling
``LIVE_EXTERNAL_WRITES`` blocks CRM/Sheet writes and reroutes email, while the two module
constants provide an emergency hard stop and forced test-recipient mode.
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
# EMAIL: production delivery is ON.
#
# 2026-08-24, the operator's decision: a human-approved draft is delivered to the
# customer. Immediate inbound acknowledgements were removed separately; enabling this
# switch cannot recreate them because no auto-ack message is produced or claimable.
#
# These are code-level emergency controls, intentionally separate from deployment env.
# ``False`` for EMAIL_SENDING_ENABLED stops SMTP at the lowest chokepoint. ``True`` for
# FORCE_TEST_RECIPIENT reroutes every message to PRELAUNCH_TEST_RECIPIENT. Production is
# the inverse: SMTP enabled and no forced recipient.
# --------------------------------------------------------------------------- #
EMAIL_SENDING_ENABLED = True
FORCE_TEST_RECIPIENT = False


def email_sending_enabled() -> bool:
    """False while the operator's no-send switch is engaged (nothing is emailed at all).

    Read through this function (not the constant) so tests and any future change only
    have one place to touch.
    """
    return bool(EMAIL_SENDING_ENABLED)


def force_test_recipient() -> bool:
    """True while every outbound email is pinned to the single test address."""
    return bool(FORCE_TEST_RECIPIENT)


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

    - ``FORCE_TEST_RECIPIENT`` (the current posture): always ``ronald@estsoft.com``,
      whatever the master switch says and whatever ``SEND_OVERRIDE_EMAIL`` holds. The
      env value is deliberately ignored: "only ronald@estsoft.com" is a code guarantee,
      and reading an address from a deployment dashboard would not be one.
    - Safe mode (pre-launch): always non-empty, for the same reason.
    - Neither engaged: honors SEND_OVERRIDE_EMAIL as-is ('' = real customer delivery).
    """
    explicit = settings.SEND_OVERRIDE_EMAIL.strip()
    if force_test_recipient():
        return PRELAUNCH_TEST_RECIPIENT
    if live_external_writes():
        return explicit
    return explicit or PRELAUNCH_TEST_RECIPIENT
