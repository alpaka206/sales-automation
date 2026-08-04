/** The 견적 계산기 arithmetic, lifted out of the screen so the numbers can be checked.
 *
 * This is a customer-facing quote: a rounding difference here is a wrong price in a
 * sales conversation, not a cosmetic bug. It is a deliberate line-for-line transcription
 * of the calculator's previous `recalc()` — the tier table itself is NOT here, it comes
 * from src/common/quote_tiers.py via /api/ui/quote-policy and is unit-tested there.
 */

export type Tier = {
  id: number;
  key: string;
  cycleMonths: number;
  usd: number;
  krw: number;
  monthlyUsd: number;
  credits: number;
  dubbingMin: number;
  monthlyCap: number;
  perMin: number;
  queue: number | string;
  concurrent: number | string;
  maxLen: number | string;
};

export type Policy = { creditsPerMin: number; lipMult: number; tiers: Tier[] };

export type Basis = "monthly" | "total";

export type Inputs = {
  per: number;          // NaN when the field is empty or unparseable
  langs: number;
  svc: number;
  lipMult: number;      // 1 = dubbing only, policy.lipMult = lip dubbing
  basis: Basis;
  selectedTier: number | null;   // null = follow the recommendation
};

export type Quote = {
  badPer: boolean;
  badLang: boolean;
  badSvc: boolean;
  usage: number;
  isTotal: boolean;
  recommended: Tier | null;
  overflow: boolean;
  belowT1: boolean;
  cap: number;             // the recommended tier's capacity on the active basis
  util: number;            // 0–100
  cycleTotal: number;      // monthly basis: usage over the cycle. total basis: the tier's monthly cap
  proBannerCap: number;    // Tier 1's capacity on the active basis
  extraMin: number;        // finished minutes to top up (overflow only)
  extraCr: number;
  showOverBanner: boolean;
  active: Tier;            // the tier the service credit applies to
  baseCr: number;
  finalCr: number;
  finalMin: number;
  lipFinalMin: number;
  effPrice: number;
  pct: number;
  cheaper: boolean;
};

export function computeQuote(policy: Policy, input: Inputs): Quote {
  const { tiers, creditsPerMin, lipMult: LIP_MULT } = policy;

  const badPer = Number.isNaN(input.per) || input.per < 0;
  const badLang = Number.isNaN(input.langs) || input.langs < 1;
  const badSvc = Number.isNaN(input.svc) || input.svc < 0;
  const per = badPer ? 0 : input.per;
  // 0, not 1: an invalid language count must zero the usage rather than quietly quote
  // for a single language the operator never entered.
  const langs = badLang ? 0 : input.langs;
  const svc = badSvc ? 0 : input.svc;

  const usage = per * langs * input.lipMult;
  const isTotal = input.basis === "total";
  // Capacity a tier absorbs on the active basis, rounded to the SAME number the UI
  // advertises — so entering a tier's displayed cap (167/367/467) lands on that tier
  // instead of being bumped up by the sub-minute repeating decimal.
  const capOf = (t: Tier) => (isTotal ? t.dubbingMin : Math.round(t.monthlyCap));

  let recommended: Tier | null = null;
  let overflow = false;
  let belowT1 = false;
  if (usage > 0 && langs >= 1) {
    recommended = tiers.find((t) => capOf(t) >= usage) ?? null;
    if (!recommended) {
      recommended = tiers[2];
      overflow = true;
    }
    if (usage < capOf(tiers[0])) belowT1 = true;
  }

  const cap = recommended ? capOf(recommended) : 0;
  const util = recommended ? Math.min(100, (usage / cap) * 100) : 0;
  const cycleTotal = recommended
    ? isTotal
      ? Math.round(recommended.monthlyCap)
      : Math.round(usage * recommended.cycleMonths)
    : 0;

  // Shortfall in credit-equivalent minutes over the cycle (usage is already lip-inflated),
  // then shown as FINISHED minutes: lip burns 3× credits, so finished = credit-equiv ÷ lipMult.
  const extraEqMin =
    recommended && overflow
      ? isTotal
        ? Math.ceil(usage - recommended.dubbingMin)
        : Math.ceil((usage - Math.round(recommended.monthlyCap)) * recommended.cycleMonths)
      : 0;

  const active = tiers.find((t) => t.id === input.selectedTier) ?? recommended ?? tiers[0];
  const baseCr = active.credits;
  const finalCr = baseCr + svc;
  const finalMin = finalCr / creditsPerMin;
  const lipFinalMin = finalCr / (creditsPerMin * LIP_MULT);
  const effPrice = finalMin > 0 ? active.usd / finalMin : 0;
  const pct = Math.round((1 - effPrice / active.perMin) * 100);
  // A genuine concession = any positive bonus lowers the effective price below list.
  const cheaper = svc > 0 && effPrice < active.perMin - 1e-9;

  return {
    badPer, badLang, badSvc,
    usage, isTotal,
    recommended, overflow, belowT1, cap, util, cycleTotal,
    proBannerCap: Math.round(isTotal ? tiers[0].dubbingMin : tiers[0].monthlyCap),
    extraMin: Math.round(extraEqMin / input.lipMult),
    extraCr: extraEqMin * creditsPerMin,
    showOverBanner: overflow && extraEqMin > 0,
    active, baseCr, finalCr, finalMin, lipFinalMin, effPrice, pct, cheaper,
  };
}
