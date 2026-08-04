import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getJSON } from "../lib/api";
import { computeQuote, type Basis, type Policy, type Tier } from "../lib/quote";
import "./quote-calculator.css";

/** 견적 계산기 — the Business Plan tier recommender.
 *
 * Was a standalone HTML document in an iframe; the maths moved to lib/quote.ts and the
 * tier table to /api/ui/quote-policy, which is the same table src/common/quote_tiers.py
 * unit-tests. Nothing about the numbers changed, only where they are computed.
 */

type Lang = "en" | "ko";

const STRINGS = {
  en: {
    badge: "Business Plan", title: "Business Plan Calculator",
    subtitle: "Enter the customer's monthly usage and we'll recommend the right tier — then layer in service credits to show the effective per-minute rate.",
    s1: "Monthly Usage", s2: "Recommended Tier", s3: "Compare Tiers",
    s3_h: "— click a tier to apply service credits to it", s4: "Service Credit (Sales)",
    l_perlang: "Monthly dubbing per language", l_perlang_h: "(finished video minutes)",
    l_langs: "Number of languages", l_langs_h: "(usage × languages)",
    l_lip: "Dubbing type", l_lip_h: "(lip-dubbing deducts 3× credits)",
    lip_off: "Dubbing only · ×1", lip_on: "Lip dubbing · ×3",
    l_basis: "Input basis", l_basis_h: "(monthly rate vs total volume)",
    basis_mo: "Monthly", basis_tot: "Total volume",
    s1_t: "Usage Volume", l_perlang_t: "Total dubbing per language", r_total_t: "Total usage", u_min_t: "min",
    r_cycle_t: "This tier's monthly capacity:",
    u_min: "min / mo", u_min2: "min / mo", u_min3: "min",
    r_total: "Total monthly usage", r_cycle: "Over the recommended billing cycle:",
    lip_note: "Lip-dubbing burns 3× credits, so the total above is shown in credit-equivalent minutes (= finished minutes × 3) for tier sizing.",
    reco_best: "Best fit", m_use: "Your usage", m_cap: "Tier capacity",
    sc_applying: "Applying to", sc_following: "· following recommendation",
    l_sc: "Service credit to grant", l_sc_h: "(bonus, on top of plan credit)", u_cr: "credits",
    sc_note: "Payment amount is fixed by the tier and never changes — extra credits only improve the effective per-minute rate the customer sees.",
    k_final: "Final granted credit", k_base: "Base", k_bonus: "bonus",
    k_minutes: "Final dubbing minutes", k_rate: "60 credits / min",
    k_lipminutes: "Final lip-sync dubbing minutes", k_liprate: "180 credits / min",
    k_eff: "Effective price / min", k_pay: "Payment (fixed)", k_locked: "LOCKED",
    e_pos: "Enter a number of 0 or greater.", e_lang: "At least 1 language is required.",
    f1: "<b>How it works:</b> Tier capacity is computed on a monthly basis (cycle dubbing minutes ÷ cycle length): Tier 1 ≈ 167 min/mo, Tier 2 ≈ 367 min/mo, Tier 3 ≈ 467 min/mo. The smallest tier that covers your total monthly usage is recommended.",
    f2: "<b>Below Tier 1?</b> Usage under ~167 min/mo can be served on the Pro subscription. <b>Above Tier 3?</b> Stay on Tier 3 and top up with Get Credit (add-on purchase).",
    f3: "<b>Credit logic:</b> 60 credits = 1 dubbing minute across all tiers. Service credits are a sales lever — the headline payment is locked per tier; only the effective per-minute rate moves.",
    cycle: "cycle", credits: "Credits", dubbing: "Dubbing", monthlyCap: "Monthly cap", perMin: "Per-min",
    monthlyEq: (min: string) => `~${min} min/mo`,
    cycleVal: (m: number, min: string) => `${min} min (${m} mo)`,
    reasonFit: (t: Tier, tot: boolean) => tot
      ? `Covers the customer's total dubbing volume with room to spare (${t.cycleMonths}-month cycle).`
      : `Covers your total monthly usage with room to spare on a ${t.cycleMonths}-month cycle.`,
    reasonExact: (t: Tier, tot: boolean) => tot
      ? `The smallest tier that fully covers the total dubbing volume (${t.cycleMonths}-month cycle).`
      : `The smallest tier that fully covers your monthly usage (${t.cycleMonths}-month cycle).`,
    reasonOver: "Usage exceeds the largest self-serve tier — see the top-up note below.",
    reasonBelowT1: "Below Tier 1's minimum — the Pro subscription is usually the better fit (see note below).",
    reasonEmpty: "Enter a monthly usage to see a recommendation.",
    proBanner: (cap: string, tot: boolean) => tot
      ? `<b>Below Tier 1.</b> The total volume is under Tier 1's allotment (${cap} min). The Pro subscription may be more cost-effective — Tier 1 is the Business plan's entry point. Recommend Pro unless the customer wants Business-grade queue/concurrency.`
      : `<b>Below Tier 1.</b> Your usage is under Tier 1's capacity (~${cap} min/mo). The Pro subscription may be more cost-effective — Tier 1 is the Business plan's entry point. Recommend Pro unless the customer wants Business-grade queue/concurrency.`,
    overBanner: (extraMin: string, extraCr: string, tot: boolean) => tot
      ? `<b>Above Tier 3.</b> Stay on Tier 3 and add <b>~${extraMin} min</b> (≈ <b>${extraCr} credits</b>) total via Get Credit.`
      : `<b>Above Tier 3.</b> Stay on Tier 3 and add <b>~${extraMin} min</b> (≈ <b>${extraCr} credits</b>) per cycle via Get Credit.`,
    payCycle: (m: number) => `${m} mo`,
    listLabel: (p: number) => `List $${p.toFixed(2)}`,
    discount: (p: number, pct: number) => `$${p.toFixed(2)} · −${pct}% vs list`,
    discountSmall: (p: number) => `$${p.toFixed(2)} · −<1% vs list`,
    needInput: "—",
  },
  ko: {
    badge: "비즈니스 플랜", title: "비즈니스 플랜 계산기",
    subtitle: "고객의 월 사용량을 입력하면 적합한 Tier를 추천하고, 서비스 크레딧을 더해 실질 분당 단가까지 보여줍니다.",
    s1: "월 사용량", s2: "추천 Tier", s3: "Tier 비교",
    s3_h: "— Tier를 클릭하면 서비스 크레딧이 해당 Tier에 적용됩니다", s4: "서비스 크레딧 (세일즈)",
    l_perlang: "언어당 월 더빙량", l_perlang_h: "(완성 영상 분)",
    l_langs: "언어 수", l_langs_h: "(사용량 × 언어 수)",
    l_lip: "더빙 유형", l_lip_h: "(립더빙은 크레딧 3배 차감)",
    lip_off: "더빙만 · ×1", lip_on: "립더빙 · ×3",
    l_basis: "입력 기준", l_basis_h: "(월 사용량 vs 전체 볼륨)",
    basis_mo: "월 기준", basis_tot: "전체 볼륨",
    s1_t: "사용량", l_perlang_t: "언어당 전체 더빙량", r_total_t: "총 사용량", u_min_t: "분",
    r_cycle_t: "이 Tier 월 capacity:",
    u_min: "분 / 월", u_min2: "분 / 월", u_min3: "분",
    r_total: "총 월 사용량", r_cycle: "추천 결제주기 기준 총량:",
    lip_note: "립더빙은 크레딧을 3배 차감하므로, 위 총량은 Tier 산정을 위해 크레딧 환산 분(= 완성 분 × 3)으로 표시됩니다.",
    reco_best: "최적", m_use: "고객 사용량", m_cap: "Tier 월 capacity",
    sc_applying: "적용 대상", sc_following: "· 추천 따라가는 중",
    l_sc: "지급할 서비스 크레딧", l_sc_h: "(플랜 크레딧에 더해지는 보너스)", u_cr: "크레딧",
    sc_note: "결제 금액은 Tier로 확정되어 변동되지 않습니다 — 추가 크레딧은 고객이 체감하는 실질 분당 단가만 낮춥니다.",
    k_final: "최종 지급 크레딧", k_base: "기본", k_bonus: "보너스",
    k_minutes: "최종 더빙 분량", k_rate: "60 크레딧 / 분",
    k_lipminutes: "최종 립더빙 분량", k_liprate: "180 크레딧 / 분",
    k_eff: "실질 분당 단가", k_pay: "결제 금액 (고정)", k_locked: "고정",
    e_pos: "0 이상의 숫자를 입력하세요.", e_lang: "언어 수는 최소 1개입니다.",
    f1: "<b>계산 방식:</b> Tier capacity는 월 환산 기준입니다(사이클 더빙 분량 ÷ 결제주기): Tier 1 ≈ 167분/월, Tier 2 ≈ 367분/월, Tier 3 ≈ 467분/월. 총 월 사용량을 커버하는 가장 작은 Tier를 추천합니다.",
    f2: "<b>Tier 1 미만:</b> 월 ~167분 미만 사용량은 Pro 구독으로 안내합니다. <b>Tier 3 초과:</b> Tier 3 유지 + Get Credit(추가 구매)으로 보충합니다.",
    f3: "<b>크레딧 로직:</b> 모든 Tier 공통 60 크레딧 = 더빙 1분. 서비스 크레딧은 세일즈 레버입니다 — 결제 금액은 Tier별로 고정, 실질 분당 단가만 움직입니다.",
    cycle: "주기", credits: "지급 크레딧", dubbing: "더빙 분량", monthlyCap: "월 capacity", perMin: "분당 단가",
    monthlyEq: (min: string) => `~${min}분/월`,
    cycleVal: (m: number, min: string) => `${min}분 (${m}개월)`,
    reasonFit: (t: Tier, tot: boolean) => tot
      ? `${t.cycleMonths}개월 주기에서 전체 더빙 볼륨을 여유 있게 커버합니다.`
      : `${t.cycleMonths}개월 주기에서 총 월 사용량을 여유 있게 커버합니다.`,
    reasonExact: (t: Tier, tot: boolean) => tot
      ? `전체 더빙 볼륨을 완전히 커버하는 가장 작은 Tier입니다 (${t.cycleMonths}개월 주기).`
      : `월 사용량을 완전히 커버하는 가장 작은 Tier입니다 (${t.cycleMonths}개월 주기).`,
    reasonOver: "최대 셀프서브 Tier를 초과합니다 — 아래 추가 구매 안내를 확인하세요.",
    reasonBelowT1: "Tier 1 최소 사용량 미만 — 보통 Pro 구독이 더 적합합니다 (아래 참고).",
    reasonEmpty: "월 사용량을 입력하면 추천이 표시됩니다.",
    proBanner: (cap: string, tot: boolean) => tot
      ? `<b>Tier 1 미만.</b> 전체 볼륨이 Tier 1 제공량(${cap}분)보다 적습니다. Pro 구독이 더 합리적일 수 있습니다 — Tier 1은 Business 플랜의 최소 진입점입니다. Business급 queue/동시처리가 꼭 필요한 경우가 아니라면 Pro를 권장하세요.`
      : `<b>Tier 1 미만.</b> 사용량이 Tier 1 월 capacity(~${cap}분/월)보다 적습니다. Pro 구독이 더 합리적일 수 있습니다 — Tier 1은 Business 플랜의 최소 진입점입니다. Business급 queue/동시처리가 꼭 필요한 경우가 아니라면 Pro를 권장하세요.`,
    overBanner: (extraMin: string, extraCr: string, tot: boolean) => tot
      ? `<b>Tier 3 초과.</b> Tier 3 유지 + 전체 <b>~${extraMin}분</b>(≈ <b>${extraCr} 크레딧</b>)을 Get Credit으로 추가하세요.`
      : `<b>Tier 3 초과.</b> Tier 3 유지 + 주기당 <b>~${extraMin}분</b>(≈ <b>${extraCr} 크레딧</b>)을 Get Credit으로 추가하세요.`,
    payCycle: (m: number) => `${m}개월`,
    listLabel: (p: number) => `정가 $${p.toFixed(2)}`,
    discount: (p: number, pct: number) => `$${p.toFixed(2)} · 정가 대비 −${pct}%`,
    discountSmall: (p: number) => `$${p.toFixed(2)} · 정가 대비 −1% 미만`,
    needInput: "—",
  },
} as const;

export function QuoteCalculator() {
  const { data: policy } = useQuery<Policy>({
    queryKey: ["quote-policy"],
    queryFn: () => getJSON("/api/ui/quote-policy"),
    staleTime: Infinity,   // a price list, not live data
  });

  const [lang, setLang] = useState<Lang>("en");
  const [basis, setBasis] = useState<Basis>("monthly");
  const [perlang, setPerlang] = useState("200");
  const [langs, setLangs] = useState("1");
  const [svc, setSvc] = useState("0");
  const [lipOn, setLipOn] = useState(false);
  const [selectedTier, setSelectedTier] = useState<number | null>(null);

  const L = STRINGS[lang];
  // Grouping follows the app's own toggle, not the browser locale.
  const loc = lang === "ko" ? "ko-KR" : "en-US";
  const fmt = (n: number) => Number(n).toLocaleString(loc, { maximumFractionDigits: 0 });
  const fmt1 = (n: number) => Number(n).toLocaleString(loc, { maximumFractionDigits: 1 });

  const lipMult = lipOn ? (policy?.lipMult ?? 3) : 1;
  const q = useMemo(
    () => policy && computeQuote(policy, {
      per: parseFloat(perlang), langs: parseInt(langs, 10), svc: parseFloat(svc),
      lipMult, basis, selectedTier,
    }),
    [policy, perlang, langs, svc, lipMult, basis, selectedTier],
  );

  if (!policy || !q) return <div className="t-muted">불러오는 중…</div>;

  const isTotal = q.isTotal;
  const unit = isTotal ? (lang === "ko" ? "분" : " min") : (lang === "ko" ? "분/월" : " min/mo");
  const bump = (value: string, delta: number, min: number) => {
    const v = parseFloat(value);
    return String(Math.max(min, (Number.isNaN(v) ? 0 : v) + delta));
  };
  const reason = q.overflow ? L.reasonOver
    : q.belowT1 ? L.reasonBelowT1
    : !q.recommended ? L.reasonEmpty
    : q.usage <= q.cap * 0.85 ? L.reasonFit(q.recommended, isTotal)
    : L.reasonExact(q.recommended, isTotal);

  return (
    <div className="qc" lang={lang}>
      <div className="qc__wrap">
        <div className="qc__head">
          <img className="qc__logo" alt="Perso AI"
               src="https://framerusercontent.com/images/uFDVb085duwGjtqfZUT5QUTJQk.svg" />
          <div className="qc__lang" role="tablist">
            {(["en", "ko"] as const).map((code) => (
              <button key={code} type="button" className={lang === code ? "is-on" : ""}
                      onClick={() => setLang(code)}>
                {code === "en" ? "EN" : "한국어"}
              </button>
            ))}
          </div>
        </div>

        <div className="qc__hero">
          <span className="qc__pill">{L.badge}</span>
          <h1>{L.title}</h1>
          <p>{L.subtitle}</p>
        </div>

        <div className="qc__grid">
          {/* 1 — usage input */}
          <div className="qc__card">
            <h2><span className="qc__n">1</span>{isTotal ? L.s1_t : L.s1}</h2>

            <div className="qc__field">
              <label>{L.l_basis} <span className="qc__hint">{L.l_basis_h}</span></label>
              <div className="qc__seg">
                {([["monthly", L.basis_mo], ["total", L.basis_tot]] as const).map(([key, label]) => (
                  <button key={key} type="button" className={basis === key ? "is-on" : ""}
                          onClick={() => setBasis(key as Basis)}>{label}</button>
                ))}
              </div>
            </div>

            <div className="qc__field">
              <label>
                {isTotal ? L.l_perlang_t : L.l_perlang} <span className="qc__hint">{L.l_perlang_h}</span>
              </label>
              <div className="qc__inputrow">
                <input type="number" min={0} step={10} inputMode="decimal"
                       value={perlang} onChange={(e) => setPerlang(e.target.value)} />
                <span className="qc__unit">{isTotal ? L.u_min_t : L.u_min}</span>
              </div>
              {q.badPer && <div className="qc__err">{L.e_pos}</div>}
            </div>

            <div className="qc__field">
              <label>{L.l_langs} <span className="qc__hint">{L.l_langs_h}</span></label>
              <div className="qc__inputrow">
                <button type="button" className="qc__stepper"
                        onClick={() => setLangs(bump(langs, -1, 1))}>−</button>
                <input type="number" min={1} step={1} inputMode="numeric" style={{ textAlign: "center" }}
                       value={langs} onChange={(e) => setLangs(e.target.value)} />
                <button type="button" className="qc__stepper"
                        onClick={() => setLangs(bump(langs, 1, 1))}>+</button>
              </div>
              {q.badLang && <div className="qc__err">{L.e_lang}</div>}
            </div>

            <div className="qc__field">
              <label>{L.l_lip} <span className="qc__hint">{L.l_lip_h}</span></label>
              <div className="qc__seg">
                <button type="button" className={lipOn ? "" : "is-on"} onClick={() => setLipOn(false)}>{L.lip_off}</button>
                <button type="button" className={lipOn ? "is-on" : ""} onClick={() => setLipOn(true)}>{L.lip_on}</button>
              </div>
            </div>

            <div className="qc__readout">
              <span className="qc__lab">{isTotal ? L.r_total_t : L.r_total}</span>
              <span className="qc__val">{fmt1(q.usage)} <small>{isTotal ? L.u_min_t : L.u_min2}</small></span>
            </div>
            {lipOn && <div className="qc__subnote">{L.lip_note}</div>}
            <div className="qc__subnote">
              {isTotal ? L.r_cycle_t : L.r_cycle}{" "}
              <b>
                {!q.recommended ? "—"
                  : isTotal ? L.monthlyEq(fmt(q.cycleTotal))
                  : L.cycleVal(q.recommended.cycleMonths, fmt(q.cycleTotal))}
              </b>
            </div>
          </div>

          {/* 2 — recommendation */}
          <div className="qc__card">
            <h2><span className="qc__n">2</span>{L.s2}</h2>
            <div className={`qc__recobox${q.recommended ? " " + q.recommended.key : ""}`}>
              <div className="qc__reco-head">
                <span className="qc__reco-badge">{L.reco_best}</span>
                <span className="qc__reco-title">{q.recommended ? `Tier ${q.recommended.id}` : L.needInput}</span>
                <span style={{ fontSize: 13, color: "var(--qc-muted)" }}>
                  {q.recommended && `· $${fmt(q.recommended.usd)} / ${L.payCycle(q.recommended.cycleMonths)}`}
                </span>
              </div>
              <p className="qc__reco-reason">{reason}</p>

              <div className="qc__util">
                <div className="qc__util-bar">
                  <div className="qc__util-fill" style={{ width: `${q.util.toFixed(0)}%` }} />
                </div>
                <div className="qc__util-meta">
                  <span>{L.m_use}: <b>{q.recommended ? fmt1(q.usage) + unit : "—"}</b></span>
                  <span>{L.m_cap}: <b>{q.recommended ? fmt(q.cap) + unit : "—"}</b></span>
                </div>
              </div>

              {q.recommended && q.belowT1 && (
                <div className="qc__banner pro">
                  <span className="qc__ic">💡</span>
                  <div dangerouslySetInnerHTML={{ __html: L.proBanner(fmt(q.proBannerCap), isTotal) }} />
                </div>
              )}
              {q.recommended && q.showOverBanner && (
                <div className="qc__banner over">
                  <span className="qc__ic">⚠️</span>
                  <div dangerouslySetInnerHTML={{ __html: L.overBanner(fmt(q.extraMin), fmt(q.extraCr), isTotal) }} />
                </div>
              )}
            </div>
          </div>
        </div>

        {/* 3 — tier comparison */}
        <div className="qc__card" style={{ marginTop: 18 }}>
          <h2>
            <span className="qc__n">3</span>{L.s3}
            <span style={{ fontWeight: 400, textTransform: "none", letterSpacing: 0, color: "var(--qc-muted)" }}>
              {L.s3_h}
            </span>
          </h2>
          <div className="qc__tiers">
            {policy.tiers.map((t) => {
              const isReco = q.recommended?.id === t.id;
              return (
                <button
                  key={t.key} type="button" data-reco={L.reco_best}
                  className={`qc__tier${t.id === q.active.id ? " is-on" : ""}${isReco ? " is-reco" : ""}`}
                  // Clicking pins the tier the service credit applies to; clicking the
                  // pinned one again returns to following the recommendation.
                  onClick={() => setSelectedTier(selectedTier === t.id ? null : t.id)}
                >
                  <div className="qc__tname">Tier {t.id}</div>
                  <div className="qc__tcycle">{L.payCycle(t.cycleMonths)}</div>
                  <div className="qc__tprice">${fmt(t.usd)} <small>/ {L.cycle}</small></div>
                  <div className="qc__tkrw">₩{fmt(t.krw)} · ${t.monthlyUsd}/mo</div>
                  <div className="qc__tspecs">
                    <div><span>{L.credits}</span><span>{fmt(t.credits)}</span></div>
                    <div><span>{L.dubbing}</span><span>{fmt(t.dubbingMin)} min</span></div>
                    <div><span>{L.monthlyCap}</span><span>{fmt(Math.round(t.monthlyCap))} min</span></div>
                    <div><span>{L.perMin}</span><span>${t.perMin.toFixed(2)}</span></div>
                    <div><span>Queue / Conc.</span><span>{t.queue} / {t.concurrent}</span></div>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* 4 — service credit */}
        <div className="qc__card" style={{ marginTop: 18 }}>
          <h2><span className="qc__n">4</span>{L.s4}</h2>
          <div className="qc__activetier-tag">
            {L.sc_applying}: <b>Tier {q.active.id}</b>{" "}
            <span style={{ color: "var(--qc-muted)", fontWeight: 400 }}>
              {selectedTier === null ? L.sc_following : ""}
            </span>
          </div>
          <div className="qc__scgrid">
            <div>
              <div className="qc__field" style={{ marginBottom: 0 }}>
                <label>{L.l_sc} <span className="qc__hint">{L.l_sc_h}</span></label>
                <div className="qc__inputrow">
                  <input type="number" min={0} step={1000} inputMode="numeric"
                         value={svc} onChange={(e) => setSvc(e.target.value)} />
                  <span className="qc__unit">{L.u_cr}</span>
                </div>
                {q.badSvc && <div className="qc__err">{L.e_pos}</div>}
              </div>
              <div className="qc__subnote">{L.sc_note}</div>
            </div>

            <div className="qc__metrics">
              <div className="qc__metric">
                <div className="qc__mlab">{L.k_final}</div>
                <div className="qc__mval">{fmt(q.finalCr)}</div>
                <div className="qc__msub mut">{L.k_base} {fmt(q.baseCr)} + {L.k_bonus} {fmt(q.finalCr - q.baseCr)}</div>
              </div>
              <div className="qc__metric">
                <div className="qc__mlab">{L.k_minutes}</div>
                <div className="qc__mval">{fmt(q.finalMin)} <small>{L.u_min3}</small></div>
                <div className="qc__msub mut">{L.k_rate}</div>
              </div>
              <div className="qc__metric">
                <div className="qc__mlab">{L.k_lipminutes}</div>
                <div className="qc__mval">{fmt(q.lipFinalMin)} <small>{L.u_min3}</small></div>
                <div className="qc__msub mut">{L.k_liprate}</div>
              </div>
              <div className="qc__metric">
                <div className="qc__mlab">{L.k_eff}</div>
                <div className="qc__mval qc__gradtext">${q.effPrice.toFixed(2)}</div>
                {/* the label has to agree with the number above it, hence the same threshold */}
                <div className={`qc__msub ${q.cheaper ? "down" : "mut"}`}>
                  {q.cheaper
                    ? (q.pct >= 1 ? L.discount(q.effPrice, q.pct) : L.discountSmall(q.effPrice))
                    : L.listLabel(q.active.perMin)}
                </div>
              </div>
              <div className="qc__metric lock">
                <div className="qc__mlab">{L.k_pay} <span className="qc__lockchip">{L.k_locked}</span></div>
                <div className="qc__mval">${fmt(q.active.usd)}</div>
                <div className="qc__msub mut">₩{fmt(q.active.krw)} · {L.payCycle(q.active.cycleMonths)}</div>
              </div>
            </div>
          </div>
        </div>

        <div className="qc__footer">
          <span dangerouslySetInnerHTML={{ __html: L.f1 }} /><br />
          <span dangerouslySetInnerHTML={{ __html: L.f2 }} /><br />
          <span dangerouslySetInnerHTML={{ __html: L.f3 }} />
        </div>
      </div>
    </div>
  );
}
