import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { InsightDrafts } from "../../lib/insightDrafts";
import type { InsightMarks, MarkChange, MarkTarget } from "../../lib/insightMarks";
import type { InsightRule } from "../../lib/insightRules";
import { stamp, useSalesInsight } from "../../lib/usageData";
import type { UsageSource } from "../../lib/usageSource";
import { parseSpaceSeqs, rulesFrom, type Rules } from "../won/usage";
import { spacesOfRows, useUsageIndex } from "../won/useUsage";
import type { ListData } from "../won/shared";
import { ChurnWhy, GrowWhy, PlanTag, PriorityTag, RiskTag, Sg, TIER, num } from "./bits";
import { Drawer, type DrawerItem } from "./Drawer";
import { MailDraft, type MailMode } from "./MailDraft";
import { templatesFrom, type Lang } from "./mail";
import {
  contractRows, countByStatus, listChurn, listGrow, listUp, markId, upsellMaybeCut, upsellRows, CANDIDATE_CAP, TODO,
  type ChurnRow, type GrowRow, type Listed, type SalesData, type Tab, type UpRow,
} from "./judge";
import { RuleLine } from "./RuleLine";

/** 영업 인사이트 — **안쪽 겹** (2026-10-15 운영자 지시: 목업 `영업인사이트.html` 대로 개편).
 *
 *  카드 셋이 곧 탭입니다 — 업셀링(우리 고객이 아닌 Pro 스페이스 중 30일 크레딧이 기준 이상) · 이탈 방지(계약
 *  크레딧이 남을 전망인 수주 고객) · 추가 계약 기회(계약 종료가 가깝거나 크레딧이 먼저 떨어질 수주 고객). 판정은
 *  `judge.ts` 이고, 수주 고객 쪽 판정은 수주 고객 「사용 진단」과 같은 함수 · 같은 기준(서버에 저장)입니다.
 *
 *  줄마다 상태(미컨택 → 컨택 완료 → 전환 / 제외 …)와 메모가 붙고 팀이 같이 봅니다 — 서버에 사는 것은 사람이 누른
 *  줄의 열쇠 · 상태 · 메모뿐입니다(업셀링 줄의 번호가 세 번째 예외, 2026-10-15 A안). 처리한 줄은 지금 기준에 안
 *  맞아도 그 칸에 남고, 카드의 큰 숫자는 아직 손대지 않은 후보 수입니다.
 *
 *  값은 전부 매일 가공된 스냅샷(`sales.json` · `summary.json`)을 브라우저가 GitHub 에서 직접 받은 것이고, 이 파일은
 *  서버를 못 부릅니다(`tests/test_usage_data_stays_off_server.py` 의 `DATA_SIDE` — 이 폴더 전체). 서버에서 오는
 *  것(수주 고객 목록 · 판정 기준 · 상태 · 사용 데이터의 열쇠)은 바깥 겹(`screens/SalesInsights.tsx`)이 받아 내려 주고,
 *  서버로 가는 것은 사람이 「기준 수정」에 적은 숫자(`RuleLine.tsx`)와 상세 패널에서 누른 상태 · 메모(`Drawer.tsx`)뿐입니다.
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
  /** `useInsightMarks()` 의 답 그대로 — 줄마다의 상태 · 메모 · 기록과 카드마다의 상태 이름. */
  marks: { data: InsightMarks | null; problem: string | null; busy: boolean };
  /** `useInsightDrafts()` 의 답 그대로 — 메일 초안의 기본 문구(서버에 저장)와 자리표시 안내. */
  drafts: { data: InsightDrafts | null; problem: string | null; busy: boolean };
  onSaveRules: (tab: Tab, values: Record<string, number>) => Promise<unknown>;
  onMark: (target: MarkTarget, change: MarkChange) => Promise<unknown>;
  /** 메일 초안을 복사했다는 기록 — 그 줄의 열쇠와 초안의 제목만. 메일은 보내지 않는다(복사만). */
  onCopied: (target: MarkTarget, subject: string) => Promise<unknown>;
  /** 「메일 초안」 창의 「기본 문구 수정」 저장 — 사람이 적은 글. */
  onSaveDrafts: (tab: Tab, values: Record<string, string>) => Promise<unknown>;
};

type Card = { name: string; desc: (r: Rules) => string; title: string; search: string; note: string };
const CARDS: Record<Tab, Card> = {
  up: { name: "업셀링", desc: () => "PRO 플랜에서 B2B로 올릴 만한 스페이스", title: "업셀링 후보", search: "Space 번호 검색", note: "" },
  churn: {
    name: "이탈 방지", desc: () => "계약 크레딧이 남을 전망인 수주 고객", title: "이탈 위험 고객", search: "고객명 · Client ID 검색",
    note: "잔여 예상 기준은 수주 고객 화면의 사용 진단과 같이 바뀝니다.",
  },
  grow: {
    name: "추가 계약 기회", desc: (r) => `계약 종료가 ${r.renewalDays}일 안이거나 크레딧이 먼저 떨어질 수주 고객`,
    title: "추가 계약 후보", search: "고객명 · Client ID 검색",
    // 초과사용 %p 한 값이 과소사용도 같이 정한다 — 이탈 방지 탭의 근거(과소사용)와 수주 고객 화면이 같이 바뀐다.
    note: "부족 예상 · 초과사용 기준은 수주 고객 화면의 사용 진단과 같이 바뀝니다. 초과사용 %p 는 과소사용도 같이 정해서 이탈 방지 탭의 근거도 바뀝니다.",
  },
};
const TABS: Tab[] = ["up", "churn", "grow"];
type AnyRow = Listed<UpRow> | Listed<ChurnRow> | Listed<GrowRow>;

export function SalesInsightsView({ usage, won, rules, marks, drafts, onSaveRules, onMark, onCopied, onSaveDrafts }: Props) {
  const sales = useSalesInsight<SalesData>(usage.source);
  // 수주 고객 목록이 쓰는 그 요약 색인 — 활성 계약의 스페이스만 고른다(`useUsageIndex`).
  const spaces = useMemo(() => spacesOfRows(won?.rows), [won?.rows]);
  const summary = useUsageIndex(usage.source, spaces);
  const ruleValues = useMemo(() => rulesFrom(rules.list), [rules.list]);
  // 메일 초안의 기본 문구 — 못 받았으면 null 이고, 그동안 「메일 초안」 단추가 잠긴다(문구 없이 빈 창을 열지 않는다).
  const templates = useMemo(() => templatesFrom(drafts.data?.parts), [drafts.data]);
  const markList = marks.data?.marks ?? null;
  // 우리 수주 고객의 space_seq — 활성 계약만이 아니라 모든 계약. 업셀링 후보에서 뺀다.
  const ours = useMemo(() => {
    const set = new Set<number>();
    for (const row of won?.rows ?? []) {
      for (const c of [row.active, ...(row.contracts ?? [])]) for (const s of parseSpaceSeqs(c?.space_seq)) set.add(s);
    }
    return set;
  }, [won]);

  const asOf = summary.index?.snapshotAt ?? sales.data?.snapshot_at.slice(0, 10) ?? null;
  const salesSpaces = sales.data?.data.spaces ?? null;
  const upList = useMemo<Listed<UpRow>[] | null>(() => {
    if (!ruleValues || !salesSpaces || !asOf || !won || !markList) return null;
    return listUp(upsellRows(salesSpaces, ours, ruleValues, asOf), salesSpaces, markList, asOf);
  }, [ruleValues, salesSpaces, asOf, ours, won, markList]);
  const judged = useMemo(
    () => (ruleValues && summary.index && won ? contractRows(won.rows, summary.index, ruleValues) : null),
    [ruleValues, summary.index, won]);
  const churnList = useMemo<Listed<ChurnRow>[] | null>(
    () => (judged && ruleValues && summary.index && markList
      ? listChurn(judged.churn, won?.rows, summary.index, ruleValues, markList) : null),
    [judged, ruleValues, summary.index, won, markList]);
  const growList = useMemo<Listed<GrowRow>[] | null>(
    () => (judged && ruleValues && summary.index && markList
      ? listGrow(judged.grow, won?.rows, summary.index, ruleValues, markList) : null),
    [judged, ruleValues, summary.index, won, markList]);
  const lists: Record<Tab, AnyRow[] | null> = { up: upList, churn: churnList, grow: growList };
  const counts = useMemo(() => ({
    up: upList ? countByStatus(upList) : null,
    churn: churnList ? countByStatus(churnList) : null,
    grow: growList ? countByStatus(growList) : null,
  }), [upList, churnList, growList]);

  const [tab, setTab] = useState<Tab>("up");
  const [status, setStatus] = useState(TODO);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [toast, setToast] = useState<string | null>(null);
  // 메일 초안 창과 그 언어 — 언어는 창을 닫았다 열어도 남는다(목업).
  const [mail, setMail] = useState<MailMode | null>(null);
  const [lang, setLang] = useState<Lang>("ko");
  // 기본 문구를 저장하면 창을 새 문구로 다시 연다 — 열쇠가 바뀌면 창이 처음부터 다시 그려진다.
  const [mailVersion, setMailVersion] = useState(0);
  const say = (message: string) => { setToast(message); window.setTimeout(() => setToast(null), 1800); };
  // Esc 는 위에 뜬 것부터 닫는다 — 메일 초안 창, 그다음 상세 패널(목업). 그 두 파일에는 스스로 도는 코드를 두지
  // 않아서 여기서 듣는다.
  useEffect(() => {
    if (open === null && mail === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (mail) setMail(null); else setOpen(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, mail]);

  /** 기준을 이 값으로 바꾸면 그 카드에 아직 손대지 않은 후보가 몇 곳이 되나 — 확인 창의 「바꾼 뒤」. 이 브라우저에서만 센다. */
  const countWith = (which: Tab) => (values: Record<string, number>) => {
    if (!rules.list || !markList) return null;
    const next = rulesFrom(rules.list.map((r) => (r.key in values ? { ...r, value: values[r.key] } : r)));
    if (!next) return null;
    const handled = new Set(markList.filter((m) => m.tab === which && m.status !== TODO).map(markId));
    if (which === "up") {
      return salesSpaces && asOf && won ? upsellRows(salesSpaces, ours, next, asOf).filter((r) => !handled.has(r.key)).length : null;
    }
    if (!summary.index || !won) return null;
    const after = contractRows(won.rows, summary.index, next);
    return (which === "churn" ? after.churn : after.grow).filter((r) => !handled.has(r.key)).length;
  };

  const card = CARDS[tab];
  const statuses = marks.data?.statuses[tab] ?? [];
  const list = lists[tab];
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (list ?? []).filter((row) => row.status === status && (status !== TODO || row.candidate)
      && (!q || ("space_seq" in row ? String(row.space_seq).includes(q) : `${row.company} ${row.clientId}`.toLowerCase().includes(q))));
  }, [list, status, query]);
  const shownKeys = shown.map((r) => r.key);
  const picked = shownKeys.filter((k) => selected.has(k));

  const choose = (next: Tab) => {
    setTab(next); setStatus(TODO); setQuery(""); setSelected(new Set()); setOpen(null); setMail(null);
  };
  const toggle = (key: number) => {
    const next = new Set(selected);
    if (next.has(key)) next.delete(key); else next.add(key);
    setSelected(next);
  };

  const item: DrawerItem | null = (() => {
    const row = open === null ? undefined : list?.find((r) => r.key === open);
    if (!row) return null;
    return { tab, row } as DrawerItem;
  })();
  const itemLog = item && marks.data
    ? marks.data.log.filter((line) => line.tab === tab && markId(line) === item.row.key) : [];
  const itemMemo = item && markList
    ? markList.find((m) => m.tab === tab && markId(m) === item.row.key)?.memo ?? null : null;

  const snapshot = sales.data?.snapshot_at ?? null;
  const notConnected = usage.configured === false || !!usage.problem;
  const problem = sales.problem ?? summary.problem
    ?? (rules.problem && `판정 기준을 못 읽었습니다: ${rules.problem}`)
    ?? (marks.problem && `상태를 못 읽었습니다: ${marks.problem}`)
    ?? (drafts.problem && `메일 초안 문구를 못 읽었습니다: ${drafts.problem}`);
  const blocked = notConnected || !!problem;
  const loading = !blocked && (!list || usage.busy);
  const creditsFrom = summary.index?.creditsFrom?.slice(0, 7) ?? null;
  const tabRules = (rules.list ?? []).filter((r) => r.tab === tab);
  const cols = tab === "up" ? 5 : tab === "churn" ? 6 : 7;

  return (
    <div className="ins">
      <div className="head">
        {/* 제목 글자는 다른 화면처럼 숨긴다(console.css 의 `.page-title`) — 왼쪽 메뉴의 강조와 같은 글자다. */}
        <h1 className="page-title">영업 인사이트</h1>
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
          const todo = counts[key];
          return (
            <div key={key} role="tab" tabIndex={0} aria-selected={tab === key}
                 className={`pt${tab === key ? " on" : ""}`} onClick={() => choose(key)}
                 onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(key); } }}>
              <span className="lb">{c.name}</span>
              <span className="ds">{ruleValues ? c.desc(ruleValues) : " "}</span>
              <span className="ft">
                <span className="v num">{todo ? num(todo[TODO] ?? 0) : "—"}
                  <span className="unit">{marks.data?.statuses[key][0]?.label ?? ""}</span></span>
                <button type="button" className="mbtn" disabled={!ruleValues || !templates}
                        onClick={(e) => { e.stopPropagation(); setMail({ kind: "tab", tab: key, title: c.name }); }}>
                  메일 초안
                </button>
              </span>
            </div>
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
                    count={counts[tab]?.[TODO] ?? (counts[tab] ? 0 : null)} countWith={countWith(tab)}
                    onSaveRules={onSaveRules} onSaved={say} />
        )}
        {statuses.length > 0 && (
          <div className="pills">
            {statuses.map((s) => (
              <button key={s.key} type="button" className={`ipill${status === s.key ? " on" : ""}`}
                      onClick={() => { setStatus(s.key); setSelected(new Set()); }}>
                {s.label}<b className="num">{num(counts[tab]?.[s.key] ?? 0)}</b>
              </button>
            ))}
          </div>
        )}
        {picked.length > 0 && (
          <div className="selbar">
            <span><b className="num">{picked.length}곳</b> 선택됨</span>
            <div className="acts">
              <button type="button" className="ibtn" onClick={() => setSelected(new Set())}>선택 해제</button>
              <button type="button" className="ibtn pri" disabled={!templates}
                      onClick={() => setMail({ kind: "bulk", tab, rows: shown.filter((r) => selected.has(r.key)) })}>
                선택한 {picked.length}곳 메일 초안
              </button>
            </div>
          </div>
        )}
        <div className="tbl-wrap">
          <table>
            <thead>
              <tr>
                <th className="ck">
                  <input type="checkbox" aria-label="전체 선택" disabled={!shown.length}
                         checked={shown.length > 0 && picked.length === shown.length}
                         ref={(el) => { if (el) el.indeterminate = picked.length > 0 && picked.length < shown.length; }}
                         onChange={(e) => setSelected(e.target.checked ? new Set(shownKeys) : new Set())} />
                </th>
                {tab === "up" && <><th>SPACE</th><th>플랜</th><th className="r">30일 크레딧</th><th>신호</th></>}
                {tab === "churn" && <><th>고객</th><th>플랜</th><th>위험도</th><th>근거</th><th>마지막 활동</th></>}
                {tab === "grow" && <><th>고객</th><th>플랜</th><th>우선도</th><th>근거</th><th className="r">잔여 크레딧</th><th>계약 종료</th></>}
              </tr>
            </thead>
            <tbody>
              {blocked && <tr><td colSpan={cols} className="iempty">데이터를 받지 못해 판정하지 않았습니다</td></tr>}
              {loading && <tr><td colSpan={cols} className="iempty">불러오는 중…</td></tr>}
              {!blocked && !loading && !shown.length && <tr><td colSpan={cols} className="iempty">해당하는 항목이 없습니다</td></tr>}
              {!blocked && !loading && ruleValues && shown.map((row) => {
                const pick = (
                  <td className="ck" onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" aria-label="선택" checked={selected.has(row.key)} onChange={() => toggle(row.key)} />
                  </td>
                );
                const props = {
                  tabIndex: 0, className: open === row.key ? "cur" : undefined,
                  onClick: () => setOpen(row.key),
                  onKeyDown: (e: React.KeyboardEvent) => { if (e.key === "Enter") setOpen(row.key); },
                };
                if ("space_seq" in row) {
                  return (
                    <tr key={row.key} {...props}>
                      {pick}
                      <td><span className="sp num" title="허브스팟 연락처의 「space seq」 칸으로 사람에게 이어집니다">{row.space_seq}</span>
                        {row.isNew && <span className="new" title="첫 작업이 30일 안인 스페이스">NEW</span>}</td>
                      <td><PlanTag plan={row.tier ? TIER[row.tier] ?? row.tier : null} /></td>
                      <td className="r num"><span className="big">{num(row.credits30d)}</span></td>
                      <td>{row.candidate
                        ? <Sg tone="up" title={`Pro 플랜인데 30일에 ${num(ruleValues.upsellCredits30d)} 크레딧 이상`}>셀프서브 대량 사용</Sg>
                        : <span className="mut" title="처리해 둔 줄 — 지금 기준에는 안 맞습니다">{row.gone ? "오늘 가공 데이터에 없음" : "기준 밖"}</span>}</td>
                    </tr>
                  );
                }
                const who = <td><span className="cname">{row.company}</span>{row.clientId > 0 && <span className="cid num">{row.clientId}</span>}</td>;
                if ("risk" in row) {
                  return (
                    <tr key={row.key} {...props}>
                      {pick}{who}<td><PlanTag plan={row.plan} /></td><td><RiskTag risk={row.risk} /></td>
                      <td><ChurnWhy row={row} idleDays={ruleValues.churnIdleDays} /></td>
                      <td className="num">{row.lastActivity ?? <span className="mut">없음</span>}</td>
                    </tr>
                  );
                }
                return (
                  <tr key={row.key} {...props}>
                    {pick}{who}<td><PlanTag plan={row.plan} /></td><td><PriorityTag priority={row.priority} /></td>
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
          {tab === "up" && ruleValues && upsellMaybeCut(salesSpaces, ruleValues) && (
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

      {item && ruleValues && (
        <>
          <div className="scrim" onClick={() => setOpen(null)} />
          <Drawer key={`${tab}:${item.row.key}`} item={item} statuses={statuses} memo={itemMemo} log={itemLog}
                  rules={ruleValues} onMark={onMark} onSaved={say} onClose={() => setOpen(null)} draftReady={!!templates}
                  onDraft={() => setMail({ kind: "row", tab, row: item.row, email: "space_seq" in item.row ? null : item.row.email })} />
        </>
      )}
      {mail && ruleValues && templates && drafts.data && (
        // 언어를 바꾸면 그 언어의 문구로 다시 연다(목업) — 고쳐 둔 글자는 그 언어의 것이라 같이 안 넘어간다.
        <MailDraft key={`${lang}:${mail.kind}:${mailVersion}`} mode={mail} tabName={CARDS[mail.tab].name} lang={lang}
                   rules={ruleValues} templates={templates} drafts={drafts.data} onLang={setLang}
                   onCopied={onCopied} onSaveDrafts={onSaveDrafts} onReload={() => setMailVersion((v) => v + 1)}
                   onSaid={say} onClose={() => setMail(null)} />
      )}
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}
