import { describe, expect, it } from "vitest";
import { RULE, daysBetween, diagnose, mergeSummaries, pairName, parseSpaceSeqs, type SpaceSummary }
  from "../src/screens/won/usage";
import type { Contract } from "../src/screens/won/shared";

// 판정은 화면에 배지로 서는 값이라, 규칙 하나가 틀리면 멀쩡한 고객이 「미사용」이 된다.

const contract = (over: Partial<Contract> = {}): Contract => ({
  credits: 120_000, plan_starts_on: "2026-03-01", plan_ends_on: "2027-03-01", plan_months: 12,
  starts_on: "2026-03-01", ends_on: "2027-03-01", ...over,
} as Contract);

const space = (over: Partial<SpaceSummary> = {}): SpaceSummary => ({
  space_seq: 1, known: true, plan_name: "X Biz", sub_status: "active",
  next_billing_day: null, billing_anchor_date: null,
  used_total: 60_000, used_30d: 9_000, used_90d: 27_000,
  first_use: "2026-03-02 09:00:00", last_use: "2026-09-12 10:00:00",
  jobs_ok: 95, jobs_failed: 5, last_job: "2026-09-12 10:00:00", projects: 80, reworked: 4, ...over,
});

const AS_OF = "2026-09-14";
const CREDITS_FROM = "2025-12-01 00:00:00";

describe("space_seq 파싱", () => {
  it("쉼표·공백·줄바꿈으로 적힌 여러 개를 읽고 중복을 버린다", () => {
    expect(parseSpaceSeqs("123456, 123457\n123456 12")).toEqual([123456, 123457, 12]);
    expect(parseSpaceSeqs("")).toEqual([]);
    expect(parseSpaceSeqs(null)).toEqual([]);
    expect(parseSpaceSeqs("없음")).toEqual([]);
  });
});

describe("사용 수준 — 누적 소진율을 계약 경과율과 견준다 (±15%p)", () => {
  it("6.5개월 지나 절반 썼으면 정상 — 최근 30일은 안 본다", () => {
    const d = diagnose(contract(), space(), AS_OF, 3.7, CREDITS_FROM);
    expect(d.pacePct).toBe(54);   // 197일 / 365일
    expect(d.usedPct).toBe(50);
    expect(d.level).toBe("정상");
    expect(d.levelDetail).toBe("소진 50% / 경과 54% (-4%p)");
    expect(diagnose(contract(), space({ used_30d: 1 }), AS_OF, 3.7, CREDITS_FROM).level).toBe("정상");
    expect(d.alerts).toEqual([]);
  });
  it("경과율보다 15%p 이상 덜 썼으면 과소사용, 더 썼으면 초과사용 — 14%p 는 정상", () => {
    expect(diagnose(contract(), space({ used_total: 46_800 }), AS_OF, 3.7, CREDITS_FROM).level).toBe("과소사용"); // 39%
    expect(diagnose(contract(), space({ used_total: 48_000 }), AS_OF, 3.7, CREDITS_FROM).level).toBe("정상");     // 40%
    expect(diagnose(contract(), space({ used_total: 81_600 }), AS_OF, 3.7, CREDITS_FROM).level).toBe("정상");     // 68%
    expect(diagnose(contract(), space({ used_total: 82_800 }), AS_OF, 3.7, CREDITS_FROM).level).toBe("초과사용"); // 69%
  });
  it("최근 30일 소진이 0이면 다른 것과 무관하게 미사용", () => {
    const d = diagnose(contract(), space({ used_30d: 0, used_total: 100_000 }), AS_OF, 3.7, CREDITS_FROM);
    expect(d.level).toBe("미사용");
  });
  it("계약 크레딧이 없으면 판정 불가이되 미사용은 잡는다", () => {
    expect(diagnose(contract({ credits: null }), space(), AS_OF, 3.7, CREDITS_FROM).level).toBe("판정 불가");
    expect(diagnose(contract({ credits: null }), space({ used_30d: 0 }), AS_OF, 3.7, CREDITS_FROM).level).toBe("미사용");
  });
});

describe("크레딧 사용 전망 — 누적 + 월평균 × 남은 달 (2026-09-17 운영자: 최근 30일 대신 지금까지의 월평균)", () => {
  // 2026-09-14 기준: 첫 소진 03-02 부터 196일 = 6.45개월 사용 → 월평균 60,000 ÷ 6.45 = 9,306.
  // 168일 남음 = 5.5개월. 60,000 + 9,306 × 5.5 = 111,429 → 93%.
  it("계약 끝에 85~115% 안이면 정상", () => {
    const d = diagnose(contract(), space(), AS_OF, 3.7, CREDITS_FROM);
    expect(d.monthsUsed).toBeCloseTo(6.447, 2);
    expect(Math.round(d.avgMonthly)).toBe(9_306);
    expect(d.projected).toBe(111_429);
    expect(d.forecast).toBe("정상");
    expect(d.forecastDetail).toBe("예상 소진 111,429 / 계약 120,000 (93%) · 월평균 9,306 × 5.5개월 남음");
  });
  it("최근 30일은 전망에 안 들어간다 — 한 달 몰아 썼어도 평균으로 편다", () => {
    const d = diagnose(contract(), space({ used_30d: 30_000 }), AS_OF, 3.7, CREDITS_FROM);
    expect(d.projected).toBe(111_429);
    expect(diagnose(contract(), space({ used_30d: 0 }), AS_OF, 3.7, CREDITS_FROM).projected).toBe(111_429);
  });
  it("115% 이상이면 부족 예상, 85% 이하면 잔여 예상", () => {
    // 90,000 ÷ 6.45 = 13,959 × 5.5 + 90,000 = 167,143 → 139%
    expect(diagnose(contract(), space({ used_total: 90_000 }), AS_OF, 3.7, CREDITS_FROM).forecast).toBe("부족 예상");
    // 20,000 ÷ 6.45 = 3,102 × 5.5 + 20,000 = 37,143 → 31%
    expect(diagnose(contract(), space({ used_total: 20_000 }), AS_OF, 3.7, CREDITS_FROM).forecast).toBe("잔여 예상");
  });
  it("보름 남았으면 반 달만 더한다 — 달력 달로 세지 않는다", () => {
    const d = diagnose(contract({ plan_ends_on: "2026-09-29", ends_on: "2026-09-29" }), space(), AS_OF, 3.7, CREDITS_FROM);
    expect(d.projected).toBe(64_592);
  });
  it("쓴 지 한 달이 안 됐으면 한 달로 센다 — 나흘 쓴 값을 일곱 배로 부풀리지 않는다", () => {
    const d = diagnose(contract(), space({ used_total: 3_000, first_use: "2026-09-10 00:00:00" }), AS_OF, 3.7, CREDITS_FROM);
    expect(d.monthsUsed).toBe(1);
    expect(d.avgMonthly).toBe(3_000);
    expect(d.projected).toBe(19_579);
  });
  it("첫 소진이 없으면 플랜 시작부터 센다 — 소진이 0 이면 월평균도 0", () => {
    const d = diagnose(contract(), space({ first_use: null, used_total: 6_000 }), AS_OF, 3.7, CREDITS_FROM);
    expect(d.monthsUsed).toBeCloseTo(197 / 30.4, 3);
    expect(diagnose(contract(), space({ first_use: null, used_total: 0, used_30d: 0 }), AS_OF, 3.7, CREDITS_FROM).avgMonthly).toBe(0);
  });
  it("계약이 스냅샷 기록보다 먼저 시작했으면 누적이 덜 잡혀 둘 다 판정 불가", () => {
    const d = diagnose(contract({ plan_starts_on: "2025-10-01", starts_on: "2025-10-01" }), space(),
      AS_OF, 3.7, CREDITS_FROM);
    expect(d.partial).toBe(true);
    expect(d.level).toBe("판정 불가");
    expect(d.forecast).toBe("판정 불가");
    expect(d.projected).toBeNull();
  });
});

describe("마지막 작업 · 품질", () => {
  it("7일 이내 초록, 그 뒤 주황, 30일부터 빨강 — 주의 배지는 없다", () => {
    const idle = diagnose(contract(), space({ last_use: "2026-08-01 00:00:00", last_job: "2026-07-20 00:00:00" }),
      AS_OF, 3.7, CREDITS_FROM);
    expect(idle.daysIdle).toBe(44);
    expect(idle.idleTone).toBe("danger");
    expect(idle.alerts).toEqual([]);
    expect(diagnose(contract(), space({ last_use: "2026-08-15 00:00:00", last_job: null }), AS_OF, 3.7, CREDITS_FROM).idleTone).toBe("danger");
    expect(diagnose(contract(), space({ last_use: "2026-08-16 00:00:00", last_job: null }), AS_OF, 3.7, CREDITS_FROM).idleTone).toBe("warn");
    expect(diagnose(contract(), space({ last_use: "2026-09-06 00:00:00", last_job: null }), AS_OF, 3.7, CREDITS_FROM).idleTone).toBe("warn");
    expect(diagnose(contract(), space({ last_use: "2026-09-07 00:00:00", last_job: null }), AS_OF, 3.7, CREDITS_FROM).idleTone).toBe("ok");
  });
  it("마지막 작업은 소진 기록과 작업 기록 중 최신", () => {
    const d = diagnose(contract(), space({ last_use: "2026-09-01 00:00:00", last_job: "2026-09-13 00:00:00" }),
      AS_OF, 3.7, CREDITS_FROM);
    expect(d.lastActivity).toBe("2026-09-13");
    expect(d.daysIdle).toBe(1);
  });
  it("실패율이 전사 평균 2배면 품질 — 단, 작업이 열 건은 돼야", () => {
    const bad = diagnose(contract(), space({ jobs_ok: 80, jobs_failed: 20 }), AS_OF, 3.7, CREDITS_FROM);
    expect(bad.alerts.find((a) => a.key === "품질")?.detail).toMatch(/실패율 20.0%/);
    const few = diagnose(contract(), space({ jobs_ok: 4, jobs_failed: 1, projects: 5, reworked: 0 }), AS_OF, 3.7, CREDITS_FROM);
    expect(few.alerts.map((a) => a.key)).not.toContain("품질");
  });
  it("재작업률은 더 이상 품질이 아니다 (2026-09-16 운영자)", () => {
    const d = diagnose(contract(), space({ projects: 50, reworked: 40 }), AS_OF, 3.7, CREDITS_FROM);
    expect(d.alerts).toEqual([]);
  });
  it("임계값은 한 곳에서 온다", () => {
    expect(RULE.gapPct).toBe(15);
    expect(RULE.forecastPct).toBe(15);
    expect(RULE.warnDays).toBe(7);
    expect(RULE.idleDays).toBe(30);
  });
});

describe("스페이스 여럿 → 계약 하나", () => {
  it("합은 더하고 시각은 최신, 모르는 스페이스는 뺀다", () => {
    const m = mergeSummaries([
      space({ space_seq: 1, used_total: 10, used_30d: 1, last_use: "2026-09-01 00:00:00", jobs_ok: 2 }),
      space({ space_seq: 2, used_total: 20, used_30d: 2, last_use: "2026-09-10 00:00:00", jobs_ok: 3 }),
      space({ space_seq: 3, known: false, used_total: 999 }),
    ])!;
    expect(m.used_total).toBe(30);
    expect(m.used_30d).toBe(3);
    expect(m.jobs_ok).toBe(5);
    expect(m.last_use).toBe("2026-09-10 00:00:00");
    expect(mergeSummaries([space({ known: false })])).toBeNull();
  });
});

describe("표시", () => {
  it("언어쌍 코드를 한국어로", () => {
    expect(pairName("en → tr")).toBe("영어 → 튀르키예어");
    expect(pairName("zh-TW → xx")).toBe("중국어(번체) → xx");
  });
  it("날짜 차이는 UTC 자정 기준", () => {
    expect(daysBetween("2026-09-01", "2026-09-14")).toBe(13);
  });
});

describe("기간 채우기", () => {
  it("빈 달은 0 으로 세우고 스냅샷 달에서 끝난다", async () => {
    const { fillMonths, fillWeeks, isCurrentPeriod } = await import("../src/screens/won/usage");
    const filled = fillMonths([{ period: "2026-07", used: 5 }], "2026-06-22", "2026-09-14");
    expect(filled.map((r) => r.period)).toEqual(["2026-06", "2026-07", "2026-08", "2026-09"]);
    expect(filled.map((r) => r.used)).toEqual([0, 5, 0, 0]);
    // 시작이 없으면 첫 기록부터
    expect(fillMonths([{ period: "2026-08", used: 1 }], null, "2026-09-14").map((r) => r.period)).toEqual(["2026-08", "2026-09"]);
    const weeks = fillWeeks([{ period: "2026-09-07", used: 3 }], "2026-09-14", 3);
    expect(weeks.map((r) => r.period)).toEqual(["2026-08-31", "2026-09-07", "2026-09-14"]);
    expect(weeks.map((r) => r.used)).toEqual([0, 3, 0]);
    expect(isCurrentPeriod("2026-09", "2026-09-14", "m")).toBe(true);
    expect(isCurrentPeriod("2026-08", "2026-09-14", "m")).toBe(false);
    expect(isCurrentPeriod("2026-09-14", "2026-09-14", "w")).toBe(true);
    expect(isCurrentPeriod("2026-09-07", "2026-09-14", "w")).toBe(false);
  });
});

describe("지급 회차 ↔ 소진 묶음", () => {
  it("회차 뒤로 소진이 시작된 엔터프라이즈 묶음이 있으면 「확인」, 없으면 「확인 불가」, 미래는 「예정」", async () => {
    const { matchGrants } = await import("../src/screens/won/usage");
    const grants = [
      { id: 1, grant_on: "2026-05-01" }, { id: 2, grant_on: "2026-06-01" },
      { id: 3, grant_on: "2026-07-01" }, { id: 4, grant_on: "2026-10-01" },
    ];
    const buckets = [
      { earn_type: "enterprise", first_use: "2026-05-10 09:00:00", consumed: 65_860 },
      { earn_type: "join", first_use: "2026-06-03 09:00:00", consumed: 30 },      // 무료 가입분 — 안 센다
      { earn_type: "enterprise", first_use: "2026-07-27 09:00:00", consumed: 60_000 },
    ];
    const m = matchGrants(grants, buckets, "2026-09-14");
    expect(m.get(1)).toEqual({ kind: "seen", firstUse: "2026-05-10", consumed: 65_860, n: 1 });
    expect(m.get(2)).toEqual({ kind: "unseen" });
    expect(m.get(3)).toEqual({ kind: "seen", firstUse: "2026-07-27", consumed: 60_000, n: 1 });
    expect(m.get(4)).toEqual({ kind: "future" });
  });
  it("한 회차 창에 묶음이 둘이면 둘 다 그 회차 것", async () => {
    const { matchGrants } = await import("../src/screens/won/usage");
    const m = matchGrants([{ id: 1, grant_on: "2026-04-20" }, { id: 2, grant_on: "2026-05-20" }], [
      { earn_type: "enterprise", first_use: "2026-05-01 00:00:00", consumed: 19_440 },
      { earn_type: "enterprise", first_use: "2026-05-10 00:00:00", consumed: 65_860 },
    ], "2026-09-14");
    expect(m.get(1)).toEqual({ kind: "seen", firstUse: "2026-05-01", consumed: 85_300, n: 2 });
    expect(m.get(2)).toEqual({ kind: "unseen" });
  });
  it("안 쓴 회차가 다음 회차의 묶음을 가로채지 않는다", async () => {
    const { matchGrants } = await import("../src/screens/won/usage");
    const grants = [{ id: 1, grant_on: "2026-06-01" }, { id: 2, grant_on: "2026-07-01" }];
    const buckets = [{ earn_type: "enterprise", first_use: "2026-07-02 00:00:00", consumed: 100 }];
    const m = matchGrants(grants, buckets, "2026-09-14");
    expect(m.get(1)).toEqual({ kind: "unseen" });
    expect(m.get(2)?.kind).toBe("seen");
  });
});

describe("자동 대조 — 무엇을 완료 처리할지", () => {
  const ev = (over: Partial<import("../src/screens/won/usage").EvidencePayment> = {}) => ({
    status: "paid", method: "card", currency: "krw", amount: 4_890_600, plan_name: "X Biz",
    paid_date: "2026-03-10 10:00:00", failed_date: null, failure_code: null, requested_date: null,
    expire_date: null, created_date: "2026-03-01 00:00:00", ...over,
  });
  it("금액이 같고 기간 안이면 자동 적용, 금액이 다르면 사람 몫, 링크만 있으면 미입금", async () => {
    const { matchPayments } = await import("../src/screens/won/usage");
    const rounds = [
      { id: 1, paid_on: "2026-03-01", amount: 4_890_600, done: false },
      { id: 2, paid_on: "2026-06-01", amount: 4_890_600, done: false },
      { id: 3, paid_on: "2026-09-01", amount: 4_890_600, done: false },
      { id: 4, paid_on: "2026-12-01", amount: 4_890_600, done: false },
    ];
    const m = matchPayments(rounds, "KRW", [
      ev(), ev({ paid_date: "2026-06-05 09:00:00", amount: 4_000_000 }),
      ev({ status: "ready", paid_date: null, amount: 9_781_200, requested_date: "2026-09-02 00:00:00" }),
    ], "2026-09-14");
    expect(m.get(1)).toEqual({ kind: "paid", paidOn: "2026-03-10", amount: 4_890_600 });
    expect(m.get(2)).toEqual({ kind: "paid-mismatch", paidOn: "2026-06-05", amount: 4_000_000 });
    expect(m.get(3)).toEqual({ kind: "ready", amount: 9_781_200, requested: "2026-09-02" });
    expect(m.get(4)).toEqual({ kind: "future" });
  });
  it("예정일 2일 전부터 본다 — 3일 뒤 회차는 「예정」, 2일 뒤 회차는 찾는다", async () => {
    const { matchPayments, matchGrants } = await import("../src/screens/won/usage");
    const rounds = [{ id: 1, paid_on: "2026-09-16", amount: 100, done: false }, { id: 2, paid_on: "2026-09-17", amount: 100, done: false }];
    const m = matchPayments(rounds, "KRW", [ev({ amount: 100, paid_date: "2026-09-13 00:00:00" })], "2026-09-14");
    expect(m.get(1)?.kind).toBe("paid");     // 예정일 2일 전 — 선납을 잡는다
    expect(m.get(2)?.kind).toBe("future");   // 3일 뒤 — 아직 안 본다
    const g = matchGrants([{ id: 1, grant_on: "2026-09-16" }, { id: 2, grant_on: "2026-09-17" }],
      [{ earn_type: "enterprise", first_use: "2026-09-14 00:00:00", consumed: 5 }], "2026-09-14");
    expect(g.get(1)?.kind).toBe("seen");
    expect(g.get(2)?.kind).toBe("future");
  });
  it("같은 날 두 회차를 한꺼번에 낸 경우 — 행마다 회차 하나씩", async () => {
    const { matchPayments } = await import("../src/screens/won/usage");
    const rounds = [{ id: 7, paid_on: "2026-03-01", amount: 4_890_600, done: false }, { id: 8, paid_on: "2026-04-01", amount: 4_890_600, done: false }];
    const m = matchPayments(rounds, "KRW", [ev(), ev({ created_date: "2026-03-02 00:00:00" })], "2026-09-14");
    expect(m.get(7)?.kind).toBe("paid");
    expect(m.get(8)?.kind).toBe("paid");
  });
  it("같은 결제 행은 한 회차에만 붙고, 원화가 아니면 안 본다", async () => {
    const { matchPayments } = await import("../src/screens/won/usage");
    const rounds = [{ id: 1, paid_on: "2026-03-01", amount: 100, done: false }, { id: 2, paid_on: "2026-03-15", amount: 100, done: false }];
    const one = matchPayments(rounds, "KRW", [ev({ amount: 100 })], "2026-09-14");
    expect(one.get(1)?.kind).toBe("paid");
    expect(one.get(2)?.kind).toBe("unseen");
    expect(matchPayments(rounds, "USD", [ev({ amount: 100 })], "2026-09-14").size).toBe(0);
  });
  it("계획: 이미 완료된 회차는 안 건드리고, 근거가 맞는 것만 고른다", async () => {
    const { planReconcile, mergeEvidence } = await import("../src/screens/won/usage");
    const merged = mergeEvidence([
      { space_seq: 1, buckets: [{ earn_type: "enterprise", first_use: "2026-05-01 00:00:00", consumed: 500 }], payments: [ev()] },
      { space_seq: 2, buckets: null, payments: [ev()] },   // 같은 enterprise 의 다른 스페이스 — 같은 결제 행
    ]);
    expect(merged.payments).toHaveLength(1);
    const plan = planReconcile({
      currency: "KRW",
      credit_grants: [{ id: 10, grant_on: "2026-04-20", done: false }, { id: 11, grant_on: "2026-05-20", done: true }],
      payments: [{ id: 20, paid_on: "2026-03-01", amount: 4_890_600, done: false }],
    }, merged, "2026-09-14");
    expect(plan.grants).toEqual([{ id: 10, firstUse: "2026-05-01", consumed: 500, n: 1 }]);
    expect(plan.payments).toEqual([{ id: 20, paidOn: "2026-03-10", amount: 4_890_600, note: null }]);
  });
});
