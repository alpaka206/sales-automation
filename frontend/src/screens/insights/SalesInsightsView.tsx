import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { InsightRule } from "../../lib/insightRules";
import { stamp, useSalesInsight } from "../../lib/usageData";
import type { UsageSource } from "../../lib/usageSource";
import { parseSpaceSeqs, rulesFrom, type Rules } from "../won/usage";
import { spacesOfRows, useUsageIndex } from "../won/useUsage";
import type { ListData } from "../won/shared";
import {
  contractRows, upsellMaybeCut, upsellRows, CANDIDATE_CAP,
  type ChurnRow, type ContractRow, type GrowRow, type SalesData, type Tab, type UpRow,
} from "./judge";
import { RuleLine } from "./RuleLine";

/** 영업 인사이트 — **안쪽 겹** (2026-10-15 운영자 지시: 목업 `영업인사이트.html` 대로 개편).
 *
 *  카드 셋이 곧 탭입니다 — 업셀링(우리 고객이 아닌 Pro 스페이스 중 30일 크레딧이 기준 이상) · 이탈 방지(계약
 *  크레딧이 남을 전망인 수주 고객) · 추가 계약 기회(계약 종료가 가깝거나 크레딧이 먼저 떨어질 수주 고객). 판정은
 *  `judge.ts` 이고, 수주 고객 쪽 판정은 수주 고객 「사용 진단」과 같은 함수 · 같은 기준(서버에 저장)입니다.
 *
 *  값은 전부 매일 가공된 스냅샷(`sales.json` · `summary.json`)을 브라우저가 GitHub 에서 직접 받은 것이고, 이 파일은
 *  서버를 못 부릅니다(`tests/test_usage_data_stays_off_server.py` 의 `DATA_SIDE` — 이 폴더 전체). 서버에서 오는
 *  것(수주 고객 목록 · 판정 기준 · 사용 데이터의 열쇠)은 바깥 겹(`screens/SalesInsights.tsx`)이 받아 내려 주고,
 *  서버로 가는 것은 사람이 「기준 수정」에서 적은 숫자뿐입니다(`RuleLine.tsx`).
 *
 *  크레딧을 분으로 바꿔 적지 않습니다 — 크레딧과 분의 비율은 더빙 모델마다 달라 「60크레딧 = 1분」은 정확하지
 *  않습니다(2026-10-14 운영자). 목업에 있던 주간 막대는 쓰이지 않는 그림이라 옮기지 않았습니다.
 *
 *  예전의 「주목할 스페이스」 표와 신호 다섯(엔터프라이즈 · 우리 기록 없음 · 트라이얼인데 많이 쓴다 · 구독 연체 ·
 *  무료인데 활발 · 실패가 잦다)은 목업에 없어서 지웠습니다(2026-10-16 운영자 확인 ②). 「셀프서브 대량 사용」은
 *  업셀링 카드가 되었고 그 문턱(9,720)은 이제 서버에 저장된 기준입니다.
 */

type Props = {
  /** `useUsageSource()` 의 답 그대로 — 열쇠와 그 상태. */
  usage: { source: UsageSource | null; configured: boolean | null; problem: string | null; busy: boolean };
  won: ListData | null;
  /** `useInsightRules()` 의 답 그대로 — 판정 기준 여섯과 누가 언제. */
  rules: { list: InsightRule[] | null; problem: string | null; busy: boolean };
  onSaveRules: (tab: Tab, values: Record<string, number>) => Promise<unknown>;
};

type Card = { name: string; desc: (r: Rules) => string; title: string; search: string; todo: string; note: string };
const CARDS: Record<Tab, Card> = {
  up: {
    name: "업셀링", desc: () => "PRO 플랜에서 B2B로 올릴 만한 스페이스", title: "업셀링 후보",
    search: "Space 번호 검색", todo: "미컨택", note: "",
  },
  churn: {
    name: "이탈 방지", desc: () => "계약 크레딧이 남을 전망인 수주 고객", title: "이탈 위험 고객",
    search: "고객명 · Client ID 검색", todo: "미조치",
    note: "잔여 예상 기준은 수주 고객 화면의 사용 진단과 같이 바뀝니다.",
  },
  grow: {
    name: "추가 계약 기회", desc: (r) => `계약 종료가 ${r.renewalDays}일 안이거나 크레딧이 먼저 떨어질 수주 고객`,
    title: "추가 계약 후보", search: "고객명 · Client ID 검색", todo: "미컨택",
    // 초과사용 %p 한 값이 과소사용도 같이 정한다 — 이탈 방지 탭의 근거(과소사용)와 수주 고객 화면이 같이 바뀐다.
    note: "부족 예상 · 초과사용 기준은 수주 고객 화면의 사용 진단과 같이 바뀝니다. 초과사용 %p 는 과소사용도 같이 정해서 이탈 방지 탭의 근거도 바뀝니다.",
  },
};
const TABS: Tab[] = ["up", "churn", "grow"];

const num = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v.toLocaleString("en-US"));
const TIER: Record<string, string> = { pro: "Pro", free: "Free", starter: "Starter", creator: "Creator", team: "Team", business: "Business", flex: "Flex", enterprise: "Enterprise" };

function PlanTag({ plan }: { plan: string | null }) {
  if (!plan) return <span className="mut">—</span>;
  const tone = /^(enterprise|business)/i.test(plan) ? "ent" : plan.toLowerCase() === "pro" ? "pro" : "free";
  return <span className={`plan ${tone}`}>{plan}</span>;
}
const Sg = ({ tone, children, title }: { tone?: string; children: React.ReactNode; title?: string }) =>
  <span className={`sg${tone ? ` ${tone}` : ""}`} title={title}>{children}</span>;

/** 이탈 방지의 근거 — 잔여 예상 · 무활동(또는 기록 없음) · 과소사용. */
export function ChurnWhy({ row, idleDays }: { row: ContractRow; idleDays: number }) {
  return (
    <span className="sgs">
      <Sg tone="warn" title={row.forecastDetail ?? undefined}>잔여 예상</Sg>
      {(row.daysIdle === null || row.daysIdle >= idleDays) &&
        <Sg tone="risk">무활동 {row.daysIdle === null ? "기록 없음" : `${row.daysIdle}일`}</Sg>}
      {row.level === "과소사용" && <Sg tone="warn" title={row.levelDetail ?? undefined}>과소사용</Sg>}
    </span>
  );
}
/** 추가 계약의 근거 — 계약 종료 D-N · 부족 예상 · 초과사용. */
export function GrowWhy({ row, renewalDays }: { row: ContractRow; renewalDays: number }) {
  return (
    <span className="sgs">
      {row.daysLeft !== null && row.daysLeft >= 0 && row.daysLeft <= renewalDays && <Sg tone="up">계약 종료 D-{row.daysLeft}</Sg>}
      {row.forecast === "부족 예상" && <Sg tone="up" title={row.forecastDetail ?? undefined}>부족 예상</Sg>}
      {row.level === "초과사용" && <Sg tone="up" title={row.levelDetail ?? undefined}>초과사용</Sg>}
    </span>
  );
}
const RISK = { high: <Sg tone="risk">높음</Sg>, mid: <Sg tone="warn">주의</Sg> };
const PRIORITY = { p1: <Sg tone="risk">높음</Sg>, p2: <Sg tone="up">보통</Sg>, p3: <Sg>낮음</Sg> };

export function SalesInsightsView({ usage, won, rules, onSaveRules }: Props) {
  const sales = useSalesInsight<SalesData>(usage.source);
  // 수주 고객 목록이 쓰는 그 요약 색인 — 활성 계약의 스페이스만 고른다(`useUsageIndex`).
  const spaces = useMemo(() => spacesOfRows(won?.rows), [won?.rows]);
  const summary = useUsageIndex(usage.source, spaces);
  const ruleValues = useMemo(() => rulesFrom(rules.list), [rules.list]);
  // 우리 수주 고객의 space_seq — 활성 계약만이 아니라 모든 계약. 업셀링 후보에서 뺀다.
  const ours = useMemo(() => {
    const set = new Set<number>();
    for (const row of won?.rows ?? []) {
      for (const c of [row.active, ...(row.contracts ?? [])]) for (const s of parseSpaceSeqs(c?.space_seq)) set.add(s);
    }
    return set;
  }, [won]);

  const asOf = summary.index?.snapshotAt ?? sales.data?.snapshot_at.slice(0, 10) ?? null;
  const up = useMemo<UpRow[] | null>(
    () => (ruleValues && sales.data && asOf && won ? upsellRows(sales.data.data.spaces, ours, ruleValues, asOf) : null),
    [ruleValues, sales.data, asOf, ours, won]);
  const judged = useMemo(
    () => (ruleValues && summary.index && won ? contractRows(won.rows, summary.index, ruleValues) : null),
    [ruleValues, summary.index, won]);

  const [tab, setTab] = useState<Tab>("up");
  const [query, setQuery] = useState("");
  const [toast, setToast] = useState<string | null>(null);
  const say = (message: string) => { setToast(message); window.setTimeout(() => setToast(null), 1800); };

  const lists: Record<Tab, (UpRow | ChurnRow | GrowRow)[] | null> = {
    up, churn: judged?.churn ?? null, grow: judged?.grow ?? null,
  };
  /** 기준을 이 값으로 바꾸면 그 카드에 몇 곳이 서나 — 확인 창의 「바꾼 뒤」. 이 브라우저에서만 센다. */
  const countWith = (which: Tab) => (values: Record<string, number>) => {
    if (!ruleValues || !rules.list) return null;
    const next = rulesFrom(rules.list.map((r) => (r.key in values ? { ...r, value: values[r.key] } : r)));
    if (!next) return null;
    if (which === "up") return sales.data && asOf && won ? upsellRows(sales.data.data.spaces, ours, next, asOf).length : null;
    if (!summary.index || !won) return null;
    const after = contractRows(won.rows, summary.index, next);
    return which === "churn" ? after.churn.length : after.grow.length;
  };

  const card = CARDS[tab];
  const list = lists[tab];
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return list ?? [];
    return (list ?? []).filter((row) => ("space_seq" in row
      ? String(row.space_seq).includes(q)
      : `${row.company} ${row.clientId}`.toLowerCase().includes(q)));
  }, [list, query]);

  const snapshot = sales.data?.snapshot_at ?? null;
  const notConnected = usage.configured === false || !!usage.problem;
  const problem = sales.problem ?? summary.problem ?? (rules.problem && `판정 기준을 못 읽었습니다: ${rules.problem}`);
  const blocked = notConnected || !!problem;
  const loading = !blocked && (!list || usage.busy);
  const creditsFrom = summary.index?.creditsFrom?.slice(0, 7) ?? null;
  const tabRules = (rules.list ?? []).filter((r) => r.tab === tab);

  return (
    <div className="ins">
      <div className="head">
        <h1>영업 인사이트</h1>
        {snapshot && <div className="meta">데이터 기준 <b className="num">{stamp(snapshot)}</b> · 최근 6개월</div>}
      </div>

      {notConnected && (
        <div className="notice">
          <strong>사용 데이터가 연결돼 있지 않습니다.</strong> 이 화면의 값은 전부 매일 가공된 스냅샷을 브라우저가
          GitHub 에서 직접 받아 그립니다 — 서버는 그 데이터를 모릅니다. <Link to="/data">「데이터 분석」</Link> 화면을 보세요.
          {usage.problem && <> · 사유: {usage.problem}</>}
        </div>
      )}
      {problem && <div className="notice">가져오지 못했습니다: {problem}</div>}

      <section className="points" role="tablist" aria-label="영업 인사이트">
        {TABS.map((key) => {
          const c = CARDS[key];
          const list = lists[key];
          return (
            <button key={key} type="button" role="tab" aria-selected={tab === key}
                    className={`pt${tab === key ? " on" : ""}`}
                    onClick={() => { setTab(key); setQuery(""); }}>
              <span className="lb">{c.name}</span>
              <span className="ds">{ruleValues ? c.desc(ruleValues) : " "}</span>
              <span className="ft">
                <span className="v num">{list ? num(list.length) : "—"}<span className="unit">{c.todo}</span></span>
              </span>
            </button>
          );
        })}
      </section>

      <section className="icard">
        <div className="card-h">
          <h2>{card.title}</h2>
          <div className="tools">
            <input className="isearch" type="search" aria-label="검색" placeholder={card.search}
                   value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
        </div>
        {tabRules.length > 0 && (
          <RuleLine key={tab} tab={tab} title={card.title} rules={tabRules} note={card.note}
                    count={lists[tab]?.length ?? null} countWith={countWith(tab)}
                    onSaveRules={onSaveRules} onSaved={say} />
        )}
        <div className="tbl-wrap">
          <table>
            <thead>
              {tab === "up" && <tr><th>SPACE</th><th>플랜</th><th className="r">30일 크레딧</th><th>신호</th></tr>}
              {tab === "churn" && <tr><th>고객</th><th>플랜</th><th>위험도</th><th>근거</th><th>마지막 활동</th></tr>}
              {tab === "grow" && <tr><th>고객</th><th>플랜</th><th>우선도</th><th>근거</th><th className="r">잔여 크레딧</th><th>계약 종료</th></tr>}
            </thead>
            <tbody>
              {blocked && <tr><td colSpan={6} className="iempty">데이터를 받지 못해 판정하지 않았습니다</td></tr>}
              {loading && <tr><td colSpan={6} className="iempty">불러오는 중…</td></tr>}
              {!blocked && !loading && !shown.length && <tr><td colSpan={6} className="iempty">해당하는 항목이 없습니다</td></tr>}
              {!blocked && !loading && ruleValues && shown.map((row) => {
                if ("space_seq" in row) {
                  return (
                    <tr key={row.key}>
                      <td><span className="sp num" title="허브스팟 연락처의 「space seq」 칸으로 사람에게 이어집니다">{row.space_seq}</span>
                        {row.isNew && <span className="new" title="첫 작업이 30일 안인 스페이스">NEW</span>}</td>
                      <td><PlanTag plan={TIER[row.tier] ?? row.tier} /></td>
                      <td className="r num"><span className="big">{num(row.credits30d)}</span></td>
                      <td><Sg tone="up" title={`Pro 플랜인데 30일에 ${num(ruleValues.upsellCredits30d)} 크레딧 이상`}>셀프서브 대량 사용</Sg></td>
                    </tr>
                  );
                }
                const who = <td><span className="cname">{row.company}</span><span className="cid num">{row.clientId}</span></td>;
                if ("risk" in row) {
                  return (
                    <tr key={row.key}>
                      {who}<td><PlanTag plan={row.plan} /></td><td>{RISK[row.risk]}</td>
                      <td><ChurnWhy row={row} idleDays={ruleValues.churnIdleDays} /></td>
                      <td className="num">{row.lastActivity ?? <span className="mut">없음</span>}</td>
                    </tr>
                  );
                }
                return (
                  <tr key={row.key}>
                    {who}<td><PlanTag plan={row.plan} /></td><td>{PRIORITY[row.priority]}</td>
                    <td><GrowWhy row={row} renewalDays={ruleValues.renewalDays} /></td>
                    <td className="r num">{num(row.remaining)}<span className="unit">/ {num(row.credits)}</span></td>
                    <td className="num">{row.end ?? "—"}{row.daysLeft !== null && <span className="mut"> D-{row.daysLeft}</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="tfoot">
          <span>{list ? `${num(shown.length)}곳` : ""}</span>
          {tab === "up" && ruleValues && upsellMaybeCut(sales.data?.data.spaces, ruleValues) && (
            <span className="warnline">
              가공 데이터가 후보를 {CANDIDATE_CAP}곳까지만 담습니다 — 지금 기준에서 30일 크레딧이 낮은 쪽 후보가 빠졌을 수 있습니다
            </span>
          )}
          {tab !== "up" && judged && (
            <span>
              {[
                judged.undetermined > 0 &&
                  `판정 불가 ${judged.undetermined}곳 · 계약 크레딧·기간 없음 또는 ${creditsFrom ?? "스냅샷 기록"} 이전 시작`,
                judged.unlinked > 0 && `Space 미연결 ${judged.unlinked}곳`,
                judged.unknown > 0 && `스냅샷에 없음 ${judged.unknown}곳`,
              ].filter(Boolean).join(" · ")}
            </span>
          )}
        </div>
      </section>

      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}
