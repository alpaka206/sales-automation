"""Single source of truth for the Business Plan quote-calculator tier policy.

The customer-facing quote calculator (served at ``/tools/quote-calculator``) used
to carry its pricing table hardcoded in client-side JavaScript. That made the
business-critical numbers invisible to the backend and impossible to unit-test.
This module is now the authoritative policy: the FastAPI route injects it into
the calculator template, and ``tests/test_quote_tiers.py`` guards its internal
consistency so a bad edit can never silently reach a customer quote.

Only *base facts* are typed by hand (cycle length, price, credits, ops limits).
Every number a customer could act on is DERIVED, so it cannot drift:

    dubbing_min = credits / CREDITS_PER_MIN          # 60 credits == 1 dubbing min
    monthly_cap = dubbing_min / cycle_months         # capacity, monthly basis
    per_min     = usd / dubbing_min                  # true base $/min (2dp on screen)
    monthly_usd = round(usd / cycle_months)          # display only
    krw         = usd * KRW_PER_USD                  # display only

``cm`` (contribution margin) is internal margin data and is deliberately never
included in the client payload, even though the route is auth-gated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

CREDITS_PER_MIN = 60  # 60 credits = 1 dubbing minute (all tiers)
LIP_MULT = 3  # lip-dubbing burns 3x credits (=> 180 credits / finished min)
KRW_PER_USD = 1500  # fixed FX used only for the ₩ display on the tier cards


@dataclass(frozen=True)
class Tier:
    """One Business Plan tier. Base facts only — derived values are properties."""

    id: int
    cycle_months: int
    usd: int
    credits: int
    queue: int
    concurrent: int
    max_len: int
    cm: float  # internal contribution-margin %; operator-only, never sent to client

    @property
    def dubbing_min(self) -> int:
        # credits are always a whole number of dubbing minutes (validated below).
        return self.credits // CREDITS_PER_MIN

    @property
    def monthly_cap(self) -> float:
        """Capacity on a monthly basis (exact; the UI rounds for display+compare)."""
        return self.dubbing_min / self.cycle_months

    @property
    def per_min(self) -> float:
        """True base list rate $/min. The UI shows this at 2dp (e.g. 1.82)."""
        return self.usd / self.dubbing_min

    @property
    def monthly_usd(self) -> int:
        return round(self.usd / self.cycle_months)

    @property
    def krw(self) -> int:
        return self.usd * KRW_PER_USD


# The authoritative policy. Base facts only; everything else is derived above.
TIERS: tuple[Tier, ...] = (
    Tier(id=1, cycle_months=3, usd=1000, credits=30000, queue=6, concurrent=3, max_len=60, cm=65.0),
    Tier(id=2, cycle_months=6, usd=4000, credits=132000, queue=7, concurrent=3, max_len=60, cm=61.5),
    Tier(id=3, cycle_months=12, usd=10000, credits=336000, queue=9, concurrent=3, max_len=60, cm=60.8),
)


def tier_to_client_dict(t: Tier) -> dict:
    """Shape a tier the way the calculator JS expects (camelCase, derived fields).

    Excludes ``cm`` — internal margin must never reach the browser.
    """
    return {
        "id": t.id,
        "key": f"t{t.id}",
        "cycleMonths": t.cycle_months,
        "usd": t.usd,
        "krw": t.krw,
        "monthlyUsd": t.monthly_usd,
        "credits": t.credits,
        "dubbingMin": t.dubbing_min,
        "monthlyCap": t.monthly_cap,  # exact; JS rounds for compare + display
        "perMin": t.per_min,  # exact; JS renders at 2dp
        "queue": t.queue,
        "concurrent": t.concurrent,
        "maxLen": t.max_len,
    }


def policy_client_json() -> str:
    """The JSON blob the template injects: the two policy constants + all tiers."""
    return json.dumps(
        {
            "creditsPerMin": CREDITS_PER_MIN,
            "lipMult": LIP_MULT,
            "tiers": [tier_to_client_dict(t) for t in TIERS],
        },
        ensure_ascii=False,
    )


def validate_policy() -> None:
    """Fail loudly if the derived tier data is internally inconsistent.

    Run at import (below) and exercised by the test-suite so a bad edit to the
    base facts — credits not a multiple of 60, non-ascending capacity, etc. — is
    caught long before it can produce a wrong customer quote.
    """
    for t in TIERS:
        assert (
            t.credits % CREDITS_PER_MIN == 0
        ), f"T{t.id}: credits ({t.credits}) is not a whole number of dubbing minutes"
        assert t.usd > 0 and t.credits > 0 and t.cycle_months > 0, f"T{t.id}: non-positive base fact"

    caps = [t.monthly_cap for t in TIERS]
    assert caps == sorted(caps) and len(set(caps)) == len(caps), (
        "tier monthly caps must be strictly ascending so 'smallest tier that "
        "covers usage' returns the cheapest valid tier"
    )
    dubs = [t.dubbing_min for t in TIERS]
    assert dubs == sorted(dubs) and len(set(dubs)) == len(dubs), (
        "tier total capacity must be strictly ascending"
    )


validate_policy()
