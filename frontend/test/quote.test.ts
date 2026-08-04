import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { computeQuote, type Basis, type Policy } from "../src/lib/quote";

/** The 견적 계산기 must keep quoting the price it quoted before the React port.
 *
 * quote.golden.json was not written by hand and must never be regenerated to make this
 * pass. Its rows are what the PRE-REACT calculator script actually rendered: the script
 * was extracted from the template it replaced, run against a stub DOM over every
 * combination below, and its output recorded. A failure here means today's code quotes
 * a different number than the calculator the sales team has been using — which is a
 * decision someone has to make on purpose, not a fixture to refresh.
 *
 * 1,512 combinations: every tier boundary (below Tier 1, each advertised cap exactly,
 * one minute over, far past Tier 3), the inputs that must be rejected (empty, negative,
 * non-numeric), both input bases, dubbing and lip-dubbing, and a pinned tier that is not
 * the recommended one.
 */

type Golden = {
  policy: Policy;
  inputs: string[];
  rendered: string[];
  rows: [
    string, string, string, number, string, number | null,   // per, langs, svc, lip, basis, pin
    ...string[],                                             // the rendered values
  ][];
};

// The tier table is captured alongside the rows, because a row only means anything
// against the prices it was produced from. tests/test_quote_tiers.py asserts this copy
// still matches src/common/quote_tiers.py — that is what makes a price change in Python
// fail loudly here instead of quietly leaving this table describing the old one.
const golden = JSON.parse(
  readFileSync(new URL("./quote.golden.json", import.meta.url), "utf-8"),
) as Golden;
const policy = golden.policy;

// Formatting is part of the quote: "1,200" and "1200.4" are different answers to a
// customer. Same locale rule the screen uses — pinned to the app's own toggle.
const fmt = (n: number) => Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 });
const fmt1 = (n: number) => Number(n).toLocaleString("en-US", { maximumFractionDigits: 1 });

describe("the ported calculator quotes what the original quoted", () => {
  it.each(golden.rows.map((row, index) => [index, row] as const))(
    "case %i",
    (_index, row) => {
      const [per, langs, svc, lip, basis, pin] = row;
      const [
        totalMonthly, recoName, utilUse, utilCap, scTier, kFinalCredit, kBaseCredit,
        kBonus, kMinutes, kLipMinutes, kEffPrice, kPayUsd, kPayKrw,
      ] = row.slice(6, 19) as string[];
      const [proBanner, overBanner, overNums, cheaper] = row.slice(19) as unknown as [
        boolean, boolean, number[], boolean,
      ];

      const q = computeQuote(policy, {
        per: parseFloat(per),
        langs: parseInt(langs, 10),
        svc: parseFloat(svc),
        lipMult: lip ? policy.lipMult : 1,
        basis: basis as Basis,
        selectedTier: pin,
      });
      const unit = basis === "total" ? " min" : " min/mo";

      expect(fmt1(q.usage)).toBe(totalMonthly);
      expect(q.recommended ? `Tier ${q.recommended.id}` : "—").toBe(recoName);
      expect(q.recommended ? fmt1(q.usage) + unit : "—").toBe(utilUse);
      expect(q.recommended ? fmt(q.cap) + unit : "—").toBe(utilCap);

      expect(`Tier ${q.active.id}`).toBe(scTier);
      expect(fmt(q.finalCr)).toBe(kFinalCredit);
      expect(fmt(q.baseCr)).toBe(kBaseCredit);
      expect(fmt(q.finalCr - q.baseCr)).toBe(kBonus);
      expect(fmt(q.finalMin)).toBe(kMinutes);
      expect(fmt(q.lipFinalMin)).toBe(kLipMinutes);
      expect(q.effPrice.toFixed(2)).toBe(kEffPrice);

      // The headline payment is fixed by the tier — service credit must never move it.
      expect(fmt(q.active.usd)).toBe(kPayUsd);
      expect(fmt(q.active.krw)).toBe(kPayKrw);

      expect(q.recommended !== null && q.belowT1).toBe(proBanner);
      expect(q.recommended !== null && q.showOverBanner).toBe(overBanner);
      if (overBanner) expect([q.extraMin, q.extraCr]).toEqual(overNums);
      // The discount label has to agree with the effective price printed above it.
      expect(q.cheaper).toBe(cheaper);
    },
  );
});
