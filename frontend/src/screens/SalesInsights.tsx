import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getJSON } from "../lib/api";
import { AGENT_DOWNLOADS, useAgent, useSalesInsight } from "../lib/agent";
import { DataTable, type Column } from "../ui/DataTable";
import { parseSpaceSeqs } from "./won/usage";
import type { ListData } from "./won/shared";

/** 영업 인사이트 — 이 PC 의 스냅샷(제품 전체)에서 **우리가 모르는 사람 중 눈에 띄는 스페이스**와
 *  제품 전체 흐름을 본다 (2026-09-15 운영자 요청). 값은 전부 에이전트가 이 PC 에서 세고, 서버로는
 *  한 바이트도 안 간다. 서버에서 오는 것은 우리 수주 고객의 space_seq 목록뿐 — 그것으로 「우리
 *  고객」을 걸러낸다.
 *
 *  「고객 인사이트」(손이 가야 하는 리드 목록 · 갱신 임박)가 있던 자리다 — 운영자 지시로 전부 뺐다.
 */

type Space = {
  space_seq: number; tier: string; plan_name: string | null; sub_status: string | null; ent: boolean;
  credits_30d: number; credits_90d: number; exports_30d: number; exports_6m: number; failed_6m: number;
  users: number; members: number; seat: number; first_job: string | null; last_job: string | null; top_pair: string | null;
};
type Sales = {
  spaces: Space[] | null; spaces_active_6m: number;
  joins_monthly: { period: string; n: number }[] | null;
  users_all: { total: number; active_30d: number; active_90d: number };
  login: { provider: string; n: number }[] | null;
  subs_monthly: { period: string; created: number; deleted: number }[] | null;
  subs_new_by_plan: { plan: string; n: number }[] | null;
  tiers_30d: { tier: string; ent: boolean; spaces: number; exports: number; credits: number }[] | null;
  credits_monthly: { period: string; tier: string; credits: number }[] | null;
  checkouts_monthly: { period: string; sessions: number; expired: number; spaces: number }[] | null;
  pairs_6m: { pair: string; n: number }[] | null;
  sources_6m: { source: string; n: number }[] | null;
  fail_rate_all: number | null; jobs_from: string | null; credits_from: string | null;
};

const num = (v: number | null | undefined) => Number(v ?? 0).toLocaleString("en-US");
const mins = (credits: number) => `${num(Math.round(credits / 60))}분`;
const TIER: Record<string, string> = { free: "Free", starter: "Starter", creator: "Creator", pro: "Pro", team: "Team", business: "Business", flex: "Flex", enterprise: "Enterprise" };
const SOURCE: Record<string, string> = { FILE_UPLOAD: "파일 업로드", YOUTUBE: "YouTube", TIKTOK: "TikTok", GOOGLE_DRIVE: "Google Drive", "(미기록)": "미기록" };
const LANG: Record<string, string> = {
  ko: "한국어", en: "영어", ja: "일본어", zh: "중국어", es: "스페인어", pt: "포르투갈어", fr: "프랑스어", de: "독일어", it: "이탈리아어",
  ru: "러시아어", vi: "베트남어", th: "태국어", id: "인도네시아어", tr: "튀르키예어", ar: "아랍어", hi: "힌디어", nl: "네덜란드어", pl: "폴란드어",
  sk: "슬로바키아어", sv: "스웨덴어", da: "덴마크어", fi: "핀란드어", no: "노르웨이어", cs: "체코어", hu: "헝가리어", el: "그리스어", uk: "우크라이나어",
  ro: "루마니아어", ms: "말레이어", tl: "타갈로그어", he: "히브리어", fa: "페르시아어", bn: "벵골어", ta: "타밀어", ur: "우르두어",
};
const pairName = (pair: string | null) => (pair ?? "").split(" → ").map((c) => LANG[c.split("-")[0]] ?? c).join(" → ");

/** 눈에 띄는 이유 — 어느 것이 붙나로 목록을 거른다. 문턱은 2026-09-14 스냅샷의 실측에서 잡았다:
 *  셀프서브 스페이스 30일 크레딧의 상위 1% 가 3,285 (= 55분) 이고, 상위 열다섯은 전부 Pro 로
 *  10,000~47,000 (170~790분) 을 쓴다 — 엔터프라이즈 제안 대상이 곧 그들이다. */
const SIGNALS = [
  { key: "ent-unknown", label: "엔터프라이즈 · 우리 기록 없음", tone: "danger",
    hint: "제품에서는 엔터프라이즈로 묶여 있는데 수주 고객에 그 space_seq 가 없습니다 — 계약이 우리 밖에서 맺어졌거나 space_seq 를 안 적은 고객입니다.",
    test: (s: Space, ours: boolean) => s.ent && !ours },
  { key: "heavy", label: "셀프서브 대량 사용", tone: "accent",
    hint: "엔터프라이즈가 아닌데 30일에 3,000 크레딧(50분) 이상 — 셀프서브 상위 1%. 엔터프라이즈 제안 대상.",
    test: (s: Space) => !s.ent && s.credits_30d >= 3000 },
  { key: "team", label: "여럿이 쓴다", tone: "info",
    hint: "6개월 안에 내보내기를 한 사용자가 둘 이상이거나 멤버가 셋 이상 — 개인이 아니라 팀입니다.",
    test: (s: Space) => s.users >= 2 || s.members >= 3 },
  { key: "trial-heavy", label: "트라이얼인데 많이 쓴다", tone: "warn",
    hint: "구독 상태가 trialing 인데 30일 1,500 크레딧 이상 — 결제로 넘어갈 자리.",
    test: (s: Space) => s.sub_status === "trialing" && s.credits_30d >= 1500 },
  { key: "past-due", label: "구독 연체", tone: "danger",
    hint: "구독 상태 past_due — 결제가 막힌 채 쓰고 있습니다.",
    test: (s: Space) => s.sub_status === "past_due" },
  { key: "free-active", label: "무료인데 활발", tone: "info",
    hint: "Free 플랜인데 30일 내보내기 10건 이상.",
    test: (s: Space) => s.tier === "free" && s.exports_30d >= 10 },
  { key: "failing", label: "실패가 잦다", tone: "warn",
    hint: "6개월 실패율이 전사 평균의 2배 이상(내보내기 10건 이상일 때) — 지원 연락의 명분.",
    test: (s: Space, _o: boolean, failAll: number | null) =>
      failAll !== null && s.exports_6m >= 10 && s.failed_6m / s.exports_6m >= (failAll / 100) * 2 },
] as const;
type SignalKey = (typeof SIGNALS)[number]["key"];

function Pill({ tone, children, title }: { tone: string; children: React.ReactNode; title?: string }) {
  return <span className={`pill pill--sm pill--${tone}`} title={title}>{children}</span>;
}

function Bars({ rows, unit = "" }: { rows: { label: string; n: number; sub?: string }[]; unit?: string }) {
  const max = Math.max(1, ...rows.map((r) => r.n));
  if (!rows.length) return <div className="t-sm td-subtle">해당 없음</div>;
  return (
    <div className="stack" style={{ gap: 8 }}>
      {rows.map((r) => (
        <div key={r.label} style={{ display: "grid", gridTemplateColumns: "minmax(90px,150px) 1fr 74px", alignItems: "center", gap: 10, fontSize: 12.5 }}>
          <span className="td-subtle truncate" title={r.sub ?? r.label}>{r.label}</span>
          <span style={{ height: 8, borderRadius: 99, background: "var(--surface-3)", overflow: "hidden" }}>
            <span style={{ display: "block", height: "100%", width: `${(r.n / max) * 100}%`, background: "var(--accent)", borderRadius: 99 }} />
          </span>
          <span className="tnum" style={{ textAlign: "right", fontWeight: 600 }}>{num(r.n)}{unit}</span>
        </div>
      ))}
    </div>
  );
}

/** 월별 몇 줄(예: 생성·해지)을 나란히 — 콘솔의 표 하나(`DataTable`)로. 열두 달이라 막대보다 숫자가 읽기 낫다. */
function Months({ rows, cols }: { rows: Record<string, number | string>[]; cols: { key: string; label: string; tone?: string }[] }) {
  const columns: Column<Record<string, number | string>>[] = [
    { label: "월", width: "84px", className: "mono", cell: (r) => String(r.period) },
    ...cols.map((c) => ({
      label: c.label, width: `${Math.max(64, Math.floor(200 / cols.length))}px`, className: "tnum", headClassName: "th-right",
      cell: (r: Record<string, number | string>) => <span style={{ display: "block", textAlign: "right", color: c.tone }}>{num(Number(r[c.key] ?? 0))}</span>,
    })),
  ];
  return <DataTable columns={columns} rows={rows} rowKey={(r) => String(r.period)} empty="기록 없음" />;
}

function Kpi({ label, value, sub, accent }: { label: string; value: React.ReactNode; sub?: string; accent?: boolean }) {
  return (
    <div className={`card kpi${accent ? " kpi--accent" : ""}`}>
      <div className="kpi__label">{label}</div>
      <div className="kpi__row"><div className="kpi__value">{value}</div></div>
      {sub && <div className="kpi__sub">{sub}</div>}
    </div>
  );
}

export function SalesInsights() {
  const agent = useAgent();
  const sales = useSalesInsight<Sales>(agent.pair);
  // 우리 수주 고객의 space_seq — 서버에서 오는 유일한 것. 이걸로 「우리 기록 없음」을 가른다.
  const { data: won } = useQuery({ queryKey: ["won-customers"], queryFn: () => getJSON<ListData>("/api/ui/won-customers") });
  const ours = useMemo(() => {
    const map = new Map<number, number>();
    for (const row of won?.rows ?? []) {
      for (const c of [row.active, ...(row.contracts ?? [])]) {
        for (const s of parseSpaceSeqs(c?.space_seq)) map.set(s, row.client_id);
      }
    }
    return map;
  }, [won]);

  const [signal, setSignal] = useState<SignalKey | "all">("all");
  const [includeOurs, setIncludeOurs] = useState(false);
  const [limit, setLimit] = useState(60);
  const d = sales.data?.data;
  const isMac = /Mac/i.test(navigator.platform);

  const rows = useMemo(() => {
    if (!d?.spaces) return [];
    const failAll = d.fail_rate_all;
    return d.spaces
      .map((s) => {
        const mine = ours.has(s.space_seq);
        const signals = SIGNALS.filter((g) => g.test(s, mine, failAll)).map((g) => g.key);
        return { ...s, mine, signals };
      })
      .filter((s) => (includeOurs || !s.mine) && (signal === "all" ? s.signals.length > 0 : s.signals.includes(signal)));
  }, [d, ours, signal, includeOurs]);

  const counts = useMemo(() => {
    const out: Record<string, number> = {};
    if (!d?.spaces) return out;
    for (const s of d.spaces) {
      const mine = ours.has(s.space_seq);
      if (!includeOurs && mine) continue;
      for (const g of SIGNALS) if (g.test(s, mine, d.fail_rate_all)) out[g.key] = (out[g.key] ?? 0) + 1;
    }
    return out;
  }, [d, ours, includeOurs]);

  const columns: Column<(typeof rows)[number]>[] = [
    { label: "Space", width: "92px", className: "mono tnum", cell: (s) => s.mine
        ? <Link to={`/won-customers/${ours.get(s.space_seq)}`} title="수주 고객">{s.space_seq}</Link>
        : <span title="허브스팟 연락처의 「space seq」 칸으로 찾습니다">{s.space_seq}</span> },
    { label: "플랜", width: "180px", cell: (s) => (
        <span className="row" style={{ gap: 6 }}>
          <Pill tone={s.ent ? "accent" : s.tier === "free" ? "info" : "ok"}>{TIER[s.tier] ?? s.tier}</Pill>
          {s.plan_name && s.tier === "enterprise" && <span className="t-xs td-subtle truncate" title={s.plan_name}>{s.plan_name}</span>}
          {s.sub_status && s.sub_status !== "active" && <span className="t-xs td-subtle">{s.sub_status}</span>}
        </span>) },
    { label: "30일 크레딧", width: "120px", className: "tnum", headClassName: "th-right", cell: (s) => (
        <span style={{ display: "block", textAlign: "right" }}><strong>{num(s.credits_30d)}</strong> <span className="td-subtle t-xs">{mins(s.credits_30d)}</span></span>) },
    { label: "90일", width: "90px", className: "tnum", headClassName: "th-right", cell: (s) => <span style={{ display: "block", textAlign: "right" }}>{num(s.credits_90d)}</span> },
    { label: "내보내기 30일 / 6개월", width: "130px", className: "tnum", headClassName: "th-right", cell: (s) => (
        <span style={{ display: "block", textAlign: "right" }}>{num(s.exports_30d)} <span className="td-subtle">/ {num(s.exports_6m)}</span>
          {s.failed_6m > 0 && <span className="t-xs td-subtle"> · 실패 {s.failed_6m}</span>}</span>) },
    { label: "사용자 · 멤버 · 좌석", width: "120px", className: "tnum", cell: (s) => `${s.users} · ${s.members} · ${s.seat}` },
    { label: "마지막 작업", width: "110px", className: "mono nowrap", cell: (s) => s.last_job ?? "—" },
    { label: "주 언어쌍", width: "150px", cell: (s) => <span title={s.top_pair ?? ""}>{pairName(s.top_pair)}</span> },
    { label: "신호", cell: (s) => (
        <span className="row" style={{ gap: 4, flexWrap: "wrap" }}>
          {s.signals.map((k) => { const g = SIGNALS.find((x) => x.key === k)!; return <Pill key={k} tone={g.tone} title={g.hint}>{g.label}</Pill>; })}
          {s.mine && <Pill tone="ok" title="수주 고객에 이 space_seq 가 적혀 있습니다">우리 고객</Pill>}
        </span>) },
  ];

  const stamp = sales.data?.snapshot_at?.slice(0, 16).replace("T", " ");
  const tiers = (d?.tiers_30d ?? []).map((t) => ({ label: `${TIER[t.tier] ?? t.tier}${t.ent ? " (엔터프라이즈 연결)" : ""}`, n: t.spaces, sub: `내보내기 ${num(t.exports)} · 크레딧 ${num(t.credits)}` }));
  const creditsByMonth = useMemo(() => {
    const map = new Map<string, Record<string, number | string>>();
    for (const r of d?.credits_monthly ?? []) {
      const row = map.get(r.period) ?? { period: r.period };
      row[r.tier] = (Number(row[r.tier] ?? 0)) + r.credits;
      map.set(r.period, row);
    }
    return [...map.values()].sort((a, b) => String(a.period).localeCompare(String(b.period))).slice(-12);
  }, [d]);
  const creditTiers = useMemo(() => {
    const seen = new Set<string>();
    for (const r of d?.credits_monthly ?? []) seen.add(r.tier);
    return ["enterprise", "pro", "creator", "starter", "team", "free"].filter((t) => seen.has(t));
  }, [d]);
  const lastSub = d?.subs_monthly?.at(-1);
  const lastCheckout = d?.checkouts_monthly?.at(-1);

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">영업 인사이트</h1>
          <div className="t-sm td-subtle" style={{ marginTop: 4 }}>
            제품 전체 스냅샷에서 — 우리가 모르는 사람 중 눈에 띄는 스페이스와 전체 흐름.
            {stamp && <> 데이터 기준 <strong>{stamp}</strong>.</>}
          </div>
        </div>
      </div>

      {!agent.busy && !agent.live && (
        <div className="card card--warn mb-gap">
          <strong>이 PC 에 데이터 에이전트가 없거나 꺼져 있습니다.</strong>
          <div className="t-sm td-subtle" style={{ marginTop: 6 }}>
            이 화면의 값은 전부 이 PC 의 스냅샷에서 계산됩니다 — 서버는 그 데이터를 모릅니다.{" "}
            <a href={isMac ? AGENT_DOWNLOADS.macArm : AGENT_DOWNLOADS.windows}>에이전트 내려받기 ({isMac ? "Mac" : "Windows"})</a>
            {agent.problem && <> · 사유: {agent.problem}</>}
          </div>
        </div>
      )}
      {agent.live && sales.busy && <div className="card mb-gap">계산 중… (스냅샷 전체를 한 번 훑습니다)</div>}
      {sales.problem && <div className="card card--warn mb-gap">가져오지 못했습니다: {sales.problem}</div>}

      {d && (
        <>
          <section className="card mb-gap">
            <div className="section-header" style={{ marginBottom: 10 }}>
              <div className="section-header__l">
                <div>
                  <div className="section-header__title">주목할 스페이스</div>
                  <div className="section-header__sub">
                    6개월 안에 내보내기가 있는 스페이스 {num(d.spaces_active_6m)}개 중 눈에 띄는 {num(d.spaces?.length ?? 0)}개 — 그중 우리 기록에 없는 것.
                    Space 번호는 허브스팟 연락처의 「space seq」 칸으로 사람에게 이어집니다.
                  </div>
                </div>
              </div>
              <label className="row t-sm" style={{ gap: 6, cursor: "pointer" }}>
                <input type="checkbox" checked={includeOurs} onChange={(e) => setIncludeOurs(e.target.checked)} /> 우리 고객도 보기
              </label>
            </div>
            <div className="chip-row" style={{ marginBottom: 12 }}>
              <button type="button" className={`chip${signal === "all" ? " is-active" : ""}`} onClick={() => setSignal("all")}>전체 {num(rows.length)}</button>
              {SIGNALS.map((g) => (
                <button key={g.key} type="button" className={`chip${signal === g.key ? " is-active" : ""}`} title={g.hint}
                        onClick={() => setSignal(g.key)}>{g.label} {num(counts[g.key] ?? 0)}</button>
              ))}
            </div>
            <DataTable columns={columns} rows={rows.slice(0, limit)} rowKey={(s) => s.space_seq}
                       empty="해당하는 스페이스가 없습니다." />
            {rows.length > limit && (
              <div style={{ marginTop: 10 }}>
                <button type="button" className="btn btn--sm" onClick={() => setLimit(limit + 60)}>더 보기 (남은 {num(rows.length - limit)})</button>
              </div>
            )}
          </section>

          <div className="grid grid-4 mb-gap">
            <Kpi label="가입 사용자" value={num(d.users_all.total)} sub={`30일 로그인 ${num(d.users_all.active_30d)} · 90일 ${num(d.users_all.active_90d)}`} />
            <Kpi label="6개월 활성 스페이스" value={num(d.spaces_active_6m)} sub="내보내기가 한 건이라도 있는 스페이스" />
            <Kpi label={`구독 ${lastSub?.period ?? ""}`} value={lastSub ? `+${num(lastSub.created)} / −${num(lastSub.deleted)}` : "—"} sub="이번 달 생성 / 해지 (셀프서브)" accent />
            <Kpi label="전사 실패율" value={d.fail_rate_all === null ? "—" : `${d.fail_rate_all}%`} sub="6개월 내보내기 기준" />
          </div>

          <div className="grid grid-2 mb-gap">
            <section className="card">
              <div className="section-header" style={{ marginBottom: 10 }}><div className="section-header__l"><div>
                <div className="section-header__title">30일 활성 스페이스 — 플랜별</div>
                <div className="section-header__sub">최근 30일에 내보내기가 있는 스페이스 수 · 그들의 내보내기 · 크레딧</div></div></div></div>
              <Bars rows={tiers} unit="개" />
            </section>
            <section className="card">
              <div className="section-header" style={{ marginBottom: 10 }}><div className="section-header__l"><div>
                <div className="section-header__title">월별 크레딧 소진 — 플랜별</div>
                <div className="section-header__sub">{d.credits_from ? `${d.credits_from.slice(0, 10)}부터` : ""} · 취소(ROLLBACK)는 뺀 값</div></div></div></div>
              <Months rows={creditsByMonth} cols={creditTiers.map((t) => ({ key: t, label: TIER[t] ?? t }))} />
            </section>
          </div>

          <div className="grid grid-3 mb-gap">
            <section className="card">
              <div className="section-header" style={{ marginBottom: 10 }}><div className="section-header__l"><div>
                <div className="section-header__title">월별 가입</div>
                <div className="section-header__sub">사용자 계정 생성</div></div></div></div>
              <Months rows={(d.joins_monthly ?? []) as Record<string, number | string>[]} cols={[{ key: "n", label: "가입" }]} />
            </section>
            <section className="card">
              <div className="section-header" style={{ marginBottom: 10 }}><div className="section-header__l"><div>
                <div className="section-header__title">월별 구독 생성 · 해지</div>
                <div className="section-header__sub">셀프서브(Stripe) 구독 이벤트</div></div></div></div>
              <Months rows={(d.subs_monthly ?? []) as Record<string, number | string>[]}
                      cols={[{ key: "created", label: "생성", tone: "var(--ok)" }, { key: "deleted", label: "해지", tone: "var(--danger)" }]} />
            </section>
            <section className="card">
              <div className="section-header" style={{ marginBottom: 10 }}><div className="section-header__l"><div>
                <div className="section-header__title">결제 체크아웃</div>
                <div className="section-header__sub">
                  결제창까지 간 세션과 그중 만료(안 낸) 수{lastCheckout ? ` — ${lastCheckout.period} 만료율 ${Math.round((lastCheckout.expired / Math.max(1, lastCheckout.sessions)) * 100)}%` : ""}
                </div></div></div></div>
              <Months rows={(d.checkouts_monthly ?? []) as Record<string, number | string>[]}
                      cols={[{ key: "sessions", label: "세션" }, { key: "expired", label: "만료", tone: "var(--warn)" }, { key: "spaces", label: "스페이스" }]} />
            </section>
          </div>

          <div className="grid grid-3 mb-gap">
            <section className="card">
              <div className="section-header" style={{ marginBottom: 10 }}><div className="section-header__l"><div>
                <div className="section-header__title">언어쌍 Top 10</div>
                <div className="section-header__sub">6개월 내보내기</div></div></div></div>
              <Bars rows={(d.pairs_6m ?? []).map((p) => ({ label: pairName(p.pair), n: p.n, sub: p.pair }))} unit="편" />
            </section>
            <section className="card">
              <div className="section-header" style={{ marginBottom: 10 }}><div className="section-header__l"><div>
                <div className="section-header__title">새 구독 — 플랜별</div>
                <div className="section-header__sub">최근 6개월에 생성된 구독</div></div></div></div>
              <Bars rows={(d.subs_new_by_plan ?? []).map((p) => ({ label: p.plan, n: p.n }))} unit="건" />
            </section>
            <section className="card">
              <div className="section-header" style={{ marginBottom: 10 }}><div className="section-header__l"><div>
                <div className="section-header__title">업로드 경로 · 로그인</div>
                <div className="section-header__sub">6개월 내보내기 · 전체 사용자</div></div></div></div>
              <Bars rows={(d.sources_6m ?? []).map((s) => ({ label: SOURCE[s.source] ?? s.source, n: s.n }))} unit="편" />
              <div style={{ height: 12 }} />
              <Bars rows={(d.login ?? []).map((l) => ({ label: l.provider, n: l.n }))} unit="명" />
            </section>
          </div>
        </>
      )}
    </>
  );
}
