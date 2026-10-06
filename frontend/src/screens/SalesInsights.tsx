import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getJSON } from "../lib/api";
import { stamp, useSalesInsight } from "../lib/usageData";
import { useUsageSource } from "../lib/usageSource";
import { DataTable, type Column } from "../ui/DataTable";
import { parseSpaceSeqs } from "./won/usage";
import type { ListData } from "./won/shared";

/** 영업 인사이트 — 스냅샷(제품 전체)에서 **우리가 모르는 사람 중 눈에 띄는 스페이스**를 본다 (2026-09-15
 *  운영자 요청). 값은 전부 매일 가공된 집계(`sales.json`)를 브라우저가 GitHub 에서 직접 받은 것이고, 서버로는
 *  한 바이트도 안 간다. 서버에서 오는 것은 우리 수주 고객의 space_seq 목록과 그 집계를 받을 열쇠뿐 — space_seq
 *  목록으로 「우리 고객」을 걸러낸다.
 *
 *  「주목할 스페이스」 하나만 남는다 (2026-10-06 운영자: 「주목할 스페이스 말고 아래에 있는거 모두 다 삭제」) —
 *  그 아래의 제품 전체 흐름(가입 · 구독 · 체크아웃 · 플랜별 크레딧 · 언어쌍 · 업로드 경로 · 로그인)은 가공기도
 *  더 안 낸다(`agent/sales.go`). 그 전에는 「고객 인사이트」(손이 가야 하는 리드 목록 · 갱신 임박)가 이 자리였다.
 */

type Space = {
  space_seq: number; tier: string; plan_name: string | null; sub_status: string | null; ent: boolean;
  credits_30d: number; credits_90d: number; exports_30d: number; exports_6m: number; failed_6m: number;
  users: number; members: number; seat: number; first_job: string | null; last_job: string | null; top_pair: string | null;
};
type Sales = { spaces: Space[] | null; spaces_active_6m: number; fail_rate_all: number | null };

const num = (v: number | null | undefined) => Number(v ?? 0).toLocaleString("en-US");
const mins = (credits: number) => `${num(Math.round(credits / 60))}분`;
const TIER: Record<string, string> = { free: "Free", starter: "Starter", creator: "Creator", pro: "Pro", team: "Team", business: "Business", flex: "Flex", enterprise: "Enterprise" };
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

export function SalesInsights() {
  const usageSource = useUsageSource();
  const sales = useSalesInsight<Sales>(usageSource.source);
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

  const asOf = sales.data && stamp(sales.data.snapshot_at);

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">영업 인사이트</h1>
          {asOf && <div className="t-sm td-subtle" style={{ marginTop: 4 }}>데이터 기준 <strong>{asOf}</strong></div>}
        </div>
      </div>

      {(usageSource.configured === false || usageSource.problem) && (
        <div className="card card--warn mb-gap">
          <strong>사용 데이터가 연결돼 있지 않습니다.</strong>
          <div className="t-sm td-subtle" style={{ marginTop: 6 }}>
            이 화면의 값은 전부 매일 가공된 스냅샷을 브라우저가 GitHub 에서 직접 받아 그립니다 — 서버는 그 데이터를 모릅니다.{" "}
            <Link to="/data">「데이터 분석」</Link> 화면을 보세요.
            {usageSource.problem && <> · 사유: {usageSource.problem}</>}
          </div>
        </div>
      )}
      {(usageSource.busy || sales.busy) && <div className="card mb-gap">불러오는 중…</div>}
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
        </>
      )}
    </>
  );
}
