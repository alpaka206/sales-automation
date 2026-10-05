import { describe, expect, it } from "vitest";
import {
  mergeCredits, mergeJobs, mergeUsage, roundHalfAway,
  type CreditsFacts, type JobsFacts, type SpaceFacts, type UsageFacts,
} from "../src/screens/won/usageMerge";

// 합친 값은 옛 에이전트 SQL(agent/spaces.go @092d34f)을 계약의 스페이스 목록 전체에 돌린 값과 같아야 한다 —
// 아래 기대값은 그 SQL 을 손으로 따라간 값이다. 숫자 하나가 갈리면 같은 고객의 같은 카드가 어제와 다른 말을 한다.

type Over = { credits?: Partial<CreditsFacts>; jobs?: Partial<JobsFacts>; usage?: Partial<UsageFacts> };
/** 기본은 활동 없는 스페이스 — 가공은 범위 안의 스페이스를 활동이 없어도 싣는다. */
const facts = (space_seq: number, over: Over = {}): SpaceFacts => ({
  space_seq,
  credits: { monthly: null, weekly: null, buckets: null, records: null, ...over.credits },
  jobs: {
    ok: 0, failed: 0, kinds: null, proc_n: 0, proc_sum: null, proc_max: null, dur_sum: null,
    wait_n: 0, wait_sum: null, events: null, conc: null, ...over.jobs,
  },
  usage: {
    pairs: null, lengths: null, sources: null, categories: null, voices: 0, voice_users: null,
    seats: 0, members: 0, users_30d: null, ...over.usage,
  },
});

type Rec = NonNullable<CreditsFacts["records"]>[number];
const rec = (at: string, pf: number, p: number, over: Partial<Rec> = {}): Rec => ({
  at, pf, p, action: "EXECUTE", credits: 60, steps: 1, status: "COMPLETED", pair: "en → ko",
  minutes: 1.5, lip_sync: false, speakers: 1, ...over,
});

const NO_CREDITS = { monthly: null, weekly: null, buckets: null, records: null, records_total: 0 };
const NO_JOBS = {
  ok: 0, failed: 0, fail_kinds: null, fail_other: 0,
  processing: { n: 0, avg: null, max: null, per_video_minute: null }, wait: { n: 0, avg: null },
  concurrency_peak: null, limits: { concurrent: null },
};
const NO_USAGE = {
  languages: null, languages_other: 0, lengths: null, sources: null, categories: null, categories_other: 0,
  voices: { voices: 0, members: 0 }, seats: { seats: 0, members: 0, active_30d: 0 }, members: null,
};

describe("합칠 것이 없을 때 — 원래 SQL 이 빈 스페이스에 내던 모양", () => {
  it("빈 목록 · 활동 없는 스페이스는 null 과 0", () => {
    const cases: SpaceFacts[][] = [[], [facts(1), facts(2)]];
    for (const f of cases) {
      expect(mergeCredits(f)).toEqual(NO_CREDITS);
      expect(mergeJobs(f)).toEqual(NO_JOBS);
      expect(mergeUsage(f)).toEqual(NO_USAGE);
    }
  });
  it("가공이 null 대신 빈 배열을 내도 「없음」이다", () => {
    const blank = facts(3, {
      credits: { monthly: [], weekly: [], buckets: [], records: [] },
      jobs: { kinds: [], events: [] },
      usage: { pairs: [], lengths: [], sources: [], categories: [], voice_users: [], users_30d: [] },
    });
    expect(mergeCredits([blank])).toEqual(NO_CREDITS);
    expect(mergeJobs([blank])).toEqual(NO_JOBS);
    expect(mergeUsage([blank])).toEqual(NO_USAGE);
  });
});

describe("반올림 — DuckDB round(DOUBLE, n) 과 같은 값 (DuckDB 1.4.1 로 대조)", () => {
  it(".5 는 0 에서 먼 쪽 — 음수에서 Math.round 와 갈린다", () => {
    expect(Math.round(-2.5)).toBe(-2); // 그래서 이 함수가 있다
    expect(roundHalfAway(-2.5)).toBe(-3);
    expect(roundHalfAway(2.5)).toBe(3);
    expect(roundHalfAway(-0.5)).toBe(-1);
    expect(roundHalfAway(1.45, 1)).toBe(1.5);
    expect(roundHalfAway(-1.45, 1)).toBe(-1.5);
    expect(roundHalfAway(-123.45, 1)).toBe(-123.5);
    expect(roundHalfAway(2.675, 2)).toBe(2.68);
  });
  it("부동소수 흔적까지 같다 — 1.005 는 1.00, −0.04 는 −0 (DuckDB 도 -0.0 을 낸다)", () => {
    expect(roundHalfAway(1.005, 2)).toBe(1);
    expect(roundHalfAway(0.49999999999999994)).toBe(0);
    expect(Object.is(roundHalfAway(-0.04, 1), -0)).toBe(true);
  });
});

describe("크레딧 사용 현황", () => {
  it("기간별 — 같은 기간끼리 반올림 전 값을 더해 한 번만 반올림, 기간 순, 음수도 0 에서 먼 쪽", () => {
    const a = facts(1, { credits: { monthly: [{ period: "2026-01", used: 10.4 }, { period: "2026-03", used: 5.2 }] } });
    const b = facts(2, { credits: {
      monthly: [{ period: "2026-02", used: -3.5 }, { period: "2026-01", used: 0.2 }],
      weekly: [{ period: "2026-03-02", used: 0.4 }],
    } });
    const c = facts(3, { credits: { weekly: [{ period: "2026-03-02", used: 0.4 }, { period: "2026-02-23", used: -0.5 }] } });
    const m = mergeCredits([a, b, c]);
    // 스페이스마다 반올림해 더했다면 2026-01 은 10 + 0 = 10, 03-02 주는 0 + 0 = 0 이었다
    expect(m.monthly).toEqual([{ period: "2026-01", used: 11 }, { period: "2026-02", used: -4 }, { period: "2026-03", used: 5 }]);
    expect(m.weekly).toEqual([{ period: "2026-02-23", used: -1 }, { period: "2026-03-02", used: 1 }]);
    expect(mergeCredits([a]).weekly).toBeNull();
  });

  it("지급 묶음 — 같은 묶음은 스페이스를 건너 합치고(첫 소진은 이른 쪽) (첫 소진, 묶음 차례) 순", () => {
    const bucket = (b: number, first_use: string, consumed: number, earn_type = "enterprise", is_free = 0) =>
      ({ b, earn_type, is_free, first_use, consumed });
    const a = facts(1, { credits: { buckets: [
      bucket(9, "2026-02-01 10:00:00", 100.4),
      bucket(1, "2026-01-05 09:00:00.5", 0.4),
    ] } });
    const b = facts(2, { credits: { buckets: [
      bucket(1, "2026-01-05 09:00:00", 0.4),                 // a 의 묶음 1 과 같은 묶음 — 0.5초 먼저 썼다
      bucket(1, "2026-01-04 08:00:00", 7, "enterprise", 1),  // 같은 b 라도 무료 여부가 다르면 다른 줄
      bucket(10, "2026-02-01 10:00:00", 2.5, "plan"),        // 첫 소진이 같으면 묶음 차례 순(글자 순이면 10 이 9 앞)
    ] } });
    expect(mergeCredits([a, b]).buckets).toEqual([
      { earn_type: "enterprise", first_use: "2026-01-04 08:00:00", consumed: 7 },
      { earn_type: "enterprise", first_use: "2026-01-05 09:00:00", consumed: 1 }, // 0.4 + 0.4
      { earn_type: "enterprise", first_use: "2026-02-01 10:00:00", consumed: 100 },
      { earn_type: "plan", first_use: "2026-02-01 10:00:00", consumed: 3 },       // 2.5 → 3
    ]);
  });

  it("작업별 기록 — 프로젝트 차례는 계약 전체의 (첫 소진, 프로젝트) 순, 기록은 (시각, 차례) 내림차순, 스페이스를 단다", () => {
    const a = facts(11, { credits: { records: [
      rec("2026-03-01 10:00:00", 300, 2),
      rec("2026-03-01 09:00:00", 100, 5, { action: "ROLLBACK", credits: -60, status: null }),
    ] } });
    const b = facts(12, { credits: { records: [
      rec("2026-03-01 10:00:00", 200, 1),
      rec("2026-02-01 00:00:00", 100, 3, { status: null, pair: null, minutes: null, lip_sync: null, speakers: null }),
    ] } });
    const m = mergeCredits([a, b]);
    // (pf 100, p 3)=1 · (100, 5)=2 · (200, 1)=3 · (300, 2)=4 — 첫 소진이 같으면 프로젝트 차례 순.
    // pf · p 는 가공의 차례 번호라 화면으로 안 나간다.
    const base = { action: "EXECUTE", credits: 60, steps: 1, status: "COMPLETED", pair: "en → ko", minutes: 1.5, lip_sync: false, speakers: 1 };
    expect(m.records).toEqual([
      { ...base, at: "2026-03-01 10:00:00", space_seq: 11, project_no: 4 },
      { ...base, at: "2026-03-01 10:00:00", space_seq: 12, project_no: 3 },
      { ...base, at: "2026-03-01 09:00:00", space_seq: 11, project_no: 2, action: "ROLLBACK", credits: -60, status: null },
      { ...base, at: "2026-02-01 00:00:00", space_seq: 12, project_no: 1, status: null, pair: null, minutes: null, lip_sync: null, speakers: null },
    ]);
    expect(m.records_total).toBe(4);
  });

  it("최신 500건만 싣고 전체 수는 따로 — 차례는 자르기 전에 매겨 잘린 옛 프로젝트도 자리를 차지한다", () => {
    const at = (i: number) => `2026-03-01 00:${String(Math.floor(i / 60)).padStart(2, "0")}:${String(i % 60).padStart(2, "0")}`;
    // 작업 600개, 작업마다 다른 프로젝트(첫 소진 = 그 작업). 스페이스 1 이 앞의 300, 2 가 뒤의 300.
    const recs = (from: number) => Array.from({ length: 300 }, (_, k) => rec(at(from + k), from + k, from + k + 1));
    const m = mergeCredits([facts(1, { credits: { records: recs(0) } }), facts(2, { credits: { records: recs(300) } })]);
    expect(m.records_total).toBe(600);
    expect(m.records).toHaveLength(500);
    expect(m.records!.every((r, i, xs) => i === 0 || xs[i - 1].at > r.at)).toBe(true);
    expect(m.records![0]).toMatchObject({ at: at(599), space_seq: 2, project_no: 600 });
    expect(m.records![499]).toMatchObject({ at: at(100), space_seq: 1, project_no: 101 });
  });
});

describe("작업 성능", () => {
  const a = facts(1, { jobs: {
    ok: 10, failed: 9,
    kinds: [{ kind: "NO_VOICE_DETECTED_VAD", n: 5 }, { kind: "(미기록)", n: 3 }, { kind: "B_ERR", n: 1 }],
    proc_n: 1, proc_sum: 30, proc_max: 30, dur_sum: 9, wait_n: 1, wait_sum: 9, conc: 3,
  } });
  const b = facts(2, { jobs: {
    ok: 5, failed: 9,
    kinds: [{ kind: "NO_VOICE_DETECTED_VAD", n: 2 }, { kind: "A_ERR", n: 3 }, { kind: "SRT_PARSE_ERROR", n: 2 },
            { kind: "Z_ERR", n: 1 }, { kind: "TIMEOUT", n: 1 }],
    proc_n: 3, proc_sum: 3, proc_max: 2, dur_sum: null, wait_n: 3, wait_sum: 0, conc: null,
  } });

  it("합 · 실패 종류 상위 5(같으면 코드 순 — 「(미기록)」이 영문 앞) · 평균은 합과 개수로", () => {
    const m = mergeJobs([a, b]);
    expect(m.ok).toBe(15);
    expect(m.failed).toBe(18);
    expect(m.fail_kinds).toEqual([
      { kind: "NO_VOICE_DETECTED_VAD", n: 7 }, { kind: "(미기록)", n: 3 }, { kind: "A_ERR", n: 3 },
      { kind: "SRT_PARSE_ERROR", n: 2 }, { kind: "B_ERR", n: 1 },
    ]);
    expect(m.fail_other).toBe(2); // TIMEOUT 1 + Z_ERR 1
    // 평균 처리 시간은 33분 ÷ 4건 = 8.25 → 8.3. 스페이스 평균(30, 1)을 다시 평균내면 15.5 였다.
    // 영상 1분당은 33 ÷ 9 — 길이를 모르는 b 의 처리 시간 3분도 분자에 든다(원래 SQL 그대로).
    expect(m.processing).toEqual({ n: 4, avg: 8.3, max: 30, per_video_minute: 3.67 });
    expect(m.wait).toEqual({ n: 4, avg: 2 }); // 9 ÷ 4 = 2.25 (스페이스 평균의 평균은 4.5)
  });

  it("영상 길이를 모르거나 합이 0 이면 영상 1분당 처리 시간은 null", () => {
    expect(mergeJobs([b]).processing).toEqual({ n: 3, avg: 1, max: 2, per_video_minute: null });
    expect(mergeJobs([facts(3, { jobs: { proc_n: 2, proc_sum: 4, proc_max: 3, dur_sum: 0 } })]).processing.per_video_minute).toBeNull();
  });

  it("동시 처리 한도는 스페이스 중 큰 쪽, 아무도 모르면 null", () => {
    expect(mergeJobs([a, b, facts(3, { jobs: { conc: 5 } })]).limits).toEqual({ concurrent: 5 });
    expect(mergeJobs([b]).limits).toEqual({ concurrent: null });
  });

  describe("동시 작업 피크", () => {
    const T = 1_767_225_600_000_000; // 2026-01-01 00:00 UTC, epoch µs
    const MIN = 60_000_000;
    const job = (start: number, end: number): [number, number][] => [[T + start * MIN, 1], [T + end * MIN, -1]];

    it("같은 시각이면 끝(−1)을 먼저 센다 — 끝나자마자 시작한 작업은 겹친 것이 아니다", () => {
      const x = facts(1, { jobs: { events: job(0, 10) } });
      const y = facts(2, { jobs: { events: job(10, 20).reverse() } }); // 순서 없이 와도 된다
      expect(mergeJobs([x, y]).concurrency_peak).toBe(1);
    });
    it("스페이스를 섞어 다시 센다 — 각자 피크가 1 이어도 같은 시간에 돌았으면 2", () => {
      const x = facts(1, { jobs: { events: [...job(0, 30), ...job(40, 50)] } });
      const y = facts(2, { jobs: { events: job(10, 20) } });
      expect(mergeJobs([x]).concurrency_peak).toBe(1);
      expect(mergeJobs([y]).concurrency_peak).toBe(1);
      expect(mergeJobs([x, y]).concurrency_peak).toBe(2);
    });
  });
});

describe("영상 분석", () => {
  it("언어쌍 상위 5 (많은 순, 같으면 이름 순) · 나머지는 기타", () => {
    const a = facts(1, { usage: { pairs: [{ pair: "en → ko", n: 10 }, { pair: "ko → en", n: 4 }, { pair: "en → ja", n: 2 }] } });
    const b = facts(2, { usage: { pairs: [
      { pair: "en → ko", n: 1 }, { pair: "ja → ko", n: 4 }, { pair: "en → es", n: 2 }, { pair: "en → fr", n: 2 }, { pair: "? → ko", n: 1 },
    ] } });
    const m = mergeUsage([a, b]);
    expect(m.languages).toEqual([
      { pair: "en → ko", n: 11 }, { pair: "ja → ko", n: 4 }, { pair: "ko → en", n: 4 },
      { pair: "en → es", n: 2 }, { pair: "en → fr", n: 2 },
    ]);
    expect(m.languages_other).toBe(3); // en → ja 2 + ? → ko 1
  });

  it("영상 길이는 구간 차례(ord) 순 · 업로드 경로는 많은 순", () => {
    const a = facts(1, { usage: {
      lengths: [{ bin: "5–15분", ord: 2, n: 3 }, { bin: "5분 미만", ord: 1, n: 2 }],
      sources: [{ source: "FILE_UPLOAD", n: 3 }, { source: "YOUTUBE", n: 2 }],
    } });
    const b = facts(2, { usage: {
      lengths: [{ bin: "30분 이상", ord: 4, n: 1 }, { bin: "5분 미만", ord: 1, n: 5 }],
      sources: [{ source: "YOUTUBE", n: 1 }, { source: "(미기록)", n: 4 }],
    } });
    const m = mergeUsage([a, b]);
    expect(m.lengths).toEqual([{ bin: "5분 미만", n: 7 }, { bin: "5–15분", n: 3 }, { bin: "30분 이상", n: 1 }]);
    // 3 대 3 의 순서는 원래 SQL 이 정하지 않았다(ORDER BY n DESC 뿐) — 여기서는 이름 순
    expect(m.sources).toEqual([{ source: "(미기록)", n: 4 }, { source: "FILE_UPLOAD", n: 3 }, { source: "YOUTUBE", n: 3 }]);
  });

  it("카테고리 상위 5 에 「(미기록)」은 안 서고 기타에 들어간다", () => {
    const a = facts(1, { usage: { categories: [
      { category: "(미기록)", n: 50 }, { category: "교육", n: 10 }, { category: "게임", n: 3 }, { category: "음악", n: 3 },
    ] } });
    const b = facts(2, { usage: { categories: [
      { category: "교육", n: 2 }, { category: "뉴스", n: 5 }, { category: "스포츠", n: 1 }, { category: "요리", n: 1 },
    ] } });
    const m = mergeUsage([a, b]);
    expect(m.categories).toEqual([
      { category: "교육", n: 12 }, { category: "뉴스", n: 5 }, { category: "게임", n: 3 },
      { category: "음악", n: 3 }, { category: "스포츠", n: 1 },
    ]);
    expect(m.categories_other).toBe(51); // (미기록) 50 + 요리 1
    // 미기록뿐이면 순위는 없고 전부 기타
    const only = mergeUsage([facts(3, { usage: { categories: [{ category: "(미기록)", n: 7 }] } })]);
    expect(only.categories).toBeNull();
    expect(only.categories_other).toBe(7);
  });

  it("사람은 스페이스를 건너 한 번만 센다 — 보이스 등록자 · 최근 30일 사용자 · 멤버 막대", () => {
    const a = facts(1, { usage: {
      voices: 2, voice_users: [5, 7], seats: 10, members: 4, users_30d: [{ u: 5, jobs: 3 }, { u: 7, jobs: 1 }],
    } });
    const b = facts(2, { usage: {
      voices: 1, voice_users: [5], seats: 5, members: 2, users_30d: [{ u: 5, jobs: 2 }, { u: 9, jobs: 5 }],
    } });
    const m = mergeUsage([a, b]);
    expect(m.voices).toEqual({ voices: 3, members: 2 });                // 5 · 7
    expect(m.seats).toEqual({ seats: 15, members: 6, active_30d: 3 });  // 5 · 7 · 9 — 스페이스별 2 + 2 가 아니다
    expect(m.members).toEqual([{ rank: 1, jobs: 5 }, { rank: 2, jobs: 5 }, { rank: 3, jobs: 1 }]); // 5번 = 3 + 2
  });

  it("멤버 막대는 상위 20명", () => {
    const users = Array.from({ length: 25 }, (_, i) => ({ u: i + 1, jobs: 25 - i }));
    const m = mergeUsage([facts(1, { usage: { users_30d: users } })]);
    expect(m.members).toHaveLength(20);
    expect(m.members![0]).toEqual({ rank: 1, jobs: 25 });
    expect(m.members![19]).toEqual({ rank: 20, jobs: 6 });
    expect(m.seats.active_30d).toBe(25);
  });
});

describe("계약의 스페이스 고르기", () => {
  it("가공 범위 밖의 번호는 사실이 없어 빠지고, 활동 없는 스페이스는 값을 안 바꾼다", () => {
    const a = facts(1, { credits: { monthly: [{ period: "2026-01", used: 3 }] }, jobs: { ok: 2 }, usage: { voices: 1 } });
    const b = facts(2, { credits: { monthly: [{ period: "2026-01", used: 4 }] }, jobs: { ok: 3 }, usage: { voices: 2 } });
    const index = new Map([a, b, facts(3)].map((s) => [s.space_seq, s]));
    // 계약에 적힌 999 는 가공 범위(엔터프라이즈 · 유료 스페이스) 밖 — 부르는 쪽이 index 에 없는 번호를 버린다
    const picked = [1, 999, 2, 3].flatMap((s) => index.get(s) ?? []);
    expect(mergeCredits(picked)).toEqual(mergeCredits([a, b]));
    expect(mergeJobs(picked)).toEqual(mergeJobs([a, b]));
    expect(mergeUsage(picked)).toEqual(mergeUsage([a, b]));
    expect(mergeCredits(picked).monthly).toEqual([{ period: "2026-01", used: 7 }]);
  });
});
