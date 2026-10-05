// 목록과 상세가 같이 쓰는 사용 현황 조각들 — 도장·두 열·배지. 값은 전부 가공된 스냅샷(브라우저가
// GitHub 에서 직접 받은 것)에서 왔고, 여기서는 그리기만 합니다.
import { Link } from "react-router-dom";
import { fmt } from "./shared";
import type { RowUsage, UsageIndex } from "./useUsage";
import { forecastTone, levelTone, type Diagnosis } from "./usage";

const TONE: Record<string, string> = {
  ok: "st-live", warn: "st-setup", danger: "risk", reply: "plan-ent", neutral: "neutral",
};
/** 글자색 — 「마지막 작업」이 목록과 상세에서 같은 색을 쓴다. */
export const TONE_COLOR: Record<string, string> = {
  ok: "var(--teal-700)", warn: "var(--amber-fg)", danger: "var(--red-fg)", reply: "var(--indigo-fg)", neutral: "var(--muted)",
};

export const Tone = ({ tone, children, title }: { tone: string; children: React.ReactNode; title?: string }) =>
  <span className={`tag ${TONE[tone] ?? "neutral"}`} title={title}>{children}</span>;

/** 사용 상태 — 목록의 열과 상세 헤더가 같은 것을 그린다: 사용 수준(정상이면 생략) · 크레딧 사용 전망
 *  (부족·잔여만) · 품질. 셋 다 조용하면 `emptyLabel`(목록은 「정상」, 헤더는 없음). */
export function StatusTags({ d, emptyLabel }: { d: Diagnosis; emptyLabel?: string }) {
  const tags: React.ReactNode[] = [];
  if (d.level !== "정상") tags.push(<Tone key="level" tone={levelTone(d.level)} title={d.levelDetail}>{d.level}</Tone>);
  if (d.forecast === "부족 예상" || d.forecast === "잔여 예상") {
    tags.push(<Tone key="forecast" tone={forecastTone(d.forecast)} title={d.forecastDetail}>{d.forecast}</Tone>);
  }
  d.alerts.forEach((a) => tags.push(<Tone key={a.key} tone="danger" title={a.detail}>{a.key}</Tone>));
  if (!tags.length) return emptyLabel ? <Tone tone="ok">{emptyLabel}</Tone> : null;
  return <>{tags}</>;
}

/** 「사용량 기준 2026-09-14 09:15」 — 연결이 없으면 그 사실을. `source` 는 `useUsageSource()` 의 답입니다. */
export function UsageStamp({ source, index, problem, busy }: {
  source: { configured: boolean | null; problem: string | null; busy: boolean };
  index: UsageIndex | null; problem: string | null; busy: boolean;
}) {
  if (source.configured === false) {
    return (
      <Link to="/data" className="tag neutral" title="사용량 두 열은 가공된 스냅샷에서 옵니다. 관리자가 아직 연결하지 않았습니다.">
        사용량 · 연결 안 됨
      </Link>
    );
  }
  if (source.problem || problem) {
    return (
      <Link to="/data" className="tag risk" title={source.problem ?? problem ?? ""}>
        사용량 · 연결 실패
      </Link>
    );
  }
  if (busy || source.busy || !index) return <span className="tag neutral">사용량 불러오는 중…</span>;
  return (
    <span className={`tag ${index.stale ? "st-setup" : "neutral"}`}
          title={index.stale ? "스냅샷이 36시간 넘게 묵었습니다 — 가공 레포의 Actions 를 확인하세요" : "가공된 스냅샷의 시각"}>
      사용량 기준 <b style={{ marginLeft: 4 }}>{index.snapshotStamp}</b>{index.stale ? " · 낡음" : ""}
    </span>
  );
}

export const idleWord = (d: number) => (d === 0 ? "오늘" : d === 1 ? "어제" : `${d}일 전`);

/** 계약의 번호 중 일부만 가공 범위에 있을 때 — 빠진 번호를 단다. 목록의 열과 상세 머리가 같이 쓴다: 숫자는
 *  범위 안의 스페이스만 합친 것이라, 말하지 않으면 줄어든 숫자가 그 계약의 전부로 읽힌다. */
export function OutOfRange({ usage }: { usage: RowUsage }) {
  if (usage.kind !== "ok" || !usage.missing.length) return null;
  return (
    <Tone tone="warn" title={`Space ${usage.missing.join(", ")} 이(가) 가공 범위(엔터프라이즈·유료 스페이스)에 없어 사용량 숫자에 안 들어갔습니다 — 번호를 확인하세요`}>
      일부 스냅샷에 없음
    </Tone>
  );
}

/** 목록의 두 열: 마지막 작업 · 사용 상태. */
export function UsageCells({ usage }: { usage: RowUsage }) {
  if (usage.kind === "no-data" || usage.kind === "loading") {
    return <><td className="muted">—</td><td className="muted">—</td></>;
  }
  if (usage.kind === "no-space") {
    return <><td className="muted">—</td>
      <td><span className="muted" title="계약의 Perso 계정·플랜에 Space ID 가 없습니다">미연결</span></td></>;
  }
  if (usage.kind === "unknown") {
    return <><td className="muted">—</td>
      <td><Tone tone="warn" title={`Space ${usage.spaces.join(", ")} 이(가) 가공 범위(엔터프라이즈·유료 스페이스)에 없습니다 — 번호를 확인하세요`}>스냅샷에 없음</Tone></td></>;
  }
  const d = usage.diagnosis;
  // 7일 이내 초록 · 30일 미만 주황 · 30일 이상 빨강 — 날짜와 「N일 전」이 같은 색 (2026-09-16 운영자).
  return (
    <>
      <td className="datecell" style={{ color: TONE_COLOR[d.idleTone], fontWeight: 600 }}>
        {d.lastActivity ? fmt(d.lastActivity) : "기록 없음"}
        {d.daysIdle !== null && <span className="sub" style={{ color: "inherit" }}>{idleWord(d.daysIdle)}</span>}
      </td>
      <td>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          <StatusTags d={d} emptyLabel="정상" />
          <OutOfRange usage={usage} />
        </div>
      </td>
    </>
  );
}
