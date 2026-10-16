import { describe, expect, it } from "vitest";
import {
  CANDIDATE_CAP, contractRows, priorityOf, riskOf, upsellMaybeCut, upsellRows,
  type ContractRow, type SalesSpace,
} from "../src/screens/insights/judge";
import { usageFor, type UsageIndex } from "../src/screens/won/useUsage";
import type { Rules, SpaceSummary } from "../src/screens/won/usage";
import type { Contract, Row } from "../src/screens/won/shared";

// 영업 인사이트의 판정(2026-10-15 운영자 지시, 목업의 RULE · riskOf · oppOf). 수주 고객 쪽 판정은 수주 고객
// 「사용 진단」과 같은 함수 · 같은 기준이어야 한다 — 두 화면이 같은 고객을 다르게 부르면 안 된다.
// **스페이스 번호 · 회사 이름은 전부 지어낸 것이다** — 이 저장소는 공개라 실제 고객의 것을 적지 않는다.

const RULES: Rules = { upsellCredits30d: 9_720, leftPct: 85, churnIdleDays: 30, renewalDays: 30, shortPct: 115, gapPp: 15 };
const AS_OF = "2026-09-14";

const salesSpace = (over: Partial<SalesSpace> = {}): SalesSpace => ({
  space_seq: 1001, tier: "pro", plan_name: "Pro", sub_status: "active", ent: false,
  credits_30d: 12_000, credits_90d: 30_000, exports_30d: 40, exports_6m: 120, failed_6m: 2,
  users: 1, members: 1, seat: 1, first_job: "2026-03-02", last_job: "2026-09-12", top_pair: "en → ko", ...over,
});

describe("업셀링 — Pro 플랜 · 엔터프라이즈 아님 · 30일 크레딧이 기준 이상 · 우리 고객 아님", () => {
  it("기준 이상(같은 값 포함)만, 30일 크레딧 큰 순으로", () => {
    const rows = upsellRows([
      salesSpace({ space_seq: 1, credits_30d: 9_720 }),
      salesSpace({ space_seq: 2, credits_30d: 9_719 }),
      salesSpace({ space_seq: 3, credits_30d: 30_000 }),
    ], new Set(), RULES, AS_OF);
    expect(rows.map((r) => r.space_seq)).toEqual([3, 1]);
  });
  it("엔터프라이즈 · Pro 가 아닌 등급 · 우리 수주 고객의 스페이스는 뺀다", () => {
    const rows = upsellRows([
      salesSpace({ space_seq: 1, ent: true }),
      salesSpace({ space_seq: 2, tier: "free" }),
      salesSpace({ space_seq: 3, tier: "team" }),
      salesSpace({ space_seq: 4 }),
      salesSpace({ space_seq: 5 }),
    ], new Set([5]), RULES, AS_OF);
    expect(rows.map((r) => r.space_seq)).toEqual([4]);
  });
  it("기준을 바꾸면 후보가 따라 바뀐다", () => {
    const spaces = [salesSpace({ space_seq: 1, credits_30d: 2_000 }), salesSpace({ space_seq: 2, credits_30d: 12_000 })];
    expect(upsellRows(spaces, new Set(), RULES, AS_OF)).toHaveLength(1);
    expect(upsellRows(spaces, new Set(), { upsellCredits30d: 1_500 }, AS_OF)).toHaveLength(2);
  });
  it("NEW 는 첫 작업이 스냅샷 시각 기준 30일 안", () => {
    const rows = upsellRows([
      salesSpace({ space_seq: 1, first_job: "2026-08-15" }), // 30일 전
      salesSpace({ space_seq: 2, first_job: "2026-08-14" }), // 31일 전
      salesSpace({ space_seq: 3, first_job: null }),
    ], new Set(), RULES, AS_OF);
    expect(Object.fromEntries(rows.map((r) => [r.space_seq, r.isNew]))).toEqual({ 1: true, 2: false, 3: false });
  });
  it("가공 파일이 300곳 상한에 닿았고 기준이 파일의 가장 작은 30일 크레딧 이하면 「빠졌을 수 있음」", () => {
    const full = Array.from({ length: CANDIDATE_CAP }, (_, i) => salesSpace({ space_seq: i + 1, credits_30d: 2_000 + i }));
    full[0] = salesSpace({ space_seq: 1, ent: true, credits_30d: 0 }); // 엔터프라이즈는 크레딧과 무관하게 먼저 실린다
    expect(upsellMaybeCut(full, { upsellCredits30d: 9_720 })).toBe(false);
    expect(upsellMaybeCut(full, { upsellCredits30d: 2_001 })).toBe(true);
    expect(upsellMaybeCut(full.slice(1), { upsellCredits30d: 1_500 })).toBe(false); // 상한 아래
  });
});

// ── 수주 고객 ────────────────────────────────────────────────────────────

const contract = (over: Partial<Contract> = {}): Contract => ({
  id: 77, seq: 1, state: "진행 중", plan: "Business Tier 2", credits: 120_000,
  plan_starts_on: "2026-03-01", plan_ends_on: "2027-03-01", plan_months: 12,
  starts_on: "2026-03-01", ends_on: "2027-03-01", terminated_on: null, space_seq: "501", ...over,
} as Contract);
const row = (over: Partial<Row> = {}, c: Partial<Contract> = {}): Row => ({
  client_id: 1101, company: "가나 스튜디오", plan_status: "사용중", active: contract(c), contracts: [], ...over,
} as Row);
const summary = (over: Partial<SpaceSummary> = {}): SpaceSummary => ({
  space_seq: 501, known: true, plan_name: "Ga Biz", sub_status: "active",
  next_billing_day: null, billing_anchor_date: null,
  used_total: 60_000, used_30d: 9_000, used_90d: 27_000,
  first_use: "2026-03-02 09:00:00", last_use: "2026-09-12 10:00:00",
  jobs_ok: 95, jobs_failed: 5, last_job: "2026-09-12 10:00:00", projects: 80, reworked: 4, ...over,
});
const index = (...rows: SpaceSummary[]): UsageIndex => ({
  bySpace: new Map(rows.map((s) => [s.space_seq, s])), failRateAll: 3.7, creditsFrom: "2025-12-01 00:00:00",
  snapshotAt: AS_OF, snapshotStamp: "", stale: false,
});

describe("수주 고객 쪽 판정은 수주 고객 「사용 진단」과 같은 계산이다", () => {
  it("사용 수준 · 크레딧 전망 · 근거 문구가 수주 고객 목록의 판정과 한 글자도 같다", () => {
    for (const used of [20_000, 60_000, 82_800, 90_000]) {
      const r = row();
      const idx = index(summary({ used_total: used }));
      const want = usageFor(r.active, idx, RULES);
      const got = contractRows([r], idx, { ...RULES, renewalDays: 999 }).grow[0]; // 종료가 「곧」이라 반드시 선다
      expect(want.kind).toBe("ok");
      if (want.kind !== "ok") continue;
      expect([got.level, got.forecast, got.levelDetail, got.forecastDetail])
        .toEqual([want.diagnosis.level, want.diagnosis.forecast, want.diagnosis.levelDetail, want.diagnosis.forecastDetail]);
    }
  });
});

describe("이탈 방지 — 잔여 예상 + 계약 30일 이상", () => {
  it("잔여 예상(31%)이면 서고, 최근 활동이 있으면 「주의」", () => {
    const out = contractRows([row()], index(summary({ used_total: 20_000 })), RULES);
    expect(out.churn).toHaveLength(1);
    expect(out.churn[0].risk).toBe("mid");
    expect(out.churn[0].daysIdle).toBe(2);
  });
  it("무활동이 기준 이상이거나 기록이 없으면 「높음」", () => {
    const idle = contractRows([row()], index(summary({ used_total: 20_000, last_use: "2026-08-15 00:00:00", last_job: null })), RULES);
    expect(idle.churn[0].risk).toBe("high");
    expect(idle.churn[0].daysIdle).toBe(30);
    expect(contractRows([row()], index(summary({ used_total: 20_000, last_use: "2026-08-15 00:00:00", last_job: null })),
      { ...RULES, churnIdleDays: 31 }).churn[0].risk).toBe("mid");
  });
  it("정상 · 부족 예상이면 안 선다 — 잔여 예상 기준을 바꾸면 따라 바뀐다", () => {
    expect(contractRows([row()], index(summary()), RULES).churn).toHaveLength(0); // 93%
    expect(contractRows([row()], index(summary({ used_total: 20_000 })), { ...RULES, leftPct: 30 }).churn).toHaveLength(0);
  });
  it("계약 30일 미만은 뺀다 — 고정값", () => {
    const fresh = row({}, { plan_starts_on: "2026-08-20", starts_on: "2026-08-20", plan_ends_on: "2027-08-20", ends_on: "2027-08-20" });
    const base: ContractRow = contractRows([fresh], index(summary({ used_total: 100, first_use: "2026-08-21 00:00:00" })),
      { ...RULES, renewalDays: 999 }).grow[0];
    expect(base.age).toBe(25);
    expect(riskOf({ ...base, forecast: "잔여 예상" }, RULES)).toBeNull();
    expect(riskOf({ ...base, forecast: "잔여 예상", age: 30 }, RULES)).toBe("mid"); // 30일부터 선다(최근 활동 있음)
  });
});

describe("추가 계약 기회 — 계약 종료 N일 이내 · 부족 예상 · 초과사용", () => {
  const ending = { ends_on: "2026-10-01", plan_ends_on: "2026-10-01" }; // D-17
  it("우선도: 종료가 가깝고 부족 예상이면 높음, 부족 예상만이면 보통, 종료만 가까우면 낮음", () => {
    // 7개월 계약의 92% 가 지났는데 113% 를 썼다 → 부족 예상(122%) · 초과사용(+21%p), 그리고 D-17.
    expect(contractRows([row({}, ending)], index(summary({ used_total: 135_000 })), RULES).grow[0].priority).toBe("p1");
    expect(contractRows([row()], index(summary({ used_total: 90_000 })), RULES).grow[0].priority).toBe("p2");
    expect(contractRows([row({}, ending)], index(summary({ used_total: 60_000 })), RULES).grow[0].priority).toBe("p3");
    expect(contractRows([row()], index(summary()), RULES).grow).toHaveLength(0);
  });
  it("초과사용(소진율이 경과율보다 15%p 이상)도 「뜨거운」 쪽이다", () => {
    const over = contractRows([row()], index(summary({ used_total: 82_800 })), RULES).grow[0];
    expect(over.level).toBe("초과사용");
    expect(over.priority).toBe("p2");
  });
  it("계약 종료 기준 일수를 바꾸면 따라 바뀐다 — 해지일이 더 이르면 그날이 종료일", () => {
    expect(contractRows([row({}, ending)], index(summary()), { ...RULES, renewalDays: 16 }).grow).toHaveLength(0);
    const terminated = contractRows([row({}, { terminated_on: "2026-09-20" })], index(summary()), RULES).grow[0];
    expect([terminated.end, terminated.daysLeft, terminated.priority]).toEqual(["2026-09-20", 6, "p3"]);
  });
  it("계약이 스냅샷 소진 기록보다 먼저 시작했으면 잔여 크레딧을 적지 않는다 — 누적이 덜 잡혀 잔여가 부푼다", () => {
    const early = row({}, { ...ending, plan_starts_on: "2025-10-01", starts_on: "2025-10-01" });
    const out = contractRows([early], index(summary({ used_total: 0 })), RULES).grow[0];
    expect([out.forecast, out.remaining, out.priority]).toEqual(["판정 불가", null, "p3"]);
  });
  it("Space 미연결이어도 종료가 가까우면 「낮음」으로 선다 — 갱신 연락은 사용량 판정이 없어도 할 일이다", () => {
    const out = contractRows([row({}, { ...ending, space_seq: null })], index(), RULES);
    expect(out.grow.map((r) => [r.priority, r.forecast, r.remaining])).toEqual([["p3", null, null]]);
    expect(out.unlinked).toBe(1);
    expect(out.churn).toHaveLength(0);
  });
  it("정렬: 우선도 순, 그 안에서 종료가 가까운 순", () => {
    const out = contractRows([
      row({ client_id: 1 }, { id: 1, ends_on: "2026-10-10", plan_ends_on: "2026-10-10" }),
      row({ client_id: 2 }, { id: 2, ends_on: "2026-09-20", plan_ends_on: "2026-09-20" }),
      row({ client_id: 3 }, { id: 3 }),
    ], index(summary()), RULES);
    expect(out.grow.map((r) => r.contractId)).toEqual([2, 1]);
    expect(priorityOf({ ...out.grow[0], forecast: "부족 예상" }, RULES)).toBe("p1");
  });
});

describe("판정 대상 · 판정 불가", () => {
  it("사용 중단 · 제외 고객과 이미 끝난 계약은 빼고, 판정 불가 · 스냅샷에 없음을 센다", () => {
    const out = contractRows([
      row({ client_id: 1, plan_status: "사용 중단" }, { id: 1 }),
      row({ client_id: 2, plan_status: "제외" }, { id: 2 }),
      row({ client_id: 3 }, { id: 3, state: "종료" }),
      row({ client_id: 4 }, { id: 4, credits: null }),           // 계약 크레딧 없음 → 판정 불가
      row({ client_id: 5 }, { id: 5, space_seq: "999" }),        // 가공 범위에 없음
      row({ client_id: 6, active: null }),
    ], index(summary({ used_total: 20_000 })), RULES);
    expect(out.churn).toHaveLength(0);
    expect([out.undetermined, out.unknown, out.unlinked]).toEqual([1, 1, 0]);
  });
});
