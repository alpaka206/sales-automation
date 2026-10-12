import { describe, expect, it } from "vitest";
import {
  RULE, daysBetween, diagnose, grantedPct, mergeSummaries, pairName, parseSpaceSeqs, planHint, planKey, spacesByPlan,
  type SpaceSummary,
} from "../src/screens/won/usage";
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

describe("미지급 크레딧 구간 — 사선이 어디서 시작하나", () => {
  // 지급 합은 **서버가 센 값**을 읽는다(`granted_credits`). 브라우저가 회차를 다시 더하면
  // 바로 위 「미지급 크레딧」 칸과 같은 숫자를 서로 다르게 정의한다.
  const withGrants = (n: number, granted: number) => contract({
    credits: 120_000, granted_credits: granted,
    credit_grants: Array.from({ length: n }, (_, i) => ({ id: i + 1 })),
  } as Partial<Contract>);

  it("분할 지급이고 일부만 지급됐으면 지급된 비율", () => {
    expect(grantedPct(withGrants(2, 60_000))).toBe(50);
  });
  it("회차가 하나면 null — 바 전체가 지급된 몫이라 그을 선이 없다", () => {
    expect(grantedPct(withGrants(1, 60_000))).toBeNull();
  });
  it("아무 회차도 완료가 아니면 null — 「지급이 안 됐다」가 아니라 「아직 안 적었다」다", () => {
    expect(grantedPct(withGrants(4, 0))).toBeNull();
  });
  it("전액 지급됐거나 계약 크레딧이 없으면 null", () => {
    expect(grantedPct(withGrants(2, 120_000))).toBeNull();
    expect(grantedPct(contract({ credits: null, granted_credits: 10, credit_grants: [{}, {}] } as Partial<Contract>))).toBeNull();
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

describe("가공 범위 밖의 번호", () => {
  const index = (rows: SpaceSummary[]) => ({
    bySpace: new Map(rows.map((s) => [s.space_seq, s])), failRateAll: null, creditsFrom: CREDITS_FROM,
    snapshotAt: AS_OF, snapshotStamp: "", stale: false,
  });
  it("일부만 범위 밖이면 숫자는 범위 안의 것으로 내고, 빠진 번호를 들고 선다 — 조용히 줄지 않는다", async () => {
    const { usageFor } = await import("../src/screens/won/useUsage");
    const u = usageFor(contract({ space_seq: "1, 2" }), index([space({ space_seq: 1 })]));
    expect(u.kind).toBe("ok");
    expect(u.kind === "ok" && u.missing).toEqual([2]);
    expect(u.kind === "ok" && u.spaces).toEqual([1, 2]);
    const all = usageFor(contract({ space_seq: "1" }), index([space({ space_seq: 1 })]));
    expect(all.kind === "ok" && all.missing).toEqual([]);
  });
  it("전부 범위 밖이면 「스냅샷에 없음」(unknown)", async () => {
    const { usageFor } = await import("../src/screens/won/useUsage");
    expect(usageFor(contract({ space_seq: "2" }), index([space({ space_seq: 1 })])).kind).toBe("unknown");
  });
});

// 2026-10-12 운영자: 「플랜명 있네 … 근데 왜 space_seq는 안적혀?」. 플랜명까지 적어 둔 고객 셋이 「미연결」로
// 서 있었다. 같은 이름의 플랜을 쓰는 스페이스를 찾아 주되, 틀리게 찾으면 남의 사용량이 그 계약에 섞이고
// 그건 화면에 「틀렸다」로 안 보인다 — 그래서 무엇을 찾고 언제 [채우기]를 내는지를 여기서 고정한다.
// **플랜 이름과 스페이스 번호는 전부 지어낸 것이다** — 이 저장소는 공개라 실제 고객의 것을 적지 않는다.
describe("플랜 이름으로 스페이스 찾기 — 계약 폼의 space_seq 안내", () => {
  const byPlan = spacesByPlan([
    space({ space_seq: 1002, plan_name: "Acme Biz" }),                 // 한 고객의 더빙 · 스튜디오 두 스페이스
    space({ space_seq: 1001, plan_name: "Acme Biz" }),
    space({ space_seq: 2001, plan_name: "Solo Biz" }),
    ...[3001, 3002, 3003, 3004, 3005].map((n) => space({ space_seq: n, plan_name: "Shared Plan" })),
    space({ space_seq: 7, plan_name: null }),                          // 구독 행이 없는 스페이스
    space({ space_seq: 8, plan_name: "Ghost Biz", known: false }),     // 가공 범위에서 못 찾은 줄
  ]);

  it("플랜 이름으로 묶고 번호는 작은 것부터 — 이름 없는 줄과 모르는 줄은 안 센다", () => {
    expect(byPlan.get("acme biz")).toEqual({ name: "Acme Biz", spaces: [1001, 1002] });
    expect(byPlan.get("solo biz")?.spaces).toEqual([2001]);
    expect([...byPlan.keys()].sort()).toEqual(["acme biz", "shared plan", "solo biz"]);
  });

  it("대소문자와 앞뒤 · 겹친 공백이 달라도 같은 플랜이다 — 플랜명 칸은 사람이 손으로 적는다", () => {
    expect(planKey("  acme   BIZ ")).toBe("acme biz");
    expect(planHint("  acme   BIZ ", "", byPlan))
      .toEqual({ kind: "fill", plan: "Acme Biz", spaces: [1001, 1002] });
  });

  it(`스페이스가 ${RULE.fillSpaces}개 이하면 채우기, 넘으면 번호만 — 여러 고객이 같이 쓰는 플랜일 수 있다`, () => {
    expect(planHint("Solo Biz", "", byPlan).kind).toBe("fill");
    expect(planHint("Shared Plan", "", byPlan))
      .toEqual({ kind: "shared", plan: "Shared Plan", spaces: [3001, 3002, 3003, 3004, 3005] });
    const three = spacesByPlan([1, 2, 3].map((n) => space({ space_seq: n, plan_name: "Three Biz" })));
    const four = spacesByPlan([1, 2, 3, 4].map((n) => space({ space_seq: n, plan_name: "Four Biz" })));
    expect(planHint("Three Biz", "", three).kind).toBe("fill");
    expect(planHint("Four Biz", "", four).kind).toBe("shared");
  });

  it("칸에 번호가 이미 있으면 아무 말도 안 한다 — 적힌 번호를 고치라는 자리가 아니다", () => {
    expect(planHint("Acme Biz", "1001", byPlan)).toEqual({ kind: "none" });   // 더빙만 일부러 적은 계약
    expect(planHint("Acme Biz", "없음", byPlan).kind).toBe("fill");           // 숫자가 없으면 빈 칸이다
  });

  it("제품에 아직 없는 플랜명 · 빈 플랜명 · 연결 없음은 조용하다 — 계약은 그래도 저장된다", () => {
    expect(planHint("아직 안 만든 플랜 Biz", "", byPlan)).toEqual({ kind: "none" });
    expect(planHint("", "", byPlan)).toEqual({ kind: "none" });
    expect(planHint(null, null, byPlan)).toEqual({ kind: "none" });
    expect(planHint("Acme Biz", "", null)).toEqual({ kind: "none" });
    expect(planHint("Ghost Biz", "", byPlan)).toEqual({ kind: "none" });
  });
});
