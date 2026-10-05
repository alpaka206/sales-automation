// 수주 고객 한 곳의 사용 현황을 **브라우저 안에서** 합칩니다 (2026-10-01, 로컬 에이전트를 걷어내며).
//
// 가공 레포에는 스페이스 하나씩의 사실(`SpaceFacts`)이 있고, 계약은 스페이스 몇 개입니다. 그 묶음은
// 계약마다 다르고 운영자가 언제든 고치므로 가공 쪽에서 미리 합쳐 둘 수 없습니다 — 그래서 여기서
// 합칩니다. **순수 함수만 있고 import 가 하나도 없습니다.** 서버도 GitHub 도 모르고(받아 오는 것은
// `lib/usageData.ts`), 타입만 지우면 노드에서 그대로 돕니다 — 옛 SQL 과 맞대 볼 때 이 파일을 그대로 부르려고.
//
// 값은 전부 옛 에이전트의 SQL(`agent/spaces.go` @092d34f 의 creditsSQL · jobsSQL · usageSQL)을
// **계약의 스페이스 목록 전체에 돌린 값과 같아야** 합니다. 어제와 같은 고객의 같은 카드가 다른 숫자를
// 말하면 둘 중 어느 쪽도 못 믿습니다. 그래서 가공 쪽은 「합칠 수 있는 재료」를 내고 여기서 마무리합니다:
//   - 합은 반올림 **전** 값으로 받아 다 더한 뒤 한 번만 반올림합니다. 스페이스마다 반올림해 더하면
//     0.4 + 0.4 가 0 이 됩니다 — SQL 은 round(sum(…)) 한 번이었습니다.
//   - 평균은 평균이 아니라 합과 개수로 받습니다. 작업 수가 다른 스페이스의 평균을 다시 평균내면 작은
//     스페이스가 크게 셉니다.
//   - 상위 5 · 동시 작업 피크 · 사람 수는 나눠 센 것을 더할 수 없어 원재료(종류 전부 · 시작/끝 사건 ·
//     사람의 차례 번호)를 받아 여기서 셉니다.
// `b` · `p` · `u` 는 식별자가 아니라 **그 가공 안에서만 쓰는 차례 번호**이고(`pf` 는 프로젝트의 첫 소진 시각),
// 순서만 보존합니다 — 묶음·프로젝트를 원래 순서대로 줄 세우고 같은 사람을 알아보는 데만 쓰며 화면으로는
// 안 나갑니다. `u` 는 가공마다 새 소금으로 섞어 다른 날과 이어 붙일 수도 없습니다.
//
// 원래 SQL 도 정하지 않았던 순서는 여기서도 약속하지 않습니다: 업로드 경로의 동률(ORDER BY n DESC
// 뿐이었다), 500건 경계에 걸친 같은 시각의 기록(LIMIT 이 아무거나 골랐다). 여기서는 이름 순 ·
// 프로젝트 차례 순으로 정해 두지만, 대조할 때 그 둘은 「달라도 맞음」입니다.

// ── 가공 레포의 모양 (`facts/<group>.json` 의 `spaces[]` 한 줄) ──────────────────
export type Period = { period: string; used: number };

/** 크레딧 — 소진 행(credit_usage_history)에서. `used` · `consumed` 는 반올림 전 값입니다. */
export type CreditsFacts = {
  monthly: Period[] | null;
  /** 스냅샷 시각 기준 최근 182일만. */
  weekly: Period[] | null;
  /** 지급 묶음별 소진. 엔터프라이즈 묶음 하나를 그 회사의 스페이스들이 나눠 쓰므로 같은 `b` 가 여러
   *  스페이스에 나옵니다. `b` 는 credit_seq 의 차례 번호, `first_use` 는 DuckDB 시각 글자(사전순 = 시간순). */
  buckets: { b: number; earn_type: string; is_free: number; first_use: string; consumed: number }[] | null;
  /** 작업 실행 한 건씩 **전부**(500 으로 자르지 않음 — 자르면 합친 뒤의 최신 500 이 달라집니다).
   *  `pf` 는 그 프로젝트의 첫 소진 시각(epoch µs), `p` 는 프로젝트의 차례 번호. `credits` · `minutes` 는
   *  원래 SQL 처럼 이미 반올림돼 있습니다 — 한 작업은 한 스페이스 안에서 끝나 합칠 일이 없습니다. */
  records: {
    at: string; pf: number; p: number; action: "EXECUTE" | "ROLLBACK"; credits: number; steps: number;
    status: string | null; pair: string | null; minutes: number | null; lip_sync: boolean | null; speakers: number | null;
  }[] | null;
};

/** 작업 — 내보내기 기록(project_export_log)에서. */
export type JobsFacts = {
  ok: number; failed: number;
  /** 실패 종류 **전부**(상위 5 로 자르지 않음 — 자르면 합친 뒤의 상위 5 와 「기타」가 달라집니다). */
  kinds: { kind: string; n: number }[] | null;
  /** 완료 작업의 처리 시간(분): 개수 · 합 · 최댓값. 합과 최댓값은 완료가 없으면 null. */
  proc_n: number; proc_sum: number | null; proc_max: number | null;
  /** 같은 완료 작업들의 영상 길이(분) 합 — 길이를 모르는 작업은 빠지고, 다 모르면 null. */
  dur_sum: number | null;
  wait_n: number; wait_sum: number | null;
  /** [시각(epoch µs), 시작 +1 · 끝 −1]. 피크는 스페이스를 섞어서 다시 세야 합니다. */
  events: [number, number][] | null;
  /** 플랜의 동시 처리 한도(plan_option.concurrentJobs). */
  conc: number | null;
};

/** 사용 구성. `u` 는 사람의 차례 번호 — 가공 전체에서 한 번 매기므로 두 스페이스의 같은 사람은 같은 번호입니다. */
export type UsageFacts = {
  pairs: { pair: string; n: number }[] | null;
  lengths: { bin: string; ord: number; n: number }[] | null;
  sources: { source: string; n: number }[] | null;
  /** 「(미기록)」까지 전부. */
  categories: { category: string; n: number }[] | null;
  voices: number; voice_users: number[] | null;
  seats: number; members: number;
  users_30d: { u: number; jobs: number }[] | null;
};

export type SpaceFacts = { space_seq: number; credits: CreditsFacts; jobs: JobsFacts; usage: UsageFacts };

// ── 화면이 읽는 모양 — 화면이 실제로 쓰는 칸만 (2026-10-01 grep) ──────────────────
/** 작업 실행 한 건. 프로젝트는 번호가 아니라 차례(`project_no`, 계약 안에서 첫 소진 순)로 부릅니다.
 *  작업 상세(status · pair · minutes …)는 스냅샷의 작업 창 안에서만 있고 그 앞은 null 입니다. */
export type CreditRecord = {
  at: string; space_seq: number; project_no: number; action: "EXECUTE" | "ROLLBACK"; credits: number;
  steps: number; status: string | null; pair: string | null; minutes: number | null; lip_sync: boolean | null; speakers: number | null;
};
export type CreditsData = {
  monthly: Period[] | null; weekly: Period[] | null;
  buckets: { earn_type: string; first_use: string; consumed: number }[] | null;
  /** 최신 500건. `records_total` 이 그보다 크면 그만큼만 보입니다. */
  records: CreditRecord[] | null; records_total: number;
};
export type JobsData = {
  ok: number; failed: number;
  /** 실패 종류 상위 5 — 엔진 오류 코드가 있으면 그것, 없으면 실패 사유. 나머지는 `fail_other`. */
  fail_kinds: { kind: string; n: number }[] | null; fail_other: number;
  processing: { n: number; avg: number | null; max: number | null; per_video_minute: number | null };
  wait: { n: number; avg: number | null };
  concurrency_peak: number | null;
  limits: { concurrent: number | null };
};
export type UsageData = {
  languages: { pair: string; n: number }[] | null; languages_other: number;
  lengths: { bin: string; n: number }[] | null;
  sources: { source: string; n: number }[] | null;
  categories: { category: string; n: number }[] | null; categories_other: number;
  voices: { voices: number; members: number };
  seats: { seats: number; members: number; active_30d: number };
  members: { rank: number; jobs: number }[] | null;
};

const UNRECORDED = "(미기록)";
const RECORDS_SHOWN = 500; // 원래 SQL 의 LIMIT 500 — 화면은 20건씩 펼친다
const MEMBERS_SHOWN = 20;

/** DuckDB 의 `round(DOUBLE, n)` 과 같은 값 — 곱하고, 0 에서 먼 쪽으로 반올림하고(C 의 std::round), 나눕니다.
 *  `Math.round` 는 .5 를 +∞ 쪽으로 올려서 음수에서 SQL 과 갈립니다(−2.5 → −2, SQL 은 −3) — 취소 환급이 많은
 *  달은 소진이 음수입니다. 곱하고 나누는 순서까지 같아야 부동소수 흔적(1.005 → 1.00)도 같습니다. */
export function roundHalfAway(x: number, digits = 0): number {
  const m = 10 ** digits;
  const v = x * m;
  return (v < 0 ? -Math.round(-v) : Math.round(v)) / m;
}

/** 글자는 `<` 로 잽니다 — DuckDB 의 기본 정렬은 코드 순이라, `localeCompare`(사전 순)를 쓰면
 *  「(미기록)」과 영문 코드의 동률 순서가 SQL 과 갈립니다.
 *  ponytail: `<` 는 UTF-16 단위 순이라 이모지 같은 BMP 밖 글자와 U+E000~FFFF 글자 사이에서만 DuckDB(UTF-8
 *  바이트 순)와 갈립니다(1.4.1 실측). 실패 코드 · 언어쌍 · 업로드 경로에는 없는 글자라 두고, 카테고리에 그런
 *  글자가 서기 시작하면 UTF-8 바이트로 비교합니다. */
const cmp = <T extends string | number>(a: T, b: T) => (a < b ? -1 : a > b ? 1 : 0);
const sum = (xs: number[]) => xs.reduce((a, x) => a + x, 0);
const maxOf = (xs: (number | null)[]) => {
  const v = xs.filter((x): x is number => x !== null);
  return v.length ? Math.max(...v) : null;
};

/** 같은 이름끼리 더합니다 — 스페이스마다 센 것을 계약 하나로 모으는 기본 동작. */
function tally<K extends string | number>(rows: (readonly [K, number])[]): Map<K, number> {
  const out = new Map<K, number>();
  for (const [k, n] of rows) out.set(k, (out.get(k) ?? 0) + n);
  return out;
}

/** 원래 SQL 의 `ORDER BY n DESC, <이름>` — 많은 순, 같으면 이름 순. */
const ranked = <K extends string | number>(m: Map<K, number>) => [...m].sort((a, b) => b[1] - a[1] || cmp(a[0], b[0]));

/** 상위 5 와 나머지 합 — `… LIMIT 5` 와 `sum(전체) − sum(상위 5)`. `skip` 은 상위에서만 빠지고 나머지 합에는
 *  남습니다: 카테고리의 「(미기록)」은 순위에 안 서고 파이의 「기타」에 들어갑니다(원래 SQL 이 그랬다). */
function top5(m: Map<string, number>, skip?: string) {
  const top = ranked(m).filter(([k]) => k !== skip).slice(0, 5);
  return { top: top.length ? top : null, other: sum([...m.values()]) - sum(top.map(([, n]) => n)) };
}

/** 기간별 소진 — 같은 기간끼리 반올림 전 값을 더해 한 번만 반올림합니다. */
function periods(lists: (Period[] | null)[]): Period[] | null {
  const m = tally(lists.flatMap((l) => l ?? []).map((r) => [r.period, r.used] as const));
  return m.size ? [...m].sort((a, b) => cmp(a[0], b[0])).map(([period, used]) => ({ period, used: roundHalfAway(used) })) : null;
}

/** 동시 작업 피크 — 시작 +1 · 끝 −1 을 시각순으로 누적한 최댓값. 같은 시각이면 끝(−1)을 먼저 셉니다
 *  (원래 SQL 의 `ORDER BY t, d`): 한 작업이 끝난 그 순간 다음 작업이 시작한 것은 겹친 것이 아닙니다.
 *  스페이스별 피크를 더하거나 그 최댓값을 고르면 안 됩니다 — 두 스페이스가 **같은 시간에** 돌았는지가 피크입니다. */
function peak(events: [number, number][]): number | null {
  if (!events.length) return null;
  let running = 0, top = -Infinity;
  for (const [, d] of events.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1])) {
    running += d;
    if (running > top) top = running;
  }
  return top;
}

// ── 5. 크레딧 사용 현황 (creditsSQL) ─────────────────────────────────────────
export function mergeCredits(f: SpaceFacts[]): CreditsData {
  // 지급 묶음 — 같은 묶음(b · 종류 · 무료 여부)의 소진은 더하고 첫 소진은 가장 이른 것. 줄 세우기는
  // (첫 소진, 묶음 차례) — 원래 SQL 의 row_number() OVER (ORDER BY first_use, credit_seq). 받은 줄은 고치지
  // 않습니다(가공본은 메모리에 남아 다음 계약을 합칠 때 또 씁니다).
  const buckets = new Map<string, { b: number; earn_type: string; first_use: string; consumed: number }>();
  for (const x of f.flatMap((s) => s.credits.buckets ?? [])) {
    const k = JSON.stringify([x.b, x.earn_type, x.is_free]);
    const acc = buckets.get(k);
    if (!acc) buckets.set(k, { b: x.b, earn_type: x.earn_type, first_use: x.first_use, consumed: x.consumed });
    else {
      acc.consumed += x.consumed;
      if (x.first_use < acc.first_use) acc.first_use = x.first_use;
    }
  }

  // 작업별 소진 기록 — 프로젝트 차례는 계약의 **모든** 기록에서 (첫 소진, 프로젝트) 순으로 매깁니다.
  // 500건으로 자르기 **전에** 세야 원래 SQL 의 dense_rank 와 같습니다: 잘려 나간 옛 프로젝트도 차례를 차지합니다.
  const all = f.flatMap((s) => (s.credits.records ?? []).map((r) => ({ ...r, space_seq: s.space_seq })));
  // 프로젝트는 `p` 하나로 묶고 첫 소진은 계약 안에서 가장 이른 것 — 원래 SQL 의 pf 가 목록 전체의
  // min(create_date) 였습니다. 소진이 스페이스 둘에 걸친 프로젝트가 있어서(2026-09-14 스냅샷에 하나)
  // (pf, p) 짝으로 세면 그 프로젝트가 차례 번호를 둘 받습니다.
  const firstUse = new Map<number, number>();
  for (const r of all) firstUse.set(r.p, Math.min(firstUse.get(r.p) ?? Infinity, r.pf));
  const order = [...firstUse].sort(([pa, fa], [pb, fb]) => fa - fb || pa - pb);
  const no = new Map(order.map(([p], i) => [p, i + 1] as const));
  const records: CreditRecord[] = all
    .map((r) => ({
      at: r.at, space_seq: r.space_seq, project_no: no.get(r.p)!, action: r.action, credits: r.credits, steps: r.steps,
      status: r.status, pair: r.pair, minutes: r.minutes, lip_sync: r.lip_sync, speakers: r.speakers,
    }))
    .sort((a, b) => cmp(b.at, a.at) || b.project_no - a.project_no)
    .slice(0, RECORDS_SHOWN);

  return {
    monthly: periods(f.map((s) => s.credits.monthly)),
    weekly: periods(f.map((s) => s.credits.weekly)),
    buckets: buckets.size
      ? [...buckets.values()].sort((a, b) => cmp(a.first_use, b.first_use) || a.b - b.b)
        .map((x) => ({ earn_type: x.earn_type, first_use: x.first_use, consumed: roundHalfAway(x.consumed) }))
      : null,
    records: records.length ? records : null,
    records_total: all.length,
  };
}

// ── 8. 작업 성능 (jobsSQL) ───────────────────────────────────────────────────
export function mergeJobs(f: SpaceFacts[]): JobsData {
  const j = f.map((s) => s.jobs);
  const kinds = top5(tally(j.flatMap((x) => x.kinds ?? []).map((k) => [k.kind, k.n] as const)));
  const procN = sum(j.map((x) => x.proc_n));
  const proc = sum(j.map((x) => x.proc_sum ?? 0));
  // 영상 길이를 모르는 작업은 분모에서만 빠지고 분자(처리 시간)에는 남습니다 — 원래 SQL 의
  // sum(proc_min) / nullif(sum(dur_min), 0) 이 그랬습니다. 길이를 하나도 모르면 null.
  const durs = j.map((x) => x.dur_sum).filter((x): x is number => x !== null);
  const dur = sum(durs);
  const waitN = sum(j.map((x) => x.wait_n));
  return {
    ok: sum(j.map((x) => x.ok)),
    failed: sum(j.map((x) => x.failed)),
    fail_kinds: kinds.top?.map(([kind, n]) => ({ kind, n })) ?? null,
    fail_other: kinds.other,
    processing: {
      n: procN,
      avg: procN ? roundHalfAway(proc / procN, 1) : null,
      max: maxOf(j.map((x) => x.proc_max)),
      per_video_minute: procN && durs.length && dur !== 0 ? roundHalfAway(proc / dur, 2) : null,
    },
    wait: { n: waitN, avg: waitN ? roundHalfAway(sum(j.map((x) => x.wait_sum ?? 0)) / waitN) : null },
    concurrency_peak: peak(j.flatMap((x) => x.events ?? [])),
    // 스페이스마다 플랜이 다르면 큰 쪽 — 원래 SQL 의 max(conc).
    limits: { concurrent: maxOf(j.map((x) => x.conc)) },
  };
}

// ── 영상 분석 (usageSQL) ─────────────────────────────────────────────────────
export function mergeUsage(f: SpaceFacts[]): UsageData {
  const u = f.map((s) => s.usage);
  const langs = top5(tally(u.flatMap((x) => x.pairs ?? []).map((p) => [p.pair, p.n] as const)));
  const cats = top5(tally(u.flatMap((x) => x.categories ?? []).map((c) => [c.category, c.n] as const)), UNRECORDED);
  const sources = ranked(tally(u.flatMap((x) => x.sources ?? []).map((s) => [s.source, s.n] as const)));
  // 길이 구간은 이름과 차례(ord)가 짝입니다 — 차례가 막대의 순서입니다.
  const lengths = new Map<string, { bin: string; ord: number; n: number }>();
  for (const l of u.flatMap((x) => x.lengths ?? [])) {
    const k = JSON.stringify([l.bin, l.ord]);
    const acc = lengths.get(k);
    if (acc) acc.n += l.n;
    else lengths.set(k, { bin: l.bin, ord: l.ord, n: l.n });
  }
  // 사람은 스페이스를 건너 한 번만 셉니다 — 계약의 두 스페이스에서 내보낸 사람은 한 사람이고 막대도 하나입니다.
  const users = ranked(tally(u.flatMap((x) => x.users_30d ?? []).map((r) => [r.u, r.jobs] as const)));
  return {
    languages: langs.top?.map(([pair, n]) => ({ pair, n })) ?? null,
    languages_other: langs.other,
    lengths: lengths.size ? [...lengths.values()].sort((a, b) => a.ord - b.ord).map(({ bin, n }) => ({ bin, n })) : null,
    sources: sources.length ? sources.map(([source, n]) => ({ source, n })) : null,
    categories: cats.top?.map(([category, n]) => ({ category, n })) ?? null,
    categories_other: cats.other,
    voices: { voices: sum(u.map((x) => x.voices)), members: new Set(u.flatMap((x) => x.voice_users ?? [])).size },
    seats: { seats: sum(u.map((x) => x.seats)), members: sum(u.map((x) => x.members)), active_30d: users.length },
    members: users.length ? users.slice(0, MEMBERS_SHOWN).map(([, jobs], i) => ({ rank: i + 1, jobs })) : null,
  };
}
