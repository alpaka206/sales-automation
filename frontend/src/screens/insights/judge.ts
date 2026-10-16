// 영업 인사이트의 판정 — 업셀링 · 이탈 방지 · 추가 계약 기회의 목록을 고릅니다 (2026-10-15 운영자 지시, 목업
// `영업인사이트.html` 의 RULE · lvlOf · fcOf · riskOf · oppOf). **순수 함수만 있습니다** — 서버를 부르지 않고,
// 결과를 어디로도 보내지 않습니다. 이 폴더(`screens/insights/`)는 통째로 「사용 데이터 쪽」입니다
// (`tests/test_usage_data_stays_off_server.py`).
//
// **수주 고객 「사용 진단」과 같은 계산입니다** (운영자 조건 1). 계약의 사용 수준 · 크레딧 전망은 여기서 다시 재지
// 않고 수주 고객 목록과 같은 함수(`useUsage.usageFor` → `usage.diagnose`)에 같은 기준(서버에 저장된 85% · 115% ·
// ±15%p)을 넘겨 받습니다. 그래서 두 화면이 같은 고객을 다르게 부를 수 없습니다. 여기서 더하는 것은 영업
// 인사이트에만 있는 셋 — 업셀링 문턱(30일 크레딧) · 무활동 일수 · 계약 종료 일수 — 과 고정값 「계약 30일 미만
// 제외」뿐입니다.
import { daysBetween, type Forecast, type Level, type Rules } from "../won/usage";
import { usageFor, type UsageIndex } from "../won/useUsage";
import { RETIRED, type Row } from "../won/shared";

export type Tab = "up" | "churn" | "grow";

/** 가공 파일 `sales.json` 의 스페이스 한 줄(`agent/sales.go`). */
export type SalesSpace = {
  space_seq: number; tier: string; plan_name: string | null; sub_status: string | null; ent: boolean;
  credits_30d: number; credits_90d: number; exports_30d: number; exports_6m: number; failed_6m: number;
  users: number; members: number; seat: number; first_job: string | null; last_job: string | null; top_pair: string | null;
};
export type SalesData = { spaces: SalesSpace[] | null; spaces_active_6m: number; fail_rate_all: number | null };

/** 가공기가 `sales.json` 에 싣는 후보의 상한 — `agent/sales.go` 의 picked `LIMIT 300` 과 같아야 합니다
 *  (`tests/test_sales_insights.py` 가 맞대어 봅니다). */
export const CANDIDATE_CAP = 300;
/** 업셀링 후보가 될 수 있는 플랜 등급 — 가공 결과 `tier`(구독 플랜의 `priority_tier`)의 글자 그대로. */
export const PRO_TIER = "pro";
/** NEW — 첫 작업이 스냅샷 시각 기준 이 일수 안인 스페이스(목업에 정의가 없어 2026-10-15 계획에서 정했다). */
export const NEW_DAYS = 30;
/** 이탈 방지에서 빼는 갓 시작한 계약 — 목업의 「계약 30일 미만 제외」, 고정값이다(기준 줄에서 안 고친다). */
export const MIN_AGE_DAYS = 30;

// ── 업셀링 ────────────────────────────────────────────────────────────────

export type UpRow = {
  key: number; space_seq: number; tier: string;
  credits30d: number; exports30d: number; exports6m: number; users: number;
  lastJob: string | null; topPair: string | null; isNew: boolean;
};

/** PRO 플랜에서 B2B 로 올릴 만한 스페이스 — 엔터프라이즈가 아니고 · Pro 플랜이고 · 30일 크레딧이 기준 이상이고 ·
 *  우리 수주 고객의 space_seq 가 아닌 것. 30일 크레딧 큰 순. */
export function upsellRows(spaces: SalesSpace[] | null | undefined, ours: Set<number>,
                           rules: Pick<Rules, "upsellCredits30d">, asOf: string): UpRow[] {
  return (spaces ?? [])
    .filter((s) => !s.ent && s.tier === PRO_TIER && s.credits_30d >= rules.upsellCredits30d && !ours.has(s.space_seq))
    .map((s) => ({
      key: s.space_seq, space_seq: s.space_seq, tier: s.tier,
      credits30d: s.credits_30d, exports30d: s.exports_30d, exports6m: s.exports_6m, users: s.users,
      lastJob: s.last_job, topPair: s.top_pair,
      isNew: !!s.first_job && daysBetween(s.first_job.slice(0, 10), asOf) <= NEW_DAYS,
    }))
    .sort((a, b) => b.credits30d - a.credits30d || a.space_seq - b.space_seq);
}

/** 가공 파일이 후보 상한(300)에 닿아, 지금 기준에서 빠진 후보가 있을 수 있나.
 *
 *  가공기는 엔터프라이즈 먼저, 그다음 30일 크레딧 큰 순으로 300개를 자릅니다. 그래서 상한에 닿았으면 파일에 든
 *  비엔터프라이즈 스페이스 중 가장 작은 30일 크레딧보다 **작거나 같은** 기준에서만 빠진 후보가 생깁니다 — 그보다
 *  큰 기준이면 그 위의 스페이스는 전부 파일에 있습니다. */
export function upsellMaybeCut(spaces: SalesSpace[] | null | undefined, rules: Pick<Rules, "upsellCredits30d">): boolean {
  const list = spaces ?? [];
  if (list.length < CANDIDATE_CAP) return false;
  const floor = Math.min(...list.filter((s) => !s.ent).map((s) => s.credits_30d));
  return Number.isFinite(floor) && rules.upsellCredits30d <= floor;
}

// ── 이탈 방지 · 추가 계약 기회 (수주 고객) ─────────────────────────────────

export type ContractRow = {
  key: number; contractId: number; clientId: number; company: string; plan: string | null;
  /** 사용 판정 — 수주 고객 「사용 진단」 그대로. Space 미연결 · 스냅샷에 없음이면 null. */
  level: Level | null; forecast: Forecast | null;
  levelDetail: string | null; forecastDetail: string | null;
  usedPct: number | null; pacePct: number | null;
  credits30d: number | null; lastActivity: string | null; daysIdle: number | null; quality: boolean;
  /** 계약 종료일(해지일이 더 이르면 그날)과 스냅샷 시각에서 남은 날. */
  end: string | null; daysLeft: number | null;
  /** 플랜(없으면 계약) 시작부터 지난 날 — 「계약 30일 미만 제외」. */
  age: number | null;
  /** 잔여 크레딧 = 계약 크레딧 − 누적 소진. 계약이 스냅샷의 소진 기록(2025-12-01~)보다 먼저 시작했으면 누적이 덜
   *  잡혀 잔여가 부풀므로 null 입니다 — 수주 고객 「사용 진단」이 그 계약을 「판정 불가」로 두는 것과 같은 이유. */
  remaining: number | null; credits: number | null;
};
export type Risk = "high" | "mid";
export type Priority = "p1" | "p2" | "p3";
export type ChurnRow = ContractRow & { risk: Risk };
export type GrowRow = ContractRow & { priority: Priority };
export type ContractJudgement = {
  churn: ChurnRow[]; grow: GrowRow[];
  /** 판정 대상 계약 중 사용 판정을 못 한 수 — 계약 크레딧 · 기간이 없거나 스냅샷 기록보다 먼저 시작 */
  undetermined: number;
  /** 계약에 space_seq 가 없는 수 · 적혀 있는데 가공 범위에 없는 수 */
  unlinked: number; unknown: number;
};

const earlier = (a: string | null, b: string | null) => (!a ? b : !b ? a : a < b ? a : b);

/** 판정 대상 — 사용 중단 · 제외 고객과 이미 끝난 계약은 뺍니다(손쓸 일이 없습니다). */
export const inScope = (row: Row) =>
  !!row.active && row.plan_status !== "사용 중단" && row.plan_status !== RETIRED && row.active.state !== "종료";

/** 이탈 위험 — 크레딧이 남을 전망(잔여 예상)이고 계약이 30일을 넘었으면 대상. 무활동이 기준 이상이거나 활동
 *  기록이 아예 없으면 「높음」, 아니면 「주의」. */
export function riskOf(row: ContractRow, rules: Pick<Rules, "churnIdleDays">): Risk | null {
  if (row.forecast !== "잔여 예상" || row.age === null || row.age < MIN_AGE_DAYS) return null;
  return row.daysIdle === null || row.daysIdle >= rules.churnIdleDays ? "high" : "mid";
}

/** 추가 계약 우선도 — 높음 = 계약 종료 N일 이내 + (부족 예상 또는 초과사용) · 보통 = 부족 예상 또는 초과사용 ·
 *  낮음 = 계약 종료 N일 이내만. 사용 판정을 못 한 계약도 종료가 다가오면 「낮음」으로 섭니다 — 갱신 연락은 사용량
 *  판정이 없어도 할 일입니다. */
export function priorityOf(row: ContractRow, rules: Pick<Rules, "renewalDays">): Priority | null {
  const hot = row.forecast === "부족 예상" || row.level === "초과사용";
  const soon = row.daysLeft !== null && row.daysLeft >= 0 && row.daysLeft <= rules.renewalDays;
  return hot && soon ? "p1" : hot ? "p2" : soon ? "p3" : null;
}

/** 수주 고객의 활성 계약 → 이탈 방지 · 추가 계약 목록. `index` 는 수주 고객 목록이 쓰는 그 요약 색인입니다. */
export function contractRows(rows: Row[] | null | undefined, index: UsageIndex, rules: Rules): ContractJudgement {
  const asOf = index.snapshotAt;
  const out: ContractJudgement = { churn: [], grow: [], undetermined: 0, unlinked: 0, unknown: 0 };
  for (const row of rows ?? []) {
    if (!inScope(row)) continue;
    const c = row.active!;
    const end = earlier(c.ends_on, c.terminated_on);
    const start = c.plan_starts_on ?? c.starts_on;
    const usage = usageFor(c, index, rules);
    const d = usage.kind === "ok" ? usage.diagnosis : null;
    if (usage.kind === "no-space") out.unlinked += 1;
    if (usage.kind === "unknown") out.unknown += 1;
    if (d && d.forecast === "판정 불가") out.undetermined += 1;
    const base: ContractRow = {
      key: c.id, contractId: c.id, clientId: row.client_id, company: row.company, plan: c.plan,
      level: d?.level ?? null, forecast: d?.forecast ?? null,
      levelDetail: d?.levelDetail ?? null, forecastDetail: d?.forecastDetail ?? null,
      usedPct: d?.usedPct ?? null, pacePct: d?.pacePct ?? null,
      credits30d: usage.kind === "ok" ? usage.summary.used_30d : null,
      lastActivity: d?.lastActivity ?? null, daysIdle: d?.daysIdle ?? null,
      quality: !!d?.alerts.some((a) => a.key === "품질"),
      end, daysLeft: end ? daysBetween(asOf, end) : null,
      age: start ? daysBetween(start, asOf) : null,
      remaining: d && !d.partial ? d.remaining : null, credits: c.credits,
    };
    const risk = riskOf(base, rules);
    if (risk) out.churn.push({ ...base, risk });
    const priority = priorityOf(base, rules);
    if (priority) out.grow.push({ ...base, priority });
  }
  // 이탈: 높음 먼저, 그 안에서 오래 쉰 순(기록 없음이 맨 위). 추가 계약: 우선도 순, 그 안에서 종료가 가까운 순.
  out.churn.sort((a, b) => (a.risk === b.risk ? 0 : a.risk === "high" ? -1 : 1)
    || (b.daysIdle ?? Infinity) - (a.daysIdle ?? Infinity) || a.contractId - b.contractId);
  out.grow.sort((a, b) => a.priority.localeCompare(b.priority)
    || (a.daysLeft ?? Infinity) - (b.daysLeft ?? Infinity) || a.contractId - b.contractId);
  return out;
}
