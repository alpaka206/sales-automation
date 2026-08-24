"""External-write and outbound-email safety controls.

The Render production blueprint enables live external writes and customer delivery.
Other deployments fail closed: disabling ``LIVE_EXTERNAL_WRITES`` blocks CRM/Sheet
writes and email delivery instead of silently changing the recipient.
"""

from __future__ import annotations

import logging

from .config import settings

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# EMAIL: production delivery is ON.
#
# 2026-08-24, the operator's decision: a human-approved draft is delivered to the
# customer. Immediate inbound acknowledgements were removed separately; enabling this
# switch cannot recreate them because no auto-ack message is produced or claimable.
#
# This code-level emergency control is intentionally separate from deployment env.
# ``False`` stops delivery at the lowest chokepoint.
# --------------------------------------------------------------------------- #
EMAIL_SENDING_ENABLED = True


def email_sending_enabled() -> bool:
    """False while the operator's no-send switch is engaged (nothing is emailed at all).

    Read through this function (not the constant) so tests and any future change only
    have one place to touch.
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


def email_delivery_enabled() -> bool:
    """True only when both the emergency switch and live mode allow delivery."""
    return email_sending_enabled() and live_external_writes()
