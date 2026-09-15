// 목록과 상세가 같이 쓰는 사용 현황 조각들 — 도장·두 열·배지. 값은 전부 이 PC 의
// 에이전트에서 왔고, 여기서는 그리기만 합니다.
import { Link } from "react-router-dom";
import type { useAgent } from "../../lib/agent";
import { fmt } from "./shared";
import type { RowUsage, UsageIndex } from "./useUsage";
import { levelTone, type Alert, type Level } from "./usage";

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
  if (agent.outdated) {
    return (
      <Link to="/data" className="tag st-setup" title={`에이전트 ${agent.status?.version ?? "1.0"} — 이 콘솔은 더 새 버전이 필요합니다. 일부 값이 빕니다.`}>
        사용량 · 에이전트 업데이트 필요
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
