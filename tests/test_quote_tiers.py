"""Guards the quote-calculator tier policy (src/common/quote_tiers.py).

The calculator's pricing used to live in client-side JS where nothing could
check it. Now it is Python and these tests enforce the invariants the UI relies
on: derived fields stay consistent, the internal margin never leaks to the
client, and entering a tier's *displayed* capacity lands on that tier (the
boundary-rounding the JS applies).
"""

from __future__ import annotations


from src.common.quote_tiers import (
    CREDITS_PER_MIN,
    LIP_MULT,
    TIERS,
    policy_client,
    tier_to_client_dict,
    validate_policy,
)


def test_policy_self_consistent():
    validate_policy()  # raises AssertionError on any drift


def test_credits_map_to_whole_dubbing_minutes():
    for t in TIERS:
        assert t.credits % CREDITS_PER_MIN == 0
        assert t.dubbing_min == t.credits // CREDITS_PER_MIN


def test_per_min_is_true_base_rate():
    # perMin must equal usd/dubbing_min exactly (was hardcoded & rounded-up before,
    # which manufactured a phantom 1pt discount on small service-credit grants).
    for t in TIERS:
        assert abs(t.per_min - t.usd / t.dubbing_min) < 1e-9


def test_caps_strictly_ascending():
    caps = [t.monthly_cap for t in TIERS]
    dubs = [t.dubbing_min for t in TIERS]
    assert caps == sorted(caps) and len(set(caps)) == len(caps)
    assert dubs == sorted(dubs) and len(set(dubs)) == len(dubs)


def test_client_payload_excludes_internal_margin():
    payload = policy_client()
    assert payload["creditsPerMin"] == CREDITS_PER_MIN
    assert payload["lipMult"] == LIP_MULT
    assert len(payload["tiers"]) == len(TIERS)
    for tc in payload["tiers"]:
        assert "cm" not in tc  # contribution margin must never reach the browser


def test_client_dict_has_fields_the_js_reads():
    expected = {
        "id", "key", "cycleMonths", "usd", "krw", "monthlyUsd",
        "credits", "dubbingMin", "monthlyCap", "perMin",
        "queue", "concurrent", "maxLen",
    }
    for t in TIERS:
        assert set(tier_to_client_dict(t)) == expected


def test_displayed_cap_lands_on_that_tier():
    # The card/footer print round(monthly_cap) (167/367/467). Entering exactly
    # that number must recommend THIS tier, not get bumped up one — this is the
    # invariant the JS boundary-rounding (Math.round(monthlyCap)) enforces.
    for t in TIERS:
        shown = round(t.monthly_cap)
        rec = next((x for x in TIERS if round(x.monthly_cap) >= shown), None)
        assert rec is not None and rec.id == t.id


def test_krw_is_uniform_exchange_rate():
    rates = {t.krw / t.usd for t in TIERS}
    assert len(rates) == 1  # a single, consistent KRW/USD across all tiers
