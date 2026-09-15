// 목록과 상세가 같이 쓰는 사용 현황 조각들 — 도장·두 열·배지. 값은 전부 이 PC 의
// 에이전트에서 왔고, 여기서는 그리기만 합니다.
import { Link } from "react-router-dom";
import type { useAgent } from "../../lib/agent";
import { fmt } from "./shared";
import type { RowUsage, UsageIndex } from "./useUsage";
import { levelTone, type Alert, type Diagnosis, type Level } from "./usage";

const TONE: Record<string, string> = {
  ok: "st-live", warn: "st-setup", danger: "risk", reply: "plan-ent", neutral: "neutral",
};

export const Tone = ({ tone, children, title }: { tone: string; children: React.ReactNode; title?: string }) =>
  <span className={`tag ${TONE[tone] ?? "neutral"}`} title={title}>{children}</span>;

export function LevelTag({ level }: { level: Level }) {
  return <Tone tone={levelTone(level)}>{level}</Tone>;
}

/** 주의 배지 전부. 하나도 없으면 `정상` (상세 헤더) 또는 아무것도 안 그림 (목록). */
export function AlertTags({ alerts, emptyLabel }: { alerts: Alert[]; emptyLabel?: string }) {
  if (!alerts.length) return emptyLabel ? <Tone tone="ok">{emptyLabel}</Tone> : null;
  return <>{alerts.map((a) => <Tone key={a.key} tone="danger" title={a.detail}>{a.key}</Tone>)}</>;
}

/** 「사용량 기준 2026-09-14 05:00」 — 연결이 없으면 그 사실을. */
export function UsageStamp({ agent, index, problem, busy }: {
  agent: ReturnType<typeof useAgent>; index: UsageIndex | null; problem: string | null; busy: boolean;
}) {
  if (!agent.pair) {
    return (
      <Link to="/data" className="tag neutral" title="사용량 두 열은 이 PC 의 데이터 에이전트가 답합니다. 켜져 있지 않습니다.">
        사용량 · 에이전트 없음
      </Link>
    );
  }
  if (agent.problem || problem) {
    return (
      <Link to="/data" className="tag risk" title={agent.problem ?? problem ?? ""}>
        사용량 · 연결 실패
      </Link>
    );
  }
  if (busy || agent.busy || !index) return <span className="tag neutral">사용량 불러오는 중…</span>;
  const stale = agent.status?.as_of.stale;
  return (
    <span className={`tag ${stale ? "st-setup" : "neutral"}`}
          title={stale ? "스냅샷이 36시간 넘게 묵었습니다 — 에이전트에서 「지금 받기」" : "이 PC 의 스냅샷 시각"}>
      사용량 기준 <b style={{ marginLeft: 4 }}>{index.snapshotStamp}</b>{stale ? " · 낡음" : ""}
    </span>
  );
}

const idleWord = (d: number) => (d === 0 ? "오늘" : d === 1 ? "어제" : `${d}일 전`);

/** 목록의 두 열: 마지막 작업 · 사용 상태. */
export function UsageCells({ usage }: { usage: RowUsage }) {
  if (usage.kind === "no-agent") {
    return <><td className="muted">—</td><td className="muted">—</td></>;
  }
  if (usage.kind === "no-space") {
    return <><td className="muted">—</td>
      <td><span className="muted" title="계약의 Perso 계정·플랜에 Space ID 가 없습니다">미연결</span></td></>;
  }
  if (usage.kind === "unknown") {
    return <><td className="muted">—</td>
      <td><Tone tone="warn" title={`Space ${usage.spaces.join(", ")} 이(가) 스냅샷에 없습니다 — 번호를 확인하세요`}>스냅샷에 없음</Tone></td></>;
  }
  const d = usage.diagnosis;
  return (
    <>
      <td className="datecell">
        {d.lastActivity ? fmt(d.lastActivity) : <span className="muted">기록 없음</span>}
        {d.daysIdle !== null && (
          <span className={`sub ${d.idleTone === "danger" ? "over" : d.idleTone === "warn" ? "due" : ""}`}>
            {idleWord(d.daysIdle)}
          </span>
        )}
      </td>
      <td>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          {d.level !== "적정" && <LevelTag level={d.level} />}
          <AlertTags alerts={d.alerts} emptyLabel={d.level === "적정" ? "정상" : undefined} />
        </div>
      </td>
    </>
  );
}

/** 판정 근거 — 상세 헤더의 (i) 팝오버 내용. 규칙과 이 고객의 값을 나란히. */
export function DiagnosisBasis({ d, rule }: { d: Diagnosis; rule: typeof import("./usage").RULE }) {
  const row = (hit: boolean, key: string, ruleText: string, value: string) => (
    <div key={key} style={{ display: "flex", alignItems: "baseline", gap: 10, padding: "6px 0",
                            borderTop: "1px solid var(--line-soft)", opacity: hit ? 1 : 0.75, fontSize: 12.5 }}>
      <b style={{ color: hit ? "var(--red-fg)" : "var(--faint)", width: 10 }}>{hit ? "●" : "·"}</b>
      <span style={{ fontWeight: 600, minWidth: 100 }}>{key}</span>
      <span className="muted" style={{ flex: 1 }}>{ruleText}</span>
      <span style={{ fontWeight: 700, fontVariantNumeric: "tabular-nums", color: hit ? "var(--red-fg)" : "var(--muted)" }}>{value}</span>
    </div>
  );
  const gap = d.gap === null ? "—" : `${d.gap > 0 ? "+" : ""}${d.gap}%p`;
  return (
    <div className="panel" style={{ padding: "12px 16px" }}>
      <div className="sub-head"><span className="sub-title">사용 수준</span><span className="sub-count">하나만 적용 · 현재 <b>{d.level}</b></span></div>
      {row(d.level === "미사용", "미사용", "최근 30일 소진 0", d.level === "미사용" ? "0" : "소진 있음")}
      {row(d.level === "과소사용", "과소사용", `누적 소진율 − 계약 경과율 ≤ −${rule.gapPct}%p`, gap)}
      {row(d.level === "초과사용", "초과사용", `≥ +${rule.gapPct}%p`, gap)}
      {row(d.level === "적정", "적정", "위 어디에도 해당 없음", d.usedPct !== null && d.pacePct !== null ? `소진 ${d.usedPct}% / 경과 ${d.pacePct}%` : "—")}
      {d.partial && <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>{d.levelDetail}</div>}
      <div className="sub-head" style={{ marginTop: 14 }}><span className="sub-title">주의</span>
        <span className="sub-count">해당하면 모두 · 현재 <b>{d.alerts.length ? d.alerts.map((a) => a.key).join(", ") : "없음"}</b></span></div>
      {row(d.daysIdle !== null && d.daysIdle >= rule.idleDays, "N일 무활동", `마지막 작업 후 ${rule.idleDays}일 이상`,
        d.daysIdle === null ? "기록 없음" : idleWord(d.daysIdle))}
      {row(d.runwayMonths !== null && d.runwayMonths <= rule.runwayMonths, "크레딧 부족 예상", `잔여 ÷ 월평균 소진 ≤ ${rule.runwayMonths}개월치`,
        d.runwayMonths === null ? "—" : `${d.runwayMonths.toFixed(1)}개월치`)}
      {row(!!d.alerts.find((a) => a.key === "크레딧 잔여 예상"), "크레딧 잔여 예상", `계약 종료 시 잔여 > 계약 크레딧의 ${rule.leftoverPct}%`,
        d.projectedLeft === null ? "—" : Math.max(0, d.projectedLeft).toLocaleString())}
      {row(!!d.alerts.find((a) => a.key === "품질"), "품질", `실패율 ≥ 전사 평균 ${rule.failMult}배 또는 재작업률 ≥ ${rule.reworkPct}%`,
        [d.failRate !== null ? `실패 ${d.failRate.toFixed(1)}%` : null, d.reworkRate !== null ? `재작업 ${d.reworkRate.toFixed(0)}%` : null]
          .filter(Boolean).join(" · ") || "—")}
      <div className="muted" style={{ fontSize: 11.5, marginTop: 10 }}>임계값은 전부 임시입니다 — 운영하며 조정합니다.</div>
    </div>
  );
}
