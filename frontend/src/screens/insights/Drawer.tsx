// 영업 인사이트의 상세 패널 — 한 줄의 진행(상태 · 메모) · 사용 현황 · 기록 (목업 `영업인사이트.html` 의 openDrawer).
//
// **이 파일이 줄의 상태 · 메모를 밖으로 내보내는 유일한 자리입니다** — 상태 고르개의 `onChange`, 상태 버튼과
// 「메모 저장」의 `onClick` 안의 `onMark(…)` 이고, 스스로 도는 것(효과 훅 · 타이머)이 없습니다. 내보내는 것은 그 줄의
// 열쇠(업셀링은 space_seq, 나머지는 계약)와 사람이 고른 상태 · 적은 메모뿐입니다 — 업셀링 줄의 번호가 서버에 남는 것이
// 「사용 데이터는 서버로 안 간다」의 세 번째 예외이고(2026-10-15 A안), 사용 현황의 숫자는 이 패널에서 그려지기만 합니다
// (`tests/test_usage_data_stays_off_server.py`).
import { useState } from "react";
import { Link } from "react-router-dom";
import type { InsightLogLine, InsightStatus, MarkChange, MarkTarget } from "../../lib/insightMarks";
import { pairName, type Rules } from "../won/usage";
import { ChurnWhy, GrowWhy, PlanTag, PriorityTag, RiskTag, Sg, TIER, mmdd, num, pct } from "./bits";
import type { ChurnRow, GrowRow, Listed, UpRow } from "./judge";

export type DrawerItem =
  | { tab: "up"; row: Listed<UpRow> }
  | { tab: "churn"; row: Listed<ChurnRow> }
  | { tab: "grow"; row: Listed<GrowRow> };

/** 「미컨택으로」 · 「미조치로」 — 받침이 있으면(ㄹ 받침 빼고) 「으로」. */
function ro(word: string) {
  const code = word.charCodeAt(word.length - 1) - 0xac00;
  if (code < 0 || code > 11171) return "로";
  const jong = code % 28;
  return jong === 0 || jong === 8 ? "로" : "으로";
}

/** 지금 상태에서 누를 수 있는 것(목업의 acts) — 상태는 서버가 준 순서(미컨택 · 컨택 완료 · 전환/정상화/계약 · 제외)다. */
export function actsFor(statuses: InsightStatus[], current: string): InsightStatus[] {
  const [todo, done, closed, drop] = statuses;
  if (!todo || !done || !closed || !drop) return [];
  if (current === todo.key) return [{ key: drop.key, label: drop.label }, { key: done.key, label: done.label }];
  if (current === done.key) return [{ key: drop.key, label: drop.label }, { key: closed.key, label: `${closed.label} 처리` }];
  return [{ key: todo.key, label: `${todo.label}${ro(todo.label)} 되돌리기` }];
}

export const nameOf = (item: DrawerItem) => (item.tab === "up" ? `Space ${item.row.space_seq}` : item.row.company);

export function Drawer({ item, statuses, memo, log, rules, onMark, onSaved, onDraft, draftReady, onClose }: {
  item: DrawerItem;
  statuses: InsightStatus[];
  /** 저장된 메모 */
  memo: string | null;
  /** 이 줄의 기록 — 최근 것부터 */
  log: InsightLogLine[];
  rules: Rules;
  onMark: (target: MarkTarget, change: MarkChange) => Promise<unknown>;
  onSaved: (message: string) => void;
  /** 이 줄의 메일 초안 창을 연다 — 복사와 그 기록은 창(`MailDraft.tsx`)이 한다. */
  onDraft: () => void;
  /** 메일 초안의 기본 문구를 받았나 — 못 받았으면 「메일 초안」이 잠긴다. */
  draftReady: boolean;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(memo ?? "");
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState("");
  const row = item.row;
  const target: MarkTarget = item.tab === "up"
    ? { tab: "up", space_seq: item.row.space_seq } : { tab: item.tab, contract_id: item.row.contractId };
  const label = (key: string) => statuses.find((s) => s.key === key)?.label ?? key;
  const done = (message: string) => () => { setSaving(null); setError(""); onSaved(message); };
  const failed = (reason: unknown) => { setSaving(null); setError(`저장하지 못했습니다: ${String(reason)}`); };
  const busy = saving !== null;

  const kv: [string, string][] = item.tab === "up" ? [
    ["30일 크레딧", num(item.row.credits30d)],
    ["내보내기 30일 / 6개월", `${num(item.row.exports30d)} / ${num(item.row.exports6m)}`],
    ["사용자 수", num(item.row.users)],
    ["마지막 작업", item.row.lastJob ?? "—"],
    ["주 언어쌍", item.row.topPair ? pairName(item.row.topPair) : "—"],
  ] : [
    ["사용 수준", item.row.level ?? "—"],
    ["크레딧 전망", item.row.forecast ?? "—"],
    ["누적 소진율", pct(item.row.usedPct)],
    ["기간 경과율", pct(item.row.pacePct)],
    ...(item.tab === "churn" ? [
      ["30일 크레딧", num(item.row.credits30d)],
      ["마지막 활동", item.row.lastActivity ?? "없음"],
      ["품질", item.row.quality ? "실패 잦음" : "정상"],
    ] as [string, string][] : [
      ["잔여 크레딧", num(item.row.remaining)],
      ["30일 크레딧", num(item.row.credits30d)],
      ["Client ID", item.row.clientId ? String(item.row.clientId) : "—"],
    ] as [string, string][]),
    ["계약 종료", item.row.end ?? "—"],
  ];

  return (
    <aside className="drawer" aria-label="상세">
      <div className="dr-h">
        <div className="dr-top">
          <h3 className="num">{nameOf(item)}</h3>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {item.tab !== "up" && item.row.clientId > 0 &&
              <Link className="ibtn" to={`/won-customers/${item.row.clientId}`}>수주 고객 상세 열기</Link>}
            <button type="button" className="x" aria-label="닫기" onClick={onClose}>×</button>
          </div>
        </div>
        <div className="dr-sub">
          {item.tab === "up" ? (
            <>
              <PlanTag plan={item.row.tier ? TIER[item.row.tier] ?? item.row.tier : null} />
              {item.row.candidate && <Sg tone="up">셀프서브 대량 사용</Sg>}
              {item.row.isNew && <span className="new">NEW</span>}
              {item.row.gone && <span className="mut">오늘 가공 데이터에 없습니다</span>}
            </>
          ) : item.tab === "churn" ? (
            <><PlanTag plan={item.row.plan} /> <RiskTag risk={item.row.risk} long /> <ChurnWhy row={item.row} idleDays={rules.churnIdleDays} /></>
          ) : (
            <><PlanTag plan={item.row.plan} /> <PriorityTag priority={item.row.priority} long /> <GrowWhy row={item.row} renewalDays={rules.renewalDays} /></>
          )}
        </div>
      </div>

      <div className="dr-body">
        <div className="sec">
          <h4>진행</h4>
          <div className="ctl">
            <div className="fld full">
              <label htmlFor="ins-status">상태</label>
              <select id="ins-status" value={row.status} disabled={busy}
                      onChange={(e) => { setSaving("status"); onMark(target, { status: e.target.value }).then(done(`상태를 ${label(e.target.value)}(으)로 바꿨습니다`), failed); }}>
                {statuses.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
              </select>
            </div>
            <div className="fld full">
              <label htmlFor="ins-memo">메모</label>
              <textarea id="ins-memo" placeholder="사유, 다음 액션 등" value={draft} maxLength={2000}
                        onChange={(e) => setDraft(e.target.value)} />
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, alignItems: "center" }}>
                {draft.trim() !== (memo ?? "") && <span className="mut" style={{ fontSize: 12 }}>저장하지 않은 메모</span>}
                <button type="button" className="ibtn" disabled={busy || draft.trim() === (memo ?? "")}
                        onClick={() => { setSaving("memo"); onMark(target, { memo: draft }).then(done("메모를 저장했습니다"), failed); }}>
                  {saving === "memo" ? <><span className="spinner" role="status" /> 저장 중</> : "메모 저장"}
                </button>
              </div>
            </div>
          </div>
          {error && <div className="hint" role="alert">{error}</div>}
        </div>

        <div className="sec">
          <h4>사용 현황 <span>{item.tab === "up" ? "최근 6개월" : "수주 고객 「사용 진단」과 같은 계산"}</span></h4>
          <div className="kv num">
            {kv.map(([k, v]) => <div key={k}><small>{k}</small><b>{v}</b></div>)}
          </div>
        </div>

        <div className="sec">
          <h4>기록</h4>
          {log.length ? (
            <ul className="tl">
              {log.map((line, i) => (
                <li key={i}><span className="dt num">{mmdd(line.created_at)}</span>
                  <span>{line.detail}{line.actor ? <span className="mut"> · {line.actor}</span> : null}</span></li>
              ))}
            </ul>
          ) : <div className="iempty">기록 없음</div>}
        </div>
      </div>

      <div className="dr-f">
        <button type="button" className="ibtn sp-l" onClick={onDraft} disabled={!draftReady}>메일 초안</button>
        {actsFor(statuses, row.status).map((act, i, all) => (
          <button key={act.key} type="button" className={`ibtn${i === all.length - 1 && all.length > 1 ? " pri" : ""}`} disabled={busy}
                  onClick={() => { setSaving(act.key); onMark(target, { status: act.key }).then(done(`상태를 ${label(act.key)}(으)로 바꿨습니다`), failed); }}>
            {saving === act.key ? <><span className="spinner" role="status" /> {act.label}</> : act.label}
          </button>
        ))}
      </div>
    </aside>
  );
}
