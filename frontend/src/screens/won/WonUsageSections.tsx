// 수주 고객 상세의 사용 현황 세 섹션 — 크레딧 사용 현황 · 작업 성능 · 사용 패턴.
//
// 값은 전부 이 PC 의 에이전트(`/v1/spaces/*`)에서 오고 **서버로 가지 않습니다.** 계약 쪽
// 숫자(계약 크레딧·플랜 기간·동시 처리 한도)는 화면이 이미 들고 있는 계약 행에서 읽어
// 브라우저 안에서 맞댑니다.
//
// 스냅샷이 답하지 **못하는** 것은 여기 없습니다(2026-09-15 실측): 결제 내역(B2B 는 Stripe
// 가 아님), 지급액(지급 원장이 스냅샷에 없음). 그 둘은 4·6번 섹션의 우리 기록이 원본입니다.
import { useState } from "react";
import { useSpaceMetric, type Pair, type SpaceResult } from "../../lib/agent";
import type { Contract } from "./shared";
import { fmt, num } from "./shared";
import { TONE_COLOR, idleWord } from "./UsageBits";
import type { RowUsage } from "./useUsage";
import { LENGTH_BINS, fillMonths, fillWeeks, forecastTone, isCurrentPeriod, levelTone, pairName, type Diagnosis } from "./usage";

// ── 에이전트 응답 모양 (spaces.go 의 SQL 과 1:1) ─────────────────────────
type Period = { period: string; used: number };
type Bucket = { no: number; earn_type: string; is_free: number; first_use: string; last_use: string; consumed: number; n: number };
/** 1.3.0 — 작업 실행 한 건. 프로젝트는 번호가 아니라 차례(`project_no`, 첫 소진 순)다 — 식별자는 에이전트가 안 내보낸다.
 *  작업 상세(status·pair·minutes…)는 스냅샷의 작업 창(2026-03-14~) 안에서만 있고 그 앞은 null 이다. */
export type CreditRecord = {
  at: string; space_seq: number; project_no: number; action: "EXECUTE" | "ROLLBACK"; credits: number; steps: number;
  tier: string | null; status: string | null; pair: string | null; minutes: number | null; lip_sync: boolean | null;
  speed: string | null; speakers: number | null;
};
export type CreditsData = {
  monthly: Period[] | null; weekly: Period[] | null; daily: Period[] | null; buckets: Bucket[] | null;
  used_total: number | null; rolled_back: number | null; first_use: string | null; last_use: string | null;
  /** 최신 500건. `records_total` 이 그보다 크면 그만큼만 보인다. */
  records?: CreditRecord[] | null; records_total?: number | null;
};
type JobsData = {
  status: { status: string; n: number }[] | null;
  monthly: { period: string; ok: number; failed: number }[] | null;
  reasons: { reason: string; n: number }[] | null;
  errors: { code: string; n: number }[] | null;
  /** 실패 종류 상위 5 — 엔진 오류 코드가 있으면 그것, 없으면 실패 사유. 나머지는 `fail_other`. */
  fail_kinds: { kind: string; n: number }[] | null; fail_other: number;
  processing: { n: number; avg: number | null; p50: number | null; p90: number | null; max: number | null; per_video_minute: number | null; avg_video_minutes: number | null };
  wait: { n: number; avg: number | null; max: number | null };
  speed: { green: number; red: number };
  concurrency_peak: number | null; concurrency_peak_at: string | null;
  /** 플랜 한도(스냅샷의 plan_option). 스페이스가 스냅샷에 없으면 둘 다 null. */
  limits: { concurrent: number | null; queue: number | null };
  jobs_from: string | null; last_job: string | null;
};
type UsageData = {
  languages: { pair: string; n: number }[] | null; languages_other: number;
  lengths: { bin: string; n: number }[] | null;
  sources: { source: string; n: number }[] | null;
  /** 1.2.0 — 콘텐츠 카테고리 상위 5(project_sensitive.project_category) · 보이스 클론(space_voice 의 살아 있는 줄). */
  categories?: { category: string; n: number }[] | null; categories_other?: number;
  voices?: { voices: number; members: number } | null;
  seats: { seats: number; spaces_found: number; members: number; owners: number; left: number; active_30d: number; active_6m: number };
  members: { rank: number; jobs: number }[] | null;
  extras: { lip_sync: number; total: number; avg_speakers: number | null };
  jobs_from: string | null;
};
const SOURCE: Record<string, string> = {
  FILE_UPLOAD: "파일 업로드", YOUTUBE: "YouTube", TIKTOK: "TikTok", GOOGLE_DRIVE: "Google Drive", "(미기록)": "미기록",
};

const REASON: Record<string, string> = {
  ENGINE_ERROR: "엔진 오류", AUDIO_PIPELINE_FAILED: "오디오 처리 실패", VIDEO_PIPELINE_FAILED: "영상 처리 실패",
  API_ERROR: "API 오류", TIMEOUT: "시간 초과", "(미기록)": "사유 미기록",
};
const ERRCODE: Record<string, string> = {
  NO_VOICE_DETECTED_VAD: "음성 미검출", CELEBRITY_VOICE_FOUND: "유명인 음성 감지", NO_AUDIO_CHANNEL: "오디오 채널 없음",
  SRT_LENGTH_EXCEED_VIDEO_ERROR: "자막이 영상보다 김", SRT_PARSE_ERROR: "자막 파싱 오류",
  SRT_REVERSE_TIMESTAMP_ERROR: "자막 시각 역순", "(미기록)": "코드 없음",
};

/** 기간별 막대 + 기준선. 목업의 drawChart 를 React 로. */
function PeriodBars({ rows, target, targetLabel, unit = "크레딧", isCurrent }: {
  rows: Period[]; target: number | null; targetLabel: string; unit?: string;
  /** 스냅샷 날짜가 속한 기간 — 「진행 중」은 거기에만 붙습니다. 마지막 막대가 아닙니다. */
  isCurrent: (period: string) => boolean;
}) {
  const W = 1000, H = 190, L = 56, R = 16, T = 14, B = 36, x0 = L, x1 = W - R, y0 = T, y1 = H - B;
  const peak = Math.max(1, ...rows.map((r) => r.used), target ?? 0);
  const step = [100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000, 250000].find((s) => s * 4 >= peak * 1.1) ?? 250000;
  const max = step * 4, y = (v: number) => y1 - (v / max) * (y1 - y0);
  const slot = (x1 - x0) / Math.max(1, rows.length), bw = Math.min(46, slot * 0.55);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto" }} role="img" aria-label={`기간별 ${unit}`}>
      {[0, 1, 2, 3, 4].map((i) => (
        <g key={i}>
          <line x1={x0} x2={x1} y1={y(step * i)} y2={y(step * i)} stroke="var(--line-soft)" />
          <text x={x0 - 8} y={y(step * i) + 4} textAnchor="end" fontSize="10" fill="var(--faint)">{num(step * i)}</text>
        </g>
      ))}
      {target !== null && target > 0 && (
        <g>
          <line x1={x0} x2={x1} y1={y(target)} y2={y(target)} stroke="var(--muted)" strokeWidth="2" strokeDasharray="6 5" opacity=".8" />
          <text x={x1} y={y(target) - 8} textAnchor="end" fontSize="10" fontWeight="600" fill="var(--muted)">{targetLabel} {num(target)}</text>
        </g>
      )}
      {rows.map((r, i) => {
        const cx = x0 + slot * (i + 0.5), last = isCurrent(r.period);
        const h = Math.max(0, y1 - y(r.used));
        return (
          <g key={r.period}>
            <title>{r.period} · {num(r.used)} {unit}{target ? ` · 기준 대비 ${r.used - target >= 0 ? "+" : ""}${num(r.used - target)}` : ""}</title>
            {h > 0
              ? <rect x={cx - bw / 2} y={y(r.used)} width={bw} height={h} rx="4" fill={last ? "var(--teal-500)" : "var(--teal-600)"} fillOpacity={last ? 0.5 : 1} stroke={last ? "var(--teal-600)" : "none"} />
              : <rect x={cx - bw / 2} y={y1 - 2} width={bw} height={2} rx="1" fill="var(--red-fg)" />}
            <text x={cx} y={y1 + 16} textAnchor="middle" fontSize={rows.length > 14 ? 8.5 : 10} fill="var(--muted)">{r.period.slice(2)}</text>
            {last && <text x={cx} y={y1 + 28} textAnchor="middle" fontSize="8.5" fill="var(--faint)">진행 중</text>}
          </g>
        );
      })}
      <line x1={x0} x2={x1} y1={y1} y2={y1} stroke="var(--line)" />
    </svg>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="board-empty">{text}</div>;
}

/** 세 섹션이 같은 「에이전트 없음 / 미연결 / 스냅샷에 없음」을 그립니다. */
function Unavailable({ usage }: { usage: RowUsage }) {
  if (usage.kind === "no-agent") {
    return <Empty text="이 PC 의 데이터 에이전트가 연결돼 있지 않습니다 — 「데이터 분석」 화면에서 켭니다. 값은 서버가 아니라 이 PC 에서 계산합니다." />;
  }
  if (usage.kind === "no-space") {
    return <Empty text="이 계약의 「Perso 계정 · 플랜」에 Space ID 가 없어 연결된 스페이스가 없습니다. 적으면 다음 스냅샷부터 채워집니다." />;
  }
  return <Empty text={`Space ${usage.spaces.join(", ")} 이(가) 스냅샷에 없습니다 — 번호를 확인하세요.`} />;
}

// ── 5. 크레딧 사용 현황 ─────────────────────────────────────────────────
export function CreditUsageSection({ contract, usage, credits, snapshotAt, creditsFrom }: {
  contract: Contract; usage: RowUsage;
  /** 부모가 한 번 받아 4번(지급 회차 대조)과 여기가 같이 씁니다 — 같은 질의를 두 번 내지 않으려고. */
  credits: { data: SpaceResult<CreditsData> | null; problem: string | null; busy: boolean };
  /** YYYY-MM-DD — 기간 채우기와 「진행 중」의 기준. */
  snapshotAt?: string; creditsFrom?: string | null;
}) {
  const { data, problem, busy } = credits;
  const [gran, setGran] = useState<"m" | "w">("m");

  return (
    <section className="sec" id="sec-usage">
      {/* 머리 오른쪽은 목업 그대로: 단위 안내, 그 오른쪽에 월별·주별 (2026-09-15 운영자 지시).
          스냅샷 시각 꼬리표와 「이 PC 에서 계산」은 뺐다 — 같은 지시. */}
      <div className="sec-head">
        <span className="sec-title">크레딧 사용 현황</span>
        <div className="sec-actions" style={{ gap: 12 }}>
          <span className="muted" style={{ fontSize: 12 }}>단위: 크레딧 (1분 = 60크레딧)</span>
          <div className="seg">
            <button type="button" className={gran === "m" ? "on" : undefined} onClick={() => setGran("m")}>월별</button>
            <button type="button" className={gran === "w" ? "on" : undefined} onClick={() => setGran("w")}>주별</button>
          </div>
        </div>
      </div>

      {usage.kind !== "ok" ? <div className="panel"><Unavailable usage={usage} /></div> : (
        <>
          <div className="panel">
            <div className="sub-head"><span className="sub-title">크레딧 소진율</span>
              {usage.diagnosis.partial && <span className="mini-chip">2025-12 이전 소진 없음</span>}</div>
            <UsedMeter d={usage.diagnosis} contract={contract} />
          </div>

          <div className="panel">
            <div className="sub-head">
              <span className="sub-title">크레딧 사용량</span>
            </div>
            {busy && <Empty text="계산 중…" />}
            {problem && <Empty text={`가져오지 못했습니다: ${problem}`} />}
            {data && (() => {
              const at = snapshotAt ?? data.snapshot_at.slice(0, 10);
              // 빈 달을 0 으로 세웁니다 — 막대가 없는 것과 0 인 것은 다른 이야기입니다. 시작은
              // 플랜 시작과 스냅샷 기록 시작(2025-12-01) 중 늦은 쪽입니다.
              const planStart = contract.plan_starts_on ?? contract.starts_on;
              const from = [planStart, creditsFrom?.slice(0, 10)].filter(Boolean).sort().pop() ?? null;
              const rows = gran === "m" ? fillMonths(data.data.monthly ?? [], from, at) : fillWeeks(data.data.weekly ?? [], at);
              const perMonth = contract.credits && contract.plan_months ? contract.credits / contract.plan_months : null;
              const target = perMonth === null ? null : gran === "w" ? Math.round(perMonth / 4.34) : Math.round(perMonth);
              return rows.some((r) => r.used > 0)
                ? <PeriodBars rows={rows} target={target} targetLabel={gran === "w" ? "주 기준" : "계약 기준"}
                              isCurrent={(p) => isCurrentPeriod(p, at, gran)} />
                : <Empty text="소진 기록이 없습니다." />;
            })()}
            {/* 취소(ROLLBACK) 차감 안내 · 계산 시간 · 「소진 기록으로 본 지급 묶음」 표는 뺐다
                (2026-09-15 운영자: 「이런 것도 삭제 · 지급 묶음도 필요 없음」). 묶음 데이터는 지급
                회차 표의 상태 줄(소진 시작 날짜)과 자동 대조가 그대로 쓴다. */}
          </div>

          {data && <CreditRecords rows={data.data.records ?? []} total={data.data.records_total ?? 0} spaces={usage.spaces} />}
        </>
      )}
    </section>
  );
}

const STATUS: Record<string, string> = { COMPLETED: "완료", FAILED: "실패", PROCESSING: "처리 중", PENDING: "대기", CANCELED: "취소" };
const PAGE = 20;

/** 작업별 소진 기록 (2026-09-17 운영자: 「크레딧을 사용한 기록들을 작업별로 … 어떤 space 인지 보기 편하게」).
 *  한 줄 = 한 작업 실행. 스페이스가 여럿이면 고르개로 좁힌다. 20건씩 펼친다 — 에이전트가 최신 500건까지 준다. */
function CreditRecords({ rows, total, spaces }: { rows: CreditRecord[]; total: number; spaces: number[] }) {
  const [space, setSpace] = useState<number | null>(null);
  const [shown, setShown] = useState(PAGE);
  const filtered = space === null ? rows : rows.filter((r) => r.space_seq === space);
  const visible = filtered.slice(0, shown);
  const stamp = (at: string) => `${fmt(at.slice(0, 10))} ${at.slice(11, 16)}`;
  return (
    <div className="panel">
      <div className="sub-head">
        <span className="sub-title">크레딧 사용 기록</span>
        <span className="muted" style={{ fontSize: 12 }}>
          {total > rows.length ? `최신 ${num(rows.length)}건 / 전체 ${num(total)}건` : `${num(total)}건`}
          {spaces.length > 1 && (
            <select className="st-sel st-neutral" style={{ marginLeft: 10 }} value={space ?? ""}
                    onChange={(e) => { setSpace(e.target.value ? Number(e.target.value) : null); setShown(PAGE); }}>
              <option value="">모든 space</option>
              {spaces.map((s) => <option key={s} value={s}>space {s}</option>)}
            </select>
          )}
        </span>
      </div>
      {!filtered.length ? <Empty text="소진 기록이 없습니다." /> : (
        <div className="table-wrap">
          <table className="mini">
            <thead><tr>
              <th>일시</th><th>space</th><th>프로젝트</th><th>작업</th><th>상태</th><th className="num">크레딧</th>
            </tr></thead>
            <tbody>
              {visible.map((r, i) => (
                <tr key={`${r.at}-${r.space_seq}-${r.project_no}-${i}`}>
                  <td className="datecell">{stamp(r.at)}</td>
                  <td className="mono">{r.space_seq}</td>
                  <td>#{r.project_no}{r.steps > 1 && <span className="muted" style={{ fontSize: 11.5 }}> · {r.steps}단계</span>}</td>
                  <td>
                    {r.pair ? pairName(r.pair)
                      : <span className="muted" title="스냅샷의 작업 기록은 최근 6개월뿐이라 그 앞의 소진은 무슨 작업이었는지 모릅니다">작업 기록 없음</span>}
                    {r.minutes !== null && <span className="muted" style={{ fontSize: 11.5 }}> · {r.minutes}분</span>}
                    {r.lip_sync && <span className="mini-chip calm" style={{ marginLeft: 6 }}>립싱크</span>}
                    {r.speakers ? <span className="muted" style={{ fontSize: 11.5 }}> · 화자 {r.speakers}</span> : null}
                  </td>
                  <td>{r.action === "ROLLBACK" ? <span className="mini-chip">취소 환급</span> : r.status ? (STATUS[r.status] ?? r.status) : "—"}</td>
                  <td className="num" style={r.credits < 0 ? { color: "var(--teal-700)" } : undefined}>{r.credits < 0 ? `+${num(-r.credits)}` : num(r.credits)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {filtered.length > shown && (
            <div style={{ padding: "8px 11px" }}>
              <button type="button" className="btn btn--sm" onClick={() => setShown(shown + PAGE)}>
                더 보기 ({num(filtered.length - shown)}건 남음)
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** 사용 진단 — 사용 수준 / 크레딧 사용 전망 / 마지막 작업 (2026-09-16 운영자: 「주의」는 아예 삭제).
 *  목업의 `dg-bar`: 「크레딧」 탭 맨 위, 카드 밖에 서는 줄이라 `.sec` 이 아니라 홀로 선 `.panel` 입니다. */
export function UsageInsight({ d }: { d: Diagnosis }) {
  const cell = (k: string, v: React.ReactNode, tone: string, sub: React.ReactNode) => (
    <div style={{ padding: "14px 18px", background: "#fff" }}>
      <div className="field-label">{k}</div>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 7, fontSize: 16, fontWeight: 800, margin: "5px 0 4px", color: TONE_COLOR[tone] }}>
        <span style={{ width: 7, height: 7, borderRadius: 99, background: "currentColor", marginTop: 8, flex: "none" }} />
        <span>{v}</span>
      </div>
      <div className="muted" style={{ fontSize: 11.5, lineHeight: 1.5 }}>{sub}</div>
    </div>
  );
  return (
    <div className="panel" style={{ padding: 0, overflow: "hidden", marginBottom: 18 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0,1fr))", gap: 1, background: "var(--line)" }}>
        {cell("사용 수준", d.level, levelTone(d.level), d.levelDetail)}
        {cell("크레딧 사용 전망", d.forecast, forecastTone(d.forecast), d.forecastDetail)}
        {cell("마지막 작업", d.daysIdle === null ? "기록 없음" : idleWord(d.daysIdle), d.idleTone,
          d.lastActivity ? fmt(d.lastActivity) : "스냅샷에 소진·작업 기록이 없습니다")}
      </div>
      {/* 「판정 근거」 펼침은 뺐습니다(2026-09-15 운영자: 「굳이 따로 안 보여줘도 돼」). 규칙 자체는
          `usage.ts` 의 RULE 과 그 테스트에 있습니다. */}
    </div>
  );
}

/** 소진율 미터 — 채움은 소진, 세로선은 계약 경과. */
function UsedMeter({ d, contract }: { d: Diagnosis; contract: Contract }) {
  const credits = contract.credits ?? 0;
  const used = credits - (d.remaining ?? credits);
  const fill = d.level === "미사용" ? "var(--red-fg)" : d.level === "과소사용" ? "#E4A11B" : d.level === "초과사용" ? "var(--indigo-fg)" : "var(--teal-600)";
  if (!credits) return <Empty text="계약 크레딧이 없어 소진율을 낼 수 없습니다 — 계약 폼의 「계약 크레딧」." />;
  return (
    <div className="meter">
      <div className="meter-head">
        <span className="meter-num">{num(Math.max(0, d.remaining ?? 0))}<span className="muted" style={{ fontSize: 13, fontWeight: 500, marginLeft: 6 }}>남음</span></span>
        <span className="meter-note">소진 <b style={{ color: "var(--ink)", fontSize: 20 }}>{d.usedPct ?? 0}%</b></span>
      </div>
      <div className="meter-track" style={{ position: "relative", overflow: "visible", height: 12 }}>
        <div className="meter-fill" style={{ width: `${Math.min(100, d.usedPct ?? 0)}%`, background: fill }} />
        {d.pacePct !== null && (
          <div title={`계약 경과 ${d.pacePct}%`} style={{ position: "absolute", top: -6, bottom: -6, left: `${d.pacePct}%`, width: 3, background: "var(--ink)", borderRadius: 2, boxShadow: "0 0 0 2px #fff" }} />
        )}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11.5, color: "var(--muted)", marginTop: 8 }}>
        <span>소진 <b>{num(used)}</b></span>
        <span>세로선 = 계약 경과 {d.pacePct ?? "—"}%</span>
        <span>계약 <b>{num(credits)}</b></span>
      </div>
      <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
        월평균 소진 {num(Math.round(d.avgMonthly))}{d.monthsUsed !== null ? ` (${d.monthsUsed.toFixed(1)}개월 기준)` : ""}
        {d.runwayMonths !== null && isFinite(d.runwayMonths) ? ` · 이 속도면 약 ${d.runwayMonths.toFixed(1)}개월치` : ""}
        {contract.credits_used !== null ? ` · 손으로 적은 사용량 ${num(contract.credits_used)}` : ""}
      </div>
    </div>
  );
}

// ── 8. 작업 성능 ─────────────────────────────────────────────────────────
/** 목업의 실패 종류 라벨 — 엔진 오류 코드(자세한 원인)와 실패 사유(큰 갈래)를 한 표에서 씁니다. */
const KIND: Record<string, string> = { ...REASON, ...ERRCODE };

/** 목업의 `donut()` 그대로 — 148px, 반지름 38, 굵기 13, 트랙 #edf3f1, 글자 #14201e. */
function MockDonut({ pct, color, label }: { pct: number; color: string; label: string }) {
  const r = 38, c = 2 * Math.PI * r, on = (c * Math.min(pct, 100)) / 100;
  return (
    <svg className="donut" viewBox="0 0 96 96" role="img" aria-label={label}>
      <circle cx="48" cy="48" r={r} fill="none" stroke="#edf3f1" strokeWidth="13" />
      <circle cx="48" cy="48" r={r} fill="none" stroke={color} strokeWidth="13" strokeLinecap="round"
              strokeDasharray={`${on.toFixed(1)} ${c.toFixed(1)}`} transform="rotate(-90 48 48)" />
      <text x="48" y="53" textAnchor="middle" fontSize={label.length > 3 ? 17 : 19} fontWeight="700" fill="#14201e">{label}</text>
    </svg>
  );
}

/** 목업의 `bars()` — `.blist > .brow (.lb · .bt > .bf · .vv)`. */
function Bars({ rows, cls, empty }: { rows: { k: string; v: number; s: string; hint?: string }[]; cls?: string; empty: string }) {
  if (!rows.length) return <div className="board-empty" style={{ padding: "6px 0" }}>{empty}</div>;
  const max = Math.max(1, ...rows.map((r) => r.v));
  return (
    <div className={`blist${cls === "pct" ? " pct" : ""}`}>
      {rows.map((r) => (
        <div className="brow" key={r.k}>
          <div className="lb" title={r.hint ?? r.k}>{r.k}</div>
          <div className="bt"><div className={`bf${cls && cls !== "pct" ? ` ${cls}` : ""}`} style={{ width: `${((r.v / max) * 100).toFixed(1)}%` }} /></div>
          <div className="vv">{r.s}</div>
        </div>
      ))}
    </div>
  );
}

/** 목업의 `pieChart()` — 176px 파이 + 오른쪽 범례(색 · 이름 · N편 · %). 가운데는 합계와 「총 영상」. */
const PIE = ["#0F766E", "#2A9D8F", "#7CC0B4", "#B9DAD3", "#DCE8E6", "#EEF2F1"];
function Pie({ rows, unit = "편", midLabel = "총 영상" }: { rows: { k: string; v: number; hint?: string }[]; unit?: string; midLabel?: string }) {
  const total = rows.reduce((a, r) => a + r.v, 0) || 1;
  const R = 52, C = 2 * Math.PI * R;
  let off = 0;
  const segs = rows.map((r, i) => {
    const len = C * (r.v / total);
    const seg = <circle key={r.k} cx="66" cy="66" r={R} fill="none" stroke={PIE[i % PIE.length]} strokeWidth="21"
                        strokeDasharray={`${len.toFixed(2)} ${(C - len).toFixed(2)}`} strokeDashoffset={(-off).toFixed(2)}
                        transform="rotate(-90 66 66)" />;
    off += len;
    return seg;
  });
  return (
    <div className="piewrap">
      <div className="pie">
        <svg viewBox="0 0 132 132">{segs}</svg>
        <div className="pie-mid"><div><div className="pie-n">{num(rows.reduce((a, r) => a + r.v, 0))}</div><div className="pie-u">{midLabel}</div></div></div>
      </div>
      <div className="lgd">
        {rows.map((r, i) => (
          <div className="lgd-row" key={r.k}>
            <span className="lgd-c" style={{ background: PIE[i % PIE.length] }} />
            <span className="lgd-k" title={r.hint ?? r.k}>{r.k}</span>
            <span className="lgd-v">{num(r.v)}{unit}</span>
            <span className="lgd-p">{Math.round((r.v / total) * 100)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** 목업의 쌓인 막대 + 범례 — 한 줄에 구간 전부, 아래에 색 · 이름 · N편 · %. 0 인 구간은 막대에서만 빠진다(색은 자리를 지킨다). */
function Stacked({ rows, unit = "편" }: { rows: { k: string; v: number }[]; unit?: string }) {
  const total = rows.reduce((a, r) => a + r.v, 0) || 1;
  const pct = (v: number) => Math.round((v / total) * 100);
  return (
    <>
      <div className="stk">
        {rows.map((r, i) => r.v > 0 && (
          <span key={r.k} className={i >= 2 ? "lt" : undefined} style={{ flex: r.v, background: PIE[i % PIE.length] }} title={`${r.k} · ${num(r.v)}${unit}`}>
            {pct(r.v) >= 6 ? `${pct(r.v)}%` : ""}
          </span>
        ))}
      </div>
      <div className="lgd">
        {rows.map((r, i) => (
          <div className="lgd-row" key={r.k}>
            <span className="lgd-c" style={{ background: PIE[i % PIE.length] }} />
            <span className="lgd-k">{r.k}</span>
            <span className="lgd-v">{num(r.v)}{unit}</span>
            <span className="lgd-p">{pct(r.v)}%</span>
          </div>
        ))}
      </div>
    </>
  );
}

/** 카드 머리 — 목업 `.card-head`(제목 + 오른쪽 힌트). */
function Card({ id, title, hint, children }: { id?: string; title: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="sec" id={id}>
      <div className="sec-head">
        <span className="sec-title">{title}</span>
        {hint && <div className="sec-actions">{hint}</div>}
      </div>
      <div className="panel">{children}</div>
    </section>
  );
}

// ── 작업 성능 — 목업의 perf pane 그대로 (2026-09-15 운영자 지시 「100% 일치」) ────────────
// 카드 셋: 작업 성공률(도넛 · 성공/실패 · 이 고객 vs 전체 평균 · 실패 사유 분포) / 작업 처리 시간
// (대기 + 처리 타임라인) / 동시 처리(피크 vs 한도). 값은 전부 이 PC 의 에이전트가 스냅샷에서 센다.
// 목업이 지어낸 곳은 실제 값으로 바꿨다: 전체 평균은 94.1 고정이 아니라 전사 실패율에서, 실패
// 사유는 지어낸 다섯 줄이 아니라 스냅샷의 엔진 오류 코드·실패 사유 상위 5, 「최장」은 ×4.5 가
// 아니라 실제 최장 처리 시간, 동시 처리 한도는 스냅샷의 플랜(plan_option.concurrentJobs).
export function JobsSection({ pair, contract, usage, failRateAll }: {
  pair: Pair | null; contract: Contract; usage: RowUsage; failRateAll: number | null;
}) {
  const spaces = usage.kind === "ok" ? usage.spaces : [];
  const { data, problem, busy } = useSpaceMetric<JobsData>(pair, "jobs", spaces);
  const d = data?.data;
  const ok = d?.status?.find((s) => s.status === "COMPLETED")?.n ?? 0;
  const failed = d?.status?.find((s) => s.status === "FAILED")?.n ?? 0;
  const rate = ok + failed ? (ok / (ok + failed)) * 100 : null;
  const avgAll = failRateAll === null ? null : 100 - failRateAll;

  const gate = usage.kind !== "ok" ? <Unavailable usage={usage} />
    : busy ? <Empty text="계산 중…" />
    : problem ? <Empty text={`가져오지 못했습니다: ${problem}`} />
    : !d ? <Empty text="기록이 없습니다." /> : null;
  if (gate || !d) return <Card id="sec-jobs" title="작업 성공률" hint="최근 6개월 누적">{gate}</Card>;

  // 처리 시간 — 목업의 dia: 평균(대기 + 처리) · 타임라인(대기 · 처리) · 축(0분 … 최장).
  const proc = d.processing.avg ?? 0;
  const wait = d.wait.n && d.wait.avg ? d.wait.avg : 0;
  const total = +(wait + proc).toFixed(1);
  const mx = Math.max(d.processing.max ?? 0, total, 1);
  const pc = (v: number) => Math.max(0, Math.min(100, (v / mx) * 100));

  // 동시 처리 — 한도는 스냅샷의 플랜이 먼저, 없으면 계약 폼의 Concurrent Jobs.
  const peak = d.concurrency_peak ?? 0;
  const lim = d.limits?.concurrent ?? contract.concurrent_jobs;
  const over = lim !== null && peak > lim;
  const cScale = Math.max(peak, lim ?? 0, 1) * 1.1;
  const cp = (v: number) => Math.max(0, Math.min(100, (v / cScale) * 100));

  const fails = [
    ...(d.fail_kinds ?? []).map((k) => ({ k: KIND[k.kind] ?? k.kind, v: k.n, s: `${num(k.n)}건`, hint: k.kind })),
    ...(d.fail_other > 0 ? [{ k: "기타", v: d.fail_other, s: `${num(d.fail_other)}건` }] : []),
  ];

  return (
    <>
      <Card id="sec-jobs" title="작업 성공률" hint="최근 6개월 누적">
        <div className="succ">
          <div className="seat">
            {rate === null ? <Empty text="완료·실패 기록이 없습니다." /> : (
              <>
                <MockDonut pct={rate} color={rate >= 94 ? "#15713a" : "#946005"} label={`${rate.toFixed(1)}%`} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="seat-txt">성공 <b>{num(ok)}</b>건 &nbsp; 실패 <b>{num(failed)}</b>건</div>
                  <div className="cmp">
                    <div className="cmp-r"><span className="cmp-k">이 고객</span>
                      <span className="cmp-t"><span className="cmp-f" style={{ width: `${rate}%`, background: rate >= 94 ? "#15713a" : "#946005" }} /></span>
                      <span className="cmp-v">{rate.toFixed(1)}%</span></div>
                    {avgAll !== null && (
                      <div className="cmp-r"><span className="cmp-k">전체 평균</span>
                        <span className="cmp-t"><span className="cmp-f" style={{ width: `${avgAll}%`, background: "#cbd9d5" }} /></span>
                        <span className="cmp-v" style={{ color: "var(--faint)" }}>{avgAll.toFixed(1)}%</span></div>
                    )}
                  </div>
                </div>
              </>
            )}
          </div>
          <div className="succ-r">
            <h3 className="succ-h">실패 사유 분포</h3>
            <Bars rows={fails} cls="b3" empty="실패가 없습니다." />
          </div>
        </div>
      </Card>

      <div className="g2">
        <Card title="작업 처리 시간" hint="최근 6개월">
          {d.processing.n ? (
            <div className="dia">
              <div className="dia-h"><span className="dia-t">평균<b>{total}분</b></span>
                <span className="dia-r">{wait > 0 ? `평균 대기 ${wait}분 · ` : ""}영상 1분당 {d.processing.per_video_minute ?? "—"}분</span></div>
              <div className="tl">
                {wait > 0 && <div className="tl-seg wait" style={{ left: 0, width: `${pc(wait)}%` }}>대기 {wait}분</div>}
                <div className={`tl-seg proc${wait > 0 ? "" : " only"}`} style={{ left: `${pc(wait)}%`, width: `${pc(proc)}%` }}>처리 {proc}분</div>
                <div className="tl-tick" style={{ left: "100%" }} />
              </div>
              <div className="tl-axis"><span>0분</span><span>최장 {mx}분</span></div>
            </div>
          ) : <Empty text="완료 기록이 없습니다." />}
        </Card>
        <Card title="동시 처리" hint="최근 6개월 피크">
          {d.concurrency_peak === null ? <Empty text="기록이 없습니다." /> : (
            <div className="dia">
              <div className="dia-h"><span className="dia-t">피크<b style={over ? { color: "#b91c1c" } : undefined}>{peak}건</b></span>
                <span className="dia-r">
                  {lim === null ? "한도 미확인 — 스냅샷에 플랜이 없고 계약 폼의 Concurrent Jobs 도 비어 있음"
                    : over ? <b style={{ color: "#b91c1c" }}>한도 {lim}건 · {peak - lim}건 초과</b>
                    : `한도 ${lim}건 · ${Math.round((peak / lim) * 100)}% 사용`}
                </span></div>
              <div className="tl">
                <div className="tl-seg proc" style={{ left: 0, width: `${cp(Math.min(peak, lim ?? peak))}%` }}>{Math.min(peak, lim ?? peak)}</div>
                {over && <div className="tl-seg" style={{ left: `${cp(lim!)}%`, width: `${cp(peak - lim!)}%`, background: "#b91c1c" }}>+{peak - lim!}</div>}
                {lim !== null && <div className="tl-tick" style={{ left: `${cp(lim)}%` }} />}
              </div>
              <div className="tl-axis"><span>0건</span><span>{lim === null ? "" : `한도 ${lim}건`}</span></div>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}

// ── 사용 패턴 — 운영자가 준 그림 그대로 (2026-09-16 「디자인은 완벽히 동일하게」) ────────────
// 언어쌍 Top 5(막대 · N편 · %) · 영상 길이 분포(쌓인 막대 + 범례) / 콘텐츠 카테고리 · 업로드 경로
// (파이 + 범례) / 좌석 활용률(계약 좌석 · 등록 멤버 · 사용, 멤버별 막대) · 보이스 클론(등록 보이스 ·
// 등록한 멤버 · 계약 좌석 대비). 멤버별 막대는 스냅샷의 실제 내보내기 수 — 이름·이메일은 스냅샷에
// 없어 「멤버 1·2·…」 순위로만 선다. 창은 카드의 「최근 30일」과 같다.
// 카테고리는 project_sensitive.project_category, 경로는 project_export_log.upload_source_type, 보이스는
// space_voice — project.generation_type 은 아니다(그건 더빙/립싱크 같은 **프로젝트 종류**이고 보이스
// 클론 343 개의 원본 프로젝트는 338 개가 DUBBING 이라 가를 것이 없다).
export function MixSection({ pair, contract, usage }: { pair: Pair | null; contract: Contract; usage: RowUsage }) {
  const spaces = usage.kind === "ok" ? usage.spaces : [];
  const { data, problem, busy } = useSpaceMetric<UsageData>(pair, "usage", spaces);
  const d = data?.data;
  const gate = usage.kind !== "ok" ? <Unavailable usage={usage} />
    : busy ? <Empty text="계산 중…" />
    : problem ? <Empty text={`가져오지 못했습니다: ${problem}`} />
    : !d ? <Empty text="기록이 없습니다." /> : null;
  if (gate || !d) return <Card id="sec-mix" title="언어쌍 Top 5" hint="최근 6개월 생성 영상">{gate}</Card>;

  const total = (d.languages ?? []).reduce((a, l) => a + l.n, 0) + d.languages_other;
  const share = (v: number) => `${num(v)}편 · ${Math.round((v / (total || 1)) * 100)}%`;
  const langs = [
    ...(d.languages ?? []).map((l) => ({ k: pairName(l.pair), v: l.n, s: share(l.n), hint: l.pair })),
    ...(d.languages_other > 0 ? [{ k: "기타", v: d.languages_other, s: share(d.languages_other) }] : []),
  ];
  const lens = LENGTH_BINS.map((bin) => ({ k: bin, v: d.lengths?.find((l) => l.bin === bin)?.n ?? 0 }));
  const lensTotal = lens.reduce((a, l) => a + l.v, 0);
  const cats = [
    ...(d.categories ?? []).map((c) => ({ k: c.category, v: c.n })),
    ...((d.categories_other ?? 0) > 0 ? [{ k: "기타", v: d.categories_other! }] : []),
  ];
  const sources = (d.sources ?? []).map((s) => ({ k: SOURCE[s.source] ?? s.source, v: s.n, hint: s.source }));
  // 계약 좌석 — 스냅샷의 space.seat 합이 먼저, 없으면 계약 폼의 Account Invitation Limit.
  const seatLimit = d.seats.seats || contract.invite_limit || 0;
  const voices = d.voices ?? { voices: 0, members: 0 };

  return (
    <>
      <div className="g2">
        <Card id="sec-mix" title="언어쌍 Top 5" hint={`최근 6개월 ${num(total)}편`}>
          <Bars rows={langs} cls="pct" empty="내보내기 기록이 없습니다." />
        </Card>
        <Card title="영상 길이 분포" hint={`최근 6개월 ${num(lensTotal)}편`}>
          {lensTotal ? <Stacked rows={lens} /> : <Empty text="내보내기 기록이 없습니다." />}
        </Card>
      </div>
      <div className="g2">
        <Card title="콘텐츠 카테고리" hint="최근 6개월 생성 영상">
          {cats.length ? <Pie rows={cats} /> : <Empty text="내보내기 기록이 없습니다." />}
        </Card>
        <Card title="업로드 경로" hint="최근 6개월 생성 영상">
          {sources.length ? <Pie rows={sources} /> : <Empty text="내보내기 기록이 없습니다." />}
        </Card>
      </div>
      <div className="g2">
        <Card title="좌석 활용률" hint="최근 30일">
          <div className="seat-sum">
            <span><i>계약 좌석</i><b>{num(seatLimit)}</b></span>
            <span><i>등록 멤버</i><b>{num(d.seats.members)}</b></span>
            <span><i>사용</i><b>{num(d.seats.active_30d)}</b></span>
          </div>
          <Bars rows={(d.members ?? []).map((m) => ({ k: `멤버 ${m.rank}`, v: m.jobs, s: `${num(m.jobs)}건` }))}
                empty="최근 30일에 내보내기를 한 멤버가 없습니다." />
        </Card>
        <Card title="보이스 클론" hint="현재 등록 기준">
          <div className="seat-sum bare">
            <span><i>등록 보이스</i><b>{num(voices.voices)}</b></span>
            <span><i>등록한 멤버</i><b>{num(voices.members)}명</b></span>
            <span><i>계약 좌석 대비</i><b>{seatLimit ? `${Math.round((voices.members / seatLimit) * 100)}%` : "—"}</b></span>
          </div>
        </Card>
      </div>
    </>
  );
}

