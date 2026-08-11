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

Email is a separate axis from this master switch, and it is the ONE thing still held
back: ``EMAIL_SENDING_ENABLED = False`` means nothing is emailed at all. HubSpot and
the Sheet are live; only delivery is off. See the EMAIL block below.

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
# EMAIL: sending is OFF. Everything else is live.
#
# 2026-08-04, the operator's decision: "메일 발송되는 것만 막고 나머지는 모두 다 되도록."
# Ticket stages, contact updates and the sales workbook all write for real; no message
# leaves this process, not even to the test address.
#
# 2026-07-30 (superseded): sending was ON and pinned to ronald@estsoft.com.
#
# Both switches are module constants and NOT env vars. Env is exactly what we could
# not trust: LIVE_EXTERNAL_WRITES / SEND_OVERRIDE_EMAIL live in a Render dashboard
# nobody can audit from here, and scripts/render_env_sync.py can overwrite the whole
# set from a local .env. A constant cannot be flipped by deployment config.
#
#   EMAIL_SENDING_ENABLED  False = nothing is emailed at all, not even to the test
#                          address (the lowest chokepoint, below the reroute, so it
#                          catches callers that bypass senders.send()). True = SMTP
#                          delivery happens.
#                          블록은 **실패가 아닙니다.** 운영자가 검토 완료·발송을 누르면
#                          메일만 안 나가고 나머지는 전부 그대로 일어납니다 — 단계가
#                          답변 발송으로 옮겨지고 HubSpot 티켓과 워크북도 따라갑니다
#                          (send_worker._send_one 이 SMTPSendingDisabled 를 잡습니다).
#                          행은 `sent` 가 아니라 `test_sent` 로 남습니다: 고객에게 정말
#                          간 것만 `sent` 여야 합니다. 고객 타임라인에 "답장했다" 기록도
#                          남지 않습니다 — senders.send() 가 SMTP **뒤에** 쓰기 때문에
#                          거기까지 가지 못합니다.
#   FORCE_TEST_RECIPIENT   True = every message goes to PRELAUNCH_TEST_RECIPIENT and
#                          nowhere else. SEND_OVERRIDE_EMAIL is IGNORED while this is
#                          on — the operator's instruction is "ronald@estsoft.com 으로만",
#                          and honouring an env address here would mean a deployment
#                          dashboard could still redirect mail somewhere unreviewed.
#                          This holds EVEN IN LIVE MODE, which is the point.
#
# REACHING REAL CUSTOMERS is therefore a deliberate two-place act: set
# FORCE_TEST_RECIPIENT = False here AND clear SEND_OVERRIDE_EMAIL. Nothing in a
# deployment dashboard can do the first one.
# --------------------------------------------------------------------------- #
EMAIL_SENDING_ENABLED = False
# Kept ON underneath the no-send switch, deliberately. It is the second layer: if
# EMAIL_SENDING_ENABLED is ever flipped back without thinking, delivery resumes pinned to
# one address rather than reaching customers. Two mistakes are needed, not one.
FORCE_TEST_RECIPIENT = True


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
