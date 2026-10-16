// 영업 인사이트의 표와 상세 패널이 같이 쓰는 조각 — 플랜 꼬리표 · 근거 배지 · 위험도/우선도 · 날짜.
// 그리기만 합니다(사용 데이터 쪽 폴더 — 서버를 부르지 않습니다).
import { kst } from "../../lib/format";
import type { ContractRow, Priority, Risk } from "./judge";

export const num = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v.toLocaleString("en-US"));
export const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v}%`);
/** 서버 시각(UTC, 시간대 없음) → 한국 날짜 「2026-10-16」 · 「10-16」. */
export const day = (v: string | null | undefined) => kst(v, "date").replace(/\.\s*/g, "-").replace(/-$/, "");
export const mmdd = (v: string | null | undefined) => day(v).slice(5);

export const TIER: Record<string, string> = {
  pro: "Pro", free: "Free", starter: "Starter", creator: "Creator", team: "Team", business: "Business", flex: "Flex",
  enterprise: "Enterprise",
};

export function PlanTag({ plan }: { plan: string | null }) {
  if (!plan) return <span className="mut">—</span>;
  const tone = /^(enterprise|business)/i.test(plan) ? "ent" : plan.toLowerCase() === "pro" ? "pro" : "free";
  return <span className={`plan ${tone}`}>{plan}</span>;
}

export const Sg = ({ tone, children, title }: { tone?: string; children: React.ReactNode; title?: string }) =>
  <span className={`sg${tone ? ` ${tone}` : ""}`} title={title}>{children}</span>;

/** 지금 기준에는 안 맞지만 사람이 처리해 둔 줄 — 처리한 칸에 남아 있는 이유를 말한다. */
export const OffRule = () => <span className="mut" title="지금 기준에는 안 맞지만 처리해 둔 줄이라 이 칸에 남아 있습니다">기준 밖</span>;

/** 이탈 방지의 근거 — 잔여 예상 · 무활동(또는 기록 없음) · 과소사용. */
export function ChurnWhy({ row, idleDays }: { row: ContractRow; idleDays: number }) {
  if (row.forecast !== "잔여 예상") return <OffRule />;
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
  const soon = row.daysLeft !== null && row.daysLeft >= 0 && row.daysLeft <= renewalDays;
  if (!soon && row.forecast !== "부족 예상" && row.level !== "초과사용") return <OffRule />;
  return (
    <span className="sgs">
      {soon && <Sg tone="up">계약 종료 D-{row.daysLeft}</Sg>}
      {row.forecast === "부족 예상" && <Sg tone="up" title={row.forecastDetail ?? undefined}>부족 예상</Sg>}
      {row.level === "초과사용" && <Sg tone="up" title={row.levelDetail ?? undefined}>초과사용</Sg>}
    </span>
  );
}

export function RiskTag({ risk, long }: { risk: Risk | null; long?: boolean }) {
  if (!risk) return <span className="mut">—</span>;
  return risk === "high" ? <Sg tone="risk">{long ? "위험 높음" : "높음"}</Sg> : <Sg tone="warn">{long ? "위험 주의" : "주의"}</Sg>;
}

export function PriorityTag({ priority, long }: { priority: Priority | null; long?: boolean }) {
  if (!priority) return <span className="mut">—</span>;
  const word = { p1: "높음", p2: "보통", p3: "낮음" }[priority];
  const tone = { p1: "risk", p2: "up", p3: "" }[priority];
  return <Sg tone={tone}>{long ? `우선도 ${word}` : word}</Sg>;
}
