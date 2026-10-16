// 수주 고객의 **사용 진단** — 계약(우리 DB)과 스냅샷 집계(가공 레포, `lib/usageData.ts`)를 브라우저
// 안에서 맞대어 판정합니다. **순수 함수만 있습니다.** 서버를 부르지 않고, 결과를 어디로도 보내지
// 않습니다 — 스냅샷 숫자는 이 브라우저의 화면에서 끝납니다.
//
// 임계값은 **전부 임시**입니다(요청 문서의 표현 그대로). 운영하며 조정할 값이라 한 곳에
// 모아 둡니다. 사용 수준(±N%p)과 크레딧 사용 전망(85% · 115%)의 문턱은 여기 없습니다 — 영업 인사이트와
// 같이 쓰는 값이라 **서버에 저장된 것**을 화면이 받아 넘겨 줍니다(아래 `Rules`, 2026-10-15).
import type { Contract } from "./shared";

export const RULE = {
  warnDays: 7,      // 마지막 작업: N일 초과면 주황
  idleDays: 30,     // 마지막 작업: N일 이상이면 빨강 · 미사용
  failMult: 2,      // 품질: 실패율 ≥ 전사 평균 N배
  minJobs: 10,      // 실패율을 따질 최소 작업 수 — 다섯 건 중 하나가 실패해도 「품질」이 되면 안 된다
  leadDays: 2,      // 자동 대조: 예정일 N일 전부터 본다(지난 것은 전부). 그보다 먼 회차는 안 본다 (2026-09-15 운영자)
  fillSpaces: 3,    // 플랜명 → space_seq 채우기: 그 플랜에 묶인 스페이스가 N개 이하일 때만 한 번에 채운다 (2026-10-12 운영자)
};

/** 영업 인사이트와 수주 고객 「사용 진단」이 같이 쓰는 판정 기준 — **서버에 저장된 값**입니다
 *  (`GET /api/ui/insight-rules`, 목록 · 기본값 · 범위는 서버의 `src/db/insights.py` RULES 한 곳, 2026-10-15
 *  운영자: 「85%·115%·15%p는 수주 고객 화면과 값을 공유」 · 「기준값은 서버에 저장하고 화면에서 바꿀 수 있게」).
 *
 *  **여기에는 기본값이 없습니다.** 기본값이 화면에도 있으면 서버를 못 읽은 동안 두 화면이 서로 다른 문턱으로
 *  같은 고객을 판정하고, 그건 화면 어디에도 안 보입니다. 못 받았으면 `rulesFrom` 이 null 이고, 그동안 화면은
 *  사용 상태를 「—」로 둡니다. 받는 일은 서버와 말하는 쪽(`lib/insightRules.ts`)이 하고, 이 파일은 받은 값을
 *  이름 붙여 꺼내기만 합니다 — 사용 데이터를 쥔 이 파일은 서버를 부르지 않습니다. */
export type Rules = {
  upsellCredits30d: number; // 업셀링: 30일 크레딧 N 이상
  leftPct: number;          // 잔여 예상: 예상 소진이 계약 크레딧의 N% 이하 — 수주 고객과 같이 씀
  churnIdleDays: number;    // 이탈 위험 높음: 무활동 N일 이상
  renewalDays: number;      // 추가 계약: 계약 종료 N일 이내
  shortPct: number;         // 부족 예상: 예상 소진이 계약 크레딧의 N% 이상 — 수주 고객과 같이 씀
  gapPp: number;            // 과소 · 초과사용: 누적 소진율 − 계약 경과율이 −N%p 이하 · +N%p 이상 — 수주 고객과 같이 씀
};
/** 서버의 열쇠(`src/db/insights.py` RULES 의 key). */
export const RULE_KEYS: Record<keyof Rules, string> = {
  upsellCredits30d: "upsell_credits_30d",
  leftPct: "forecast_left_pct",
  churnIdleDays: "churn_idle_days",
  renewalDays: "renewal_days",
  shortPct: "forecast_short_pct",
  gapPp: "level_gap_pp",
};

/** 서버가 준 목록 → 이름 붙인 기준. 하나라도 없으면 null — 일부만 서버 값인 판정은 하지 않습니다. */
export function rulesFrom(list: { key: string; value: number }[] | null | undefined): Rules | null {
  if (!list) return null;
  const out = {} as Rules;
  for (const [name, key] of Object.entries(RULE_KEYS) as [keyof Rules, string][]) {
    const value = list.find((rule) => rule.key === key)?.value;
    if (typeof value !== "number" || !Number.isFinite(value)) return null;
    out[name] = value;
  }
  return out;
}

/** `diagnose` 가 쓰는 셋 — 수주 고객 화면과 영업 인사이트가 같은 값을 넘깁니다. */
export type DiagnoseRules = Pick<Rules, "leftPct" | "shortPct" | "gapPp">;

/** 가공 파일 `summary.json` 의 한 스페이스. */
export type SpaceSummary = {
  space_seq: number; known: boolean;
  plan_name: string | null; sub_status: string | null;
  next_billing_day: string | null; billing_anchor_date: string | null;
  used_total: number; used_30d: number; used_90d: number;
  first_use: string | null; last_use: string | null;
  jobs_ok: number; jobs_failed: number; last_job: string | null;
  projects: number; reworked: number;
};
export type SummaryData = {
  spaces: SpaceSummary[]; fail_rate_all: number | null;
  credits_from: string | null; jobs_from: string | null;
};

/** 계약의 `space_seq` 칸은 자유 텍스트입니다 — 쉼표·공백·줄바꿈으로 여러 개를 적습니다. */
export function parseSpaceSeqs(text: string | null | undefined): number[] {
  if (!text) return [];
  const out: number[] = [];
  for (const m of text.matchAll(/\d+/g)) {
    const v = Number(m[0]);
    if (v > 0 && !out.includes(v)) out.push(v);
  }
  return out;
}

// ── 플랜 이름으로 스페이스 찾기 (2026-10-12) ─────────────────────────────
// 운영자: 「플랜명 있네 … 근데 왜 space_seq는 안적혀? 연동을 따로 해야하는거야?」. 제품의 엔터프라이즈 플랜은
// 이름이 「<고객> Biz」 꼴이라(가공기 `agent/spaces.go` 가 스페이스마다 싣는 `plan_name`), 계약의 「플랜명」과
// 같은 이름의 플랜을 쓰는 스페이스가 곧 그 계약의 space_seq 후보입니다. **여기는 찾기까지만 합니다** — 칸에
// 넣는 것은 사람이 [채우기]를 눌렀을 때이고(`PlanSpaceHint.tsx`), 저장은 그 뒤 사람이 따로 누릅니다.

/** 플랜 이름의 열쇠 — 대소문자와 앞뒤 · 겹친 공백의 차이는 같은 이름으로 봅니다. */
export const planKey = (name: string | null | undefined) =>
  (name ?? "").trim().replace(/\s+/g, " ").toLowerCase();

export type PlanSpaces = { name: string; spaces: number[] };

/** `summary.json` 의 스페이스들을 플랜 이름으로 묶습니다. 열쇠는 `planKey`, 번호는 작은 것부터. */
export function spacesByPlan(spaces: SpaceSummary[]): Map<string, PlanSpaces> {
  const out = new Map<string, PlanSpaces>();
  for (const s of spaces) {
    const key = planKey(s.plan_name);
    if (!s.known || !key) continue;
    const hit = out.get(key);
    if (hit) hit.spaces.push(s.space_seq);
    else out.set(key, { name: (s.plan_name ?? "").trim(), spaces: [s.space_seq] });
  }
  for (const plan of out.values()) plan.spaces.sort((a, b) => a - b);
  return out;
}

/** 계약 폼의 space_seq 칸 아래에 무엇을 적을까.
 *
 *  - `fill` — 그 플랜의 스페이스가 `RULE.fillSpaces` 개 이하: 번호와 [채우기].
 *  - `shared` — 그보다 많다: 번호는 보여 주되 채우기는 없다. **여러 고객이 같이 쓰는 플랜일 수 있어서**입니다
 *    (이름에 고객이 없는 공용 플랜 · 데모 플랜이 실제로 있습니다 — 2026-10-12 스냅샷 실측). 그대로 채우면 남의
 *    사용량이 이 계약의 숫자에 섞이고, 그건 화면 어디에도 「틀렸다」로 안 보입니다. 개수로 재는 이유: 스페이스가
 *    어느 고객의 것인지는 가공 데이터에 없습니다 — 그래서 한 고객이 수십 개를 쓰는 플랜도 여기로 옵니다.
 *    그때는 번호를 보고 사람이 적습니다. (실제 플랜 이름 · 고객 이름 · 스페이스 번호는 코드 · 테스트 · 문서에
 *    적지 않습니다 — 이 저장소는 공개입니다.)
 *  - `none` — 플랜명이 비었거나, 그런 이름의 플랜이 가공 범위에 없거나(아직 안 만든 플랜 — 계약은 그래도
 *    저장됩니다), **칸에 번호가 이미 있다**. 이 안내는 「미연결」을 푸는 자리이지 적힌 번호를 고치는 자리가
 *    아닙니다 — 더빙 스페이스 하나만 일부러 적어 둔 계약에 「둘 다 적으라」고 계속 말하면 안 됩니다. */
export type PlanHint =
  | { kind: "none" }
  | { kind: "fill" | "shared"; plan: string; spaces: number[] };

export function planHint(planName: string | null | undefined, spaceSeq: string | null | undefined,
                         byPlan: Map<string, PlanSpaces> | null): PlanHint {
  if (!byPlan || parseSpaceSeqs(spaceSeq).length) return { kind: "none" };
  const hit = byPlan.get(planKey(planName));
  if (!hit || !hit.spaces.length) return { kind: "none" };
  return { kind: hit.spaces.length <= RULE.fillSpaces ? "fill" : "shared", plan: hit.name, spaces: hit.spaces };
}

/** 스페이스 여럿을 계약 하나로 합칩니다 — 합은 더하고 시각은 최신을 고릅니다. */
export function mergeSummaries(rows: SpaceSummary[]): SpaceSummary | null {
  const known = rows.filter((r) => r.known);
  if (!known.length) return null;
  const latest = (a: string | null, b: string | null) => (!a ? b : !b ? a : a > b ? a : b);
  return known.reduce((acc, r) => ({
    space_seq: acc.space_seq, known: true,
    plan_name: acc.plan_name ?? r.plan_name, sub_status: acc.sub_status ?? r.sub_status,
    next_billing_day: acc.next_billing_day ?? r.next_billing_day,
    billing_anchor_date: acc.billing_anchor_date ?? r.billing_anchor_date,
    used_total: acc.used_total + r.used_total, used_30d: acc.used_30d + r.used_30d,
    used_90d: acc.used_90d + r.used_90d,
    first_use: acc.first_use && r.first_use ? (acc.first_use < r.first_use ? acc.first_use : r.first_use) : (acc.first_use ?? r.first_use),
    last_use: latest(acc.last_use, r.last_use),
    jobs_ok: acc.jobs_ok + r.jobs_ok, jobs_failed: acc.jobs_failed + r.jobs_failed,
    last_job: latest(acc.last_job, r.last_job),
    projects: acc.projects + r.projects, reworked: acc.reworked + r.reworked,
  }));
}

const DAY = 86_400_000;
const dayOf = (iso: string) => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10));
export const daysBetween = (fromIso: string, toIso: string) => Math.round((dayOf(toIso) - dayOf(fromIso)) / DAY);
export const shiftDay = (iso: string, days: number) => {
  const d = new Date(iso + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
};

export type Level = "미사용" | "과소사용" | "초과사용" | "정상" | "판정 불가";
export type Forecast = "부족 예상" | "잔여 예상" | "정상" | "판정 불가";
export type Alert = { key: string; detail: string };
export type Diagnosis = {
  level: Level; levelDetail: string;
  /** 계약 끝까지 이 속도면 — 누적 + 월평균 소진 × 남은 달(일수 ÷ 30.4, 보름 남았으면 반 달). */
  forecast: Forecast; forecastDetail: string; projected: number | null;
  usedPct: number | null; pacePct: number | null;
  lastActivity: string | null; daysIdle: number | null; idleTone: "ok" | "warn" | "danger";
  /** 월평균 소진 = 누적 ÷ 사용 개월 수(첫 소진부터 스냅샷 시각까지, 최소 1개월). */
  remaining: number | null; avgMonthly: number; monthsUsed: number | null; runwayMonths: number | null;
  failRate: number | null;
  /** 품질뿐이다 — 무활동은 「마지막 작업」의 색이, 부족·잔여는 「크레딧 사용 전망」이 말한다 (2026-09-16). */
  alerts: Alert[];
  /** 계약이 스냅샷의 크레딧 기록(2025-12-01~)보다 먼저 시작해 누적이 덜 잡힌다. */
  partial: boolean;
};

/**
 * 판정. `asOf` 는 **스냅샷 시각**(YYYY-MM-DD)입니다 — 벽시계가 아닙니다. 스냅샷이 사흘
 * 묵었으면 「3일 전」도 「30일 무활동」도 그 시각 기준이고, 화면은 그 시각을 도장으로 찍습니다.
 * `rules` 는 서버에 저장된 문턱(`Rules`) — 수주 고객 화면과 영업 인사이트가 같은 값을 넘깁니다.
 */
export function diagnose(contract: Contract, s: SpaceSummary, asOf: string, globalFailRate: number | null,
                         creditsFrom: string | null, rules: DiagnoseRules): Diagnosis {
  const start = contract.plan_starts_on ?? contract.starts_on;
  const end = contract.plan_ends_on ?? contract.ends_on;
  const credits = contract.credits ?? null;

  // 경과율 — 플랜 기간 기준(계약 기간이 아닙니다: MRR 이 그렇게 나뉘는 것과 같은 이유).
  let pacePct: number | null = null;
  let monthsLeft: number | null = null;
  if (start && end && end > start) {
    const total = daysBetween(start, end);
    const gone = Math.min(total, Math.max(0, daysBetween(start, asOf)));
    pacePct = Math.round((gone / total) * 100);
    // 남은 달은 일수로 잰다 — 보름 남았으면 반 달. 달력 달로 세면 이번 달이 통째로 한 달이 된다.
    monthsLeft = Math.max(0, (total - gone) / 30.4);
  }
  const usedPct = credits && credits > 0 ? Math.round((s.used_total / credits) * 100) : null;
  const partial = !!(start && creditsFrom && start < creditsFrom.slice(0, 10));

  const lastActivity = [s.last_use, s.last_job].filter(Boolean).sort().pop()?.slice(0, 10) ?? null;
  const daysIdle = lastActivity ? Math.max(0, daysBetween(lastActivity, asOf)) : null;
  // 7일 이내 초록 · 7일 넘어 30일 미만 주황 · 30일 이상 빨강 (2026-09-16 운영자). 목록과 상세가 같은 색.
  const idleTone = daysIdle === null || daysIdle >= RULE.idleDays ? "danger" : daysIdle > RULE.warnDays ? "warn" : "ok";

  const remaining = credits !== null ? credits - s.used_total : null;
  // 월평균 소진 = 누적 ÷ 사용 개월 수. 사용 개월 수는 **첫 소진**(없으면 플랜 시작)부터 스냅샷 시각까지이고,
  // 한 달이 안 됐으면 한 달로 센다 — 열흘 쓴 값을 세 배로 부풀려 전망하지 않는다. 최근 30일이 아니다:
  // 한 달 쉬면 전망이 0 이 되고 한 달 몰아 쓰면 「부족」이 되던 것을 평균으로 편다 (2026-09-17 운영자).
  // 스냅샷 기록 시작(2025-12-01)보다 앞은 어차피 누적에 없어 그 날부터 센다.
  const usedFrom = [s.first_use?.slice(0, 10) ?? start, creditsFrom?.slice(0, 10)].filter((v): v is string => !!v).sort().pop() ?? null;
  const monthsUsed = usedFrom && usedFrom <= asOf ? Math.max(1, daysBetween(usedFrom, asOf) / 30.4) : null;
  const avgMonthly = monthsUsed ? s.used_total / monthsUsed : 0;
  const runwayMonths = remaining !== null && avgMonthly > 0 ? remaining / avgMonthly : null;

  const jobs = s.jobs_ok + s.jobs_failed;
  const failRate = jobs > 0 ? (s.jobs_failed / jobs) * 100 : null;

  const cannot = !credits ? "계약 크레딧이 없습니다" : pacePct === null ? "플랜 기간이 없습니다"
    : partial ? `스냅샷의 소진 기록이 ${creditsFrom?.slice(0, 10)} 부터라 그 앞 소진이 빠져 있습니다` : null;

  // 사용 수준 — 누적 소진율을 계약 경과율(소진율 미터의 세로선)과 견준다. 「지금까지 쓴 만큼이 지난
  // 기간에 맞나」이고, 최근 30일은 안 본다 — 그건 아래 「전망」의 재료다 (2026-09-17 운영자).
  let level: Level; let levelDetail: string;
  if (s.used_30d <= 0) {
    level = "미사용"; levelDetail = "최근 30일 소진 0";
  } else if (cannot) {
    level = "판정 불가"; levelDetail = cannot;
  } else {
    const gap = usedPct! - pacePct!;
    level = gap <= -rules.gapPp ? "과소사용" : gap >= rules.gapPp ? "초과사용" : "정상";
    levelDetail = `소진 ${usedPct}% / 경과 ${pacePct}% (${gap > 0 ? "+" : ""}${gap}%p)`;
  }

  // 크레딧 사용 전망 — 누적 + 월평균 × 남은 달 이 계약 크레딧의 115%(`shortPct`) 이상이면 부족, 85%(`leftPct`)
  // 이하면 잔여. 서버 범위가 잔여 ≤ 100 ≤ 부족이라 둘이 겹치는 것은 둘 다 100 일 때뿐이고, 그때는 부족이 먼저다.
  let forecast: Forecast; let forecastDetail: string; let projected: number | null = null;
  if (cannot) {
    forecast = "판정 불가"; forecastDetail = cannot;
  } else {
    projected = Math.round(s.used_total + avgMonthly * monthsLeft!);
    const pct = Math.round((projected / credits!) * 100);
    forecast = pct >= rules.shortPct ? "부족 예상" : pct <= rules.leftPct ? "잔여 예상" : "정상";
    forecastDetail = `예상 소진 ${num(projected)} / 계약 ${num(credits!)} (${pct}%) · 월평균 ${num(Math.round(avgMonthly))} × ${monthsLeft!.toFixed(1)}개월 남음`;
  }

  // 품질 — 실패율이 전사 평균의 두 배. 재작업률 조건은 뺐다 (2026-09-16 운영자: 「아예 삭제」).
  const alerts: Alert[] = [];
  if (failRate !== null && globalFailRate && jobs >= RULE.minJobs && failRate >= globalFailRate * RULE.failMult) {
    alerts.push({ key: "품질", detail: `실패율 ${failRate.toFixed(1)}% (전사 ${globalFailRate.toFixed(1)}%의 ${(failRate / globalFailRate).toFixed(1)}배)` });
  }

  return { level, levelDetail, forecast, forecastDetail, projected, usedPct, pacePct, lastActivity, daysIdle, idleTone,
    remaining, avgMonthly, monthsUsed, runwayMonths, failRate, alerts, partial };
}

const num = (v: number) => v.toLocaleString("ko-KR");

/**
 * 분할 지급 계약에서 **아직 지급되지 않은 구간**이 소진율 바의 어디서 시작하는지 —
 * 지급된 크레딧의 비율(%)입니다. 회차가 하나면 null: 바 전체가 지급된 몫이라 그을 선이
 * 없습니다 (2026-09-21 운영자: 「분할 지급할때가 있잖아 … 미지급된거라는거 표시」).
 *
 * 합은 **서버가 센 값**(`granted_credits`)을 그대로 읽습니다. 바로 위 「미지급 크레딧」
 * 칸이 같은 값을 그리므로 여기서 `credit_grants` 를 다시 더하면 한 화면의 두 숫자가 서로
 * 다른 정의를 갖게 됩니다 — 이 저장소가 이미 세 번 겪은 자리입니다.
 *
 * `granted_credits > 0` 이 꼭 필요합니다: `_reseed_credit_grants` 가 계약마다 회차를
 * `done=False` 로 깔아 두므로 아무도 완료 표시를 안 한 계약은 0 이고, 그때 바가 통째로
 * 미지급으로 칠해집니다 — 「지급이 안 됐다」가 아니라 「아직 안 적었다」인데.
 */
export function grantedPct(contract: Contract): number | null {
  const credits = contract.credits;
  const granted = contract.granted_credits;
  if (contract.credit_grants.length <= 1 || !credits) return null;
  if (granted <= 0 || granted >= credits) return null;
  return Math.round((granted / credits) * 100);
}

export const levelTone = (level: Level) =>
  level === "미사용" ? "danger" : level === "과소사용" ? "warn" : level === "초과사용" ? "reply"
  : level === "정상" ? "ok" : "neutral";
export const forecastTone = (f: Forecast) =>
  f === "부족 예상" ? "danger" : f === "잔여 예상" ? "warn" : f === "정상" ? "ok" : "neutral";

/** 언어 코드 → 한국어. 스냅샷에는 사람 이름이 없습니다(`language.description` 은 `en-US` 같은 태그). */
const LANG: Record<string, string> = {
  en: "영어", ko: "한국어", ja: "일본어", zh: "중국어", "zh-TW": "중국어(번체)", es: "스페인어", pt: "포르투갈어",
  fr: "프랑스어", de: "독일어", it: "이탈리아어", ru: "러시아어", ar: "아랍어", hi: "힌디어", id: "인도네시아어",
  th: "태국어", vi: "베트남어", tr: "튀르키예어", pl: "폴란드어", nl: "네덜란드어", sv: "스웨덴어", fi: "핀란드어",
  da: "덴마크어", no: "노르웨이어", cs: "체코어", el: "그리스어", he: "히브리어", hu: "헝가리어", ro: "루마니아어",
  uk: "우크라이나어", ms: "말레이어", tl: "필리핀어", bn: "벵골어", ta: "타밀어", fa: "페르시아어", sk: "슬로바키아어",
  bg: "불가리아어", hr: "크로아티아어", sr: "세르비아어", ca: "카탈루냐어", ur: "우르두어", sw: "스와힐리어",
};
export const langName = (code: string) => LANG[code] ?? LANG[code.split("-")[0]] ?? code;
export const pairName = (pair: string) => pair.split(" → ").map((c) => langName(c.trim())).join(" → ");

// ── 기간 채우기 ───────────────────────────────────────────────────────────
// 가공 데이터에는 **소진이 있던 기간만** 있습니다(GROUP BY). 화면은 빈 달을 0 으로 세워야
// 「6월에는 안 썼다」가 보입니다 — 막대가 없는 것과 0 인 것은 다른 이야기입니다.
export type PeriodRow = { period: string; used: number };

const ym = (d: Date) => `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
const ymd = (d: Date) => `${ym(d)}-${String(d.getUTCDate()).padStart(2, "0")}`;

/** `from`(YYYY-MM-DD 또는 null)부터 `to`(스냅샷 날짜)까지 달마다 한 칸. from 이 없으면 첫 기록부터. */
export function fillMonths(rows: PeriodRow[], from: string | null, to: string): PeriodRow[] {
  const have = new Map(rows.map((r) => [r.period, r.used]));
  const first = from?.slice(0, 7) ?? rows[0]?.period ?? to.slice(0, 7);
  const start = first < to.slice(0, 7) ? first : to.slice(0, 7);
  const out: PeriodRow[] = [];
  const cursor = new Date(Date.UTC(+start.slice(0, 4), +start.slice(5, 7) - 1, 1));
  const end = to.slice(0, 7);
  while (ym(cursor) <= end && out.length < 60) {
    out.push({ period: ym(cursor), used: have.get(ym(cursor)) ?? 0 });
    cursor.setUTCMonth(cursor.getUTCMonth() + 1);
  }
  return out;
}

/** 스냅샷 날짜가 속한 주(월요일 시작)까지 최근 `n` 주. 가공 데이터의 주 키도 월요일입니다(date_trunc('week')). */
export function fillWeeks(rows: PeriodRow[], to: string, n = 16): PeriodRow[] {
  const have = new Map(rows.map((r) => [r.period, r.used]));
  const d = new Date(Date.UTC(+to.slice(0, 4), +to.slice(5, 7) - 1, +to.slice(8, 10)));
  const dow = (d.getUTCDay() + 6) % 7; // 월요일 = 0
  d.setUTCDate(d.getUTCDate() - dow - 7 * (n - 1));
  const out: PeriodRow[] = [];
  for (let i = 0; i < n; i++) {
    out.push({ period: ymd(d), used: have.get(ymd(d)) ?? 0 });
    d.setUTCDate(d.getUTCDate() + 7);
  }
  return out;
}

/** 스냅샷 날짜가 이 기간 안에 있나 — 「진행 중」 표시는 그 기간에만 붙습니다. */
export function isCurrentPeriod(period: string, snapshotDate: string, gran: "m" | "w"): boolean {
  if (gran === "m") return period === snapshotDate.slice(0, 7);
  const start = new Date(period + "T00:00:00Z").getTime();
  const at = new Date(snapshotDate + "T00:00:00Z").getTime();
  return at >= start && at < start + 7 * 86_400_000;
}

export const LENGTH_BINS = ["5분 미만", "5–15분", "15–30분", "30분 이상"];

// ── 지급 회차 ↔ 스냅샷 소진 묶음 맞대기 ─────────────────────────────────
// 스냅샷에 지급 원장은 없다. 있는 것은 「어느 지급 묶음(credit_seq)에서 언제부터 얼마나
// 썼나」다. 그래서 우리가 적어 둔 지급 회차마다 **그 날 이후로 엔터프라이즈 묶음의 소진이
// 시작됐는지**를 본다 — 지급이 있었다는 증거이지 지급액은 아니다. **표시만 한다.** 우리
// 기록(`contract_credit_grants`)에는 쓰지 않는다 — 스냅샷 데이터는 서버로 안 간다.
export type BucketLite = { earn_type: string; first_use: string; consumed: number };
export type GrantEvidence =
  | { kind: "future" }                                      // 지급 예정일이 스냅샷 뒤
  | { kind: "seen"; firstUse: string; consumed: number; n: number } // 그 뒤로 소진이 시작된 묶음이 있다 (창 안의 묶음 전부)
  | { kind: "unseen" };                                     // 없다 — 지급이 안 됐거나 아직 안 쓴 것

/**
 * 예정일 2일 전부터 본다(지난 회차는 전부). 회차는 날짜순으로, 묶음은 첫 사용순으로 놓고
 * 앞에서부터 짝을 짓는다. 창은 지급일 3일 전
 * 부터 **다음 회차 전날 또는 45일** 중 이른 쪽까지 — 안 쓴 회차가 다음 회차의 묶음을 가로채면
 * 안 된다. 창 안에 묶음이 여럿이면 전부 그 회차 것이다.
 */
export function matchGrants(
  grants: { id: number; grant_on: string | null }[], buckets: BucketLite[], snapshotAt: string,
): Map<number, GrantEvidence> {
  const out = new Map<number, GrantEvidence>();
  const dated = grants.filter((g) => g.grant_on).slice()
    .sort((a, b) => a.grant_on!.localeCompare(b.grant_on!));
  const pool = buckets.filter((b) => b.earn_type === "enterprise")
    .map((b) => ({ ...b, day: b.first_use.slice(0, 10) })).sort((a, b) => a.day.localeCompare(b.day));
  const used = new Set<number>();
  const shift = shiftDay;
  const horizon = shiftDay(snapshotAt, RULE.leadDays);
  dated.forEach((g, i) => {
    const on = g.grant_on!;
    if (on > horizon) { out.set(g.id, { kind: "future" }); return; }
    const next = dated[i + 1]?.grant_on;
    const lo = shift(on, -3);
    const hi = [shift(on, 45), next ? shift(next, -1) : null].filter(Boolean).sort()[0]!;
    // 창 안의 묶음은 **전부** 이 회차의 것으로 본다 — 한 지급이 묶음 둘로 쪼개지기도 한다.
    const hits = pool.map((b, j) => ({ b, j })).filter(({ b, j }) => !used.has(j) && b.day >= lo && b.day <= hi);
    if (!hits.length) { out.set(g.id, { kind: "unseen" }); return; }
    hits.forEach(({ j }) => used.add(j));
    out.set(g.id, { kind: "seen", firstUse: hits[0].b.day,
      consumed: hits.reduce((a, { b }) => a + b.consumed, 0), n: hits.length });
  });
  return out;
}

// ── 자동 대조 (2026-09-15 운영자 결정: 「바뀌는 건 상관없지, 완전 자동으로」) ──────────
// 스냅샷에 결제·지급 근거가 들어오면 우리 회차를 **자동으로 완료 처리**한다. 여기는 무엇을
// 바꿀지 **정하기만** 하는 순수 함수이고, 실제 쓰기는 `reconcile.ts` 한 곳이다.
export type EvidencePayment = {
  status: string; method: string | null; currency: string | null; amount: number | null; plan_name: string | null;
  paid_date: string | null; failed_date: string | null; failure_code: string | null;
  requested_date: string | null; expire_date: string | null; created_date: string | null;
};
export type SpaceEvidence = { space_seq: number; buckets: BucketLite[] | null; payments: EvidencePayment[] | null };

/** 같은 enterprise 의 스페이스마다 같은 결제 행이 실린다 — 한 번만 센다. */
export function mergeEvidence(rows: SpaceEvidence[]): { buckets: BucketLite[]; payments: EvidencePayment[] } {
  const buckets: BucketLite[] = [];
  const seen = new Set<string>();
  const payments: EvidencePayment[] = [];
  for (const r of rows) {
    for (const b of r.buckets ?? []) buckets.push({ earn_type: "enterprise", first_use: b.first_use, consumed: b.consumed });
    for (const p of r.payments ?? []) {
      const key = [p.status, p.amount, p.currency, p.paid_date, p.requested_date, p.created_date].join("|");
      if (!seen.has(key)) { seen.add(key); payments.push(p); }
    }
  }
  return { buckets, payments };
}

export type PaymentEvidence =
  | { kind: "future" }
  | { kind: "paid"; paidOn: string; amount: number }                  // 금액·기간이 맞는 결제 완료 행 — 자동 적용
  | { kind: "paid-mismatch"; paidOn: string; amount: number }         // 결제는 됐는데 금액이 다르다 — 사람이 본다
  | { kind: "ready"; amount: number; requested: string | null }       // 결제 링크만 발급됨 (미입금)
  | { kind: "failed"; on: string; code: string | null }
  | { kind: "unseen" };

/**
 * 분납 회차 ↔ 국내 결제 행. **예정일 2일 전부터 본다**(지난 회차는 전부, 그보다 먼 회차는
 * 「예정」). 창은 예정일 45일 전 ~ 60일 후 — 앞이 넓은 이유는 선납이다
 * (실측: 두 회차를 같은 날 한꺼번에 낸 고객이 있다). **금액이 1원 안으로 같아야** 자동
 * 적용한다 — 같은 달에 두 건이 있을 때 엉뚱한 회차를 닫으면 되돌리기 전까지 아무도 모른다.
 * 행 하나는 회차 하나에만 붙는다. 원화 계약만 본다(portone 은 KRW).
 */
export function matchPayments(
  payments: { id: number; paid_on: string | null; amount: number | string | null; done: boolean }[],
  currency: string, evidence: EvidencePayment[], snapshotAt: string,
): Map<number, PaymentEvidence> {
  const out = new Map<number, PaymentEvidence>();
  if (currency.toUpperCase() !== "KRW") return out;
  const used = new Set<number>();
  const rows = payments.filter((p) => p.paid_on).slice().sort((a, b) => a.paid_on!.localeCompare(b.paid_on!));
  const horizon = shiftDay(snapshotAt, RULE.leadDays);
  for (const p of rows) {
    const due = p.paid_on!;
    if (due > horizon && !p.done) { out.set(p.id, { kind: "future" }); continue; }
    const lo = shiftDay(due, -45), hi = shiftDay(due, 60);
    const want = Number(p.amount ?? 0);
    const dayOfRow = (e: EvidencePayment) =>
      (e.paid_date ?? e.failed_date ?? e.requested_date ?? e.created_date ?? "").slice(0, 10);
    const inWindow = evidence.map((e, i) => ({ e, i }))
      .filter(({ e, i }) => !used.has(i) && dayOfRow(e) >= lo && dayOfRow(e) <= hi);
    const exact = inWindow.find(({ e }) => e.status === "paid" && e.amount !== null && Math.abs(e.amount - want) <= 1);
    if (exact) {
      used.add(exact.i);
      out.set(p.id, { kind: "paid", paidOn: exact.e.paid_date!.slice(0, 10), amount: exact.e.amount! });
      continue;
    }
    const paidOther = inWindow.find(({ e }) => e.status === "paid");
    if (paidOther) {
      out.set(p.id, { kind: "paid-mismatch", paidOn: paidOther.e.paid_date!.slice(0, 10), amount: paidOther.e.amount ?? 0 });
      continue;
    }
    const failed = inWindow.find(({ e }) => e.failed_date);
    if (failed) { out.set(p.id, { kind: "failed", on: failed.e.failed_date!.slice(0, 10), code: failed.e.failure_code }); continue; }
    const ready = inWindow.find(({ e }) => e.status === "ready");
    if (ready) { out.set(p.id, { kind: "ready", amount: ready.e.amount ?? 0, requested: ready.e.requested_date?.slice(0, 10) ?? null }); continue; }
    out.set(p.id, { kind: "unseen" });
  }
  return out;
}

export type ReconcilePlan = {
  grants: { id: number; firstUse: string; consumed: number; n: number }[];
  payments: { id: number; paidOn: string; amount: number; note: string | null }[];
};
export type ReconcileContract = {
  credit_grants: { id: number; grant_on: string | null; done: boolean }[];
  payments: { id: number; paid_on: string | null; amount: number | string | null; done: boolean; note?: string | null }[];
  currency: string;
};

/** 계약 하나에서 **자동으로 완료 처리할** 회차. 이미 완료인 것은 안 건드린다. */
export function planReconcile(
  contract: ReconcileContract, evidence: { buckets: BucketLite[]; payments: EvidencePayment[] }, snapshotAt: string,
): ReconcilePlan {
  const plan: ReconcilePlan = { grants: [], payments: [] };
  const g = matchGrants(contract.credit_grants, evidence.buckets, snapshotAt);
  for (const grant of contract.credit_grants) {
    const e = g.get(grant.id);
    if (!grant.done && e?.kind === "seen") plan.grants.push({ id: grant.id, firstUse: e.firstUse, consumed: e.consumed, n: e.n });
  }
  const p = matchPayments(contract.payments, contract.currency, evidence.payments, snapshotAt);
  for (const pay of contract.payments) {
    const e = p.get(pay.id);
    if (!pay.done && e?.kind === "paid") plan.payments.push({ id: pay.id, paidOn: e.paidOn, amount: e.amount, note: pay.note ?? null });
  }
  return plan;
}
