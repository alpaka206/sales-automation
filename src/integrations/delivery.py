"""Transport-neutral outbound delivery errors used by the send worker."""

from __future__ import annotations


class DeliveryPermanentError(RuntimeError):
    """The request is invalid or misconfigured; retrying unchanged will not help."""


class DeliveryTransientError(RuntimeError):
    """The provider definitely rejected the attempt temporarily; retrying is safe."""


class DeliveryUnknown(RuntimeError):
    """The provider may have accepted the message; automatic retry could duplicate it."""


class SendingDisabled(DeliveryPermanentError):
    """Live email delivery is disabled by the application safety controls."""
