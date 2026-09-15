// 수주 고객 상세의 사용 현황 세 섹션 — 크레딧 사용 현황 · 작업 성능 · 사용 구성.
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
import { AlertTags, DiagnosisBasis, LevelTag } from "./UsageBits";
import type { RowUsage } from "./useUsage";
import { LENGTH_BINS, RULE, fillMonths, fillWeeks, isCurrentPeriod, pairName, type Diagnosis } from "./usage";

// ── 에이전트 응답 모양 (spaces.go 의 SQL 과 1:1) ─────────────────────────
type Period = { period: string; used: number };
type Bucket = { no: number; earn_type: string; is_free: number; first_use: string; last_use: string; consumed: number; n: number };
export type CreditsData = {
  monthly: Period[] | null; weekly: Period[] | null; daily: Period[] | null; buckets: Bucket[] | null;
  used_total: number | null; rolled_back: number | null; first_use: string | null; last_use: string | null;
};
type JobsData = {
  status: { status: string; n: number }[] | null;
  monthly: { period: string; ok: number; failed: number }[] | null;
  reasons: { reason: string; n: number }[] | null;
  errors: { code: string; n: number }[] | null;
  processing: { n: number; p50: number | null; p90: number | null; max: number | null; per_video_minute: number | null; avg_video_minutes: number | null };
  wait: { n: number; avg: number | null; max: number | null };
  speed: { green: number; red: number };
  concurrency_peak: number | null; concurrency_peak_at: string | null;
  jobs_from: string | null; last_job: string | null;
};
type UsageData = {
  languages: { pair: string; n: number }[] | null; languages_other: number;
  lengths: { bin: string; n: number }[] | null;
  sources: { source: string; n: number }[] | null;
  seats: { seats: number; spaces_found: number; members: number; owners: number; left: number; active_30d: number; active_6m: number };
  members: { rank: number; jobs: number }[] | null;
  extras: { lip_sync: number; total: number; avg_speakers: number | null };
  jobs_from: string | null;
};

const EARN: Record<string, string> = { enterprise: "엔터프라이즈 지급", charge: "충전", join: "가입", event: "이벤트", none: "구분 없음" };
const REASON: Record<string, string> = {
  ENGINE_ERROR: "엔진 오류", AUDIO_PIPELINE_FAILED: "오디오 처리 실패", VIDEO_PIPELINE_FAILED: "영상 처리 실패",
  API_ERROR: "API 오류", TIMEOUT: "시간 초과", "(미기록)": "사유 미기록",
};
const ERRCODE: Record<string, string> = {
  NO_VOICE_DETECTED_VAD: "음성 미검출", CELEBRITY_VOICE_FOUND: "유명인 음성 감지", NO_AUDIO_CHANNEL: "오디오 채널 없음",
  SRT_LENGTH_EXCEED_VIDEO_ERROR: "자막이 영상보다 김", SRT_PARSE_ERROR: "자막 파싱 오류",
  SRT_REVERSE_TIMESTAMP_ERROR: "자막 시각 역순", "(미기록)": "코드 없음",
};
const SOURCE: Record<string, string> = { FILE_UPLOAD: "파일 업로드", YOUTUBE: "YouTube", TIKTOK: "TikTok", GOOGLE_DRIVE: "Google Drive", "(미기록)": "미기록" };

// ── 작은 그림들 ─────────────────────────────────────────────────────────
function BarList({ rows, color, unit = "건" }: { rows: { label: string; n: number; sub?: string }[]; color?: string; unit?: string }) {
  const max = Math.max(1, ...rows.map((r) => r.n));
  if (!rows.length) return <div className="board-empty">해당 없음</div>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
      {rows.map((r) => (
        <div key={r.label} style={{ display: "grid", gridTemplateColumns: "minmax(90px,140px) 1fr 64px", alignItems: "center", gap: 10, fontSize: 12.5 }}>
          <span className="muted" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={r.sub ?? r.label}>{r.label}</span>
          <span style={{ height: 8, borderRadius: 5, background: "var(--line)", overflow: "hidden" }}>
            <span style={{ display: "block", height: "100%", width: `${(r.n / max) * 100}%`, background: color ?? "var(--teal-600)", borderRadius: 5 }} />
          </span>
          <span style={{ textAlign: "right", fontVariantNumeric: "tabular-nums", fontWeight: 600 }}>{num(r.n)}{unit}</span>
        </div>
      ))}
    </div>
  );
}

function Donut({ pct, color, label }: { pct: number; color: string; label: string }) {
  const r = 38, c = 2 * Math.PI * r, on = (c * Math.min(pct, 100)) / 100;
  return (
    <svg viewBox="0 0 96 96" width={110} height={110} role="img" aria-label={label}>
      <circle cx="48" cy="48" r={r} fill="none" stroke="var(--line)" strokeWidth="12" />
      <circle cx="48" cy="48" r={r} fill="none" stroke={color} strokeWidth="12" strokeLinecap="round"
              strokeDasharray={`${on.toFixed(1)} ${c.toFixed(1)}`} transform="rotate(-90 48 48)" />
      <text x="48" y="53" textAnchor="middle" fontSize="17" fontWeight="700" fill="var(--ink)">{label}</text>
    </svg>
  );
}

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
export function CreditUsageSection({ contract, usage, credits, snapshotStamp, snapshotAt, creditsFrom }: {
  contract: Contract; usage: RowUsage; snapshotStamp?: string;
  /** 부모가 한 번 받아 4번(지급 회차 대조)과 여기가 같이 씁니다 — 같은 질의를 두 번 내지 않으려고. */
  credits: { data: SpaceResult<CreditsData> | null; problem: string | null; busy: boolean };
  /** YYYY-MM-DD — 기간 채우기와 「진행 중」의 기준. */
  snapshotAt?: string; creditsFrom?: string | null;
}) {
  const { data, problem, busy } = credits;
  const [gran, setGran] = useState<"m" | "w">("m");

  return (
    <section className="sec" id="sec-usage">
      <div className="sec-head">
        <span className="sec-title">크레딧 사용 현황</span>
        {snapshotStamp && <span className="tag neutral">스냅샷 {snapshotStamp}</span>}
        <div className="sec-actions">
          <span className="muted" style={{ fontSize: 12 }}>단위: 크레딧 (1분 = 60크레딧) · 이 PC 에서 계산</span>
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
              <span className="sub-count">기준선 = 계약 크레딧 ÷ 플랜 개월수{gran === "w" ? " ÷ 4.34" : ""}</span>
              <div className="chips" style={{ marginLeft: "auto" }}>
                <button type="button" className={`chip btn-sm${gran === "m" ? " is-on" : ""}`} onClick={() => setGran("m")}>월별</button>
                <button type="button" className={`chip btn-sm${gran === "w" ? " is-on" : ""}`} onClick={() => setGran("w")}>주별</button>
              </div>
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
            {data && data.data.rolled_back ? (
              <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
                취소(ROLLBACK) {num(data.data.rolled_back)} 크레딧은 뺀 값입니다 · 계산 {data.computed_ms}ms
              </div>
            ) : null}
          </div>

          <div className="panel">
            <div className="sub-head">
              <span className="sub-title">소진 기록으로 본 지급 묶음</span>
              <span className="sub-count">지급액은 스냅샷에 없습니다 — 각 묶음에서 쓴 만큼만 보입니다. 안 쓴 지급은 안 잡힙니다.</span>
            </div>
            {data && (data.data.buckets?.length ? (
              <div className="table-wrap"><table className="mini">
                <thead><tr><th>묶음</th><th>종류</th><th>첫 사용</th><th>마지막 사용</th><th style={{ textAlign: "right" }}>소진</th><th style={{ textAlign: "right" }}>건수</th></tr></thead>
                <tbody>
                  {data.data.buckets.map((b) => (
                    <tr key={b.no}>
                      <td>{b.no}</td>
                      <td>{EARN[b.earn_type] ?? b.earn_type}{b.is_free ? " · 무료" : ""}</td>
                      <td>{fmt(b.first_use.slice(0, 10))}</td>
                      <td>{fmt(b.last_use.slice(0, 10))}</td>
                      <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{num(b.consumed)}</td>
                      <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{num(b.n)}</td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            ) : <Empty text="스냅샷에 이 스페이스의 지급 묶음 기록이 없습니다." />)}
          </div>
        </>
      )}
    </section>
  );
}

const idleWord = (d: number) => (d === 0 ? "오늘" : d === 1 ? "어제" : `${d}일 전`);
const TONE_COLOR: Record<string, string> = { ok: "var(--teal-700)", warn: "var(--amber-fg)", danger: "var(--red-fg)", reply: "var(--indigo-fg)", neutral: "var(--muted)" };

/** 사용 진단 — 사용 수준 / 마지막 작업 / 주의. 목업의 `dg-bar`: 「크레딧」 탭 맨 위, 카드 밖에
 *  서는 줄이라 `.sec` 이 아니라 홀로 선 `.panel` 입니다(테두리·모서리는 같고 머리가 없다). */
export function UsageInsight({ d }: { d: Diagnosis }) {
  const [open, setOpen] = useState(false);
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
  const levelTone = d.level === "미사용" ? "danger" : d.level === "과소사용" ? "warn" : d.level === "초과사용" ? "reply" : d.level === "적정" ? "ok" : "neutral";
  return (
    <div className="panel" style={{ padding: 0, overflow: "hidden", marginBottom: 18 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0,1fr))", gap: 1, background: "var(--line)" }}>
        {cell("사용 수준", d.level, levelTone, d.levelDetail)}
        {cell("마지막 작업", d.daysIdle === null ? "기록 없음" : idleWord(d.daysIdle), d.idleTone,
          d.lastActivity ? fmt(d.lastActivity) : "스냅샷에 소진·작업 기록이 없습니다")}
        {cell("주의", d.alerts.length ? d.alerts.map((a) => <div key={a.key}>{a.key}</div>) : "없음",
          d.alerts.length ? "danger" : "ok",
          d.alerts.length ? d.alerts.map((a) => <div key={a.key}>{a.detail}</div>) : "해당하는 주의 항목이 없습니다")}
      </div>
      <div style={{ padding: "8px 18px", borderTop: "1px solid var(--line-soft)", display: "flex", gap: 8, alignItems: "center" }}>
        <button type="button" className="btn btn-sm" onClick={() => setOpen(!open)}>{open ? "판정 근거 닫기" : "판정 근거"}</button>
        <span className="muted" style={{ fontSize: 11.5 }}>기준일은 스냅샷 시각입니다 — 오늘이 아닙니다.</span>
      </div>
      {open && <div style={{ padding: "0 18px 14px" }}><DiagnosisBasis d={d} rule={RULE} /></div>}
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
        최근 3개월 평균 월 소진 {num(Math.round(d.avgMonthly))}
        {d.runwayMonths !== null && isFinite(d.runwayMonths) ? ` · 이 속도면 약 ${d.runwayMonths.toFixed(1)}개월치` : ""}
        {contract.credits_used !== null ? ` · 손으로 적은 사용량 ${num(contract.credits_used)}` : ""}
      </div>
    </div>
  );
}

// ── 8. 작업 성능 ─────────────────────────────────────────────────────────
export function JobsSection({ pair, contract, usage, failRateAll }: {
  pair: Pair | null; contract: Contract; usage: RowUsage; failRateAll: number | null;
}) {
  const spaces = usage.kind === "ok" ? usage.spaces : [];
  const { data, problem, busy } = useSpaceMetric<JobsData>(pair, "jobs", spaces);
  const d = data?.data;
  const ok = d?.status?.find((s) => s.status === "COMPLETED")?.n ?? 0;
  const failed = d?.status?.find((s) => s.status === "FAILED")?.n ?? 0;
  const rate = ok + failed ? (ok / (ok + failed)) * 100 : null;
  const limit = contract.concurrent_jobs;

  return (
    <section className="sec" id="sec-jobs">
      <div className="sec-head">
        <span className="sec-title">작업 성능</span>
        <span className="tag neutral">최근 6개월 · 스냅샷은 그 앞을 안 담습니다</span>
      </div>
      {usage.kind !== "ok" ? <div className="panel"><Unavailable usage={usage} /></div>
        : busy ? <div className="panel"><Empty text="계산 중…" /></div>
        : problem ? <div className="panel"><Empty text={`가져오지 못했습니다: ${problem}`} /></div>
        : !d ? null : (
        <>
          <div className="panel">
            <div className="sub-head"><span className="sub-title">작업 성공률</span>
              <span className="sub-count">{d.jobs_from ? `${fmt(d.jobs_from.slice(0, 10))} 이후` : ""} · 내보내기 {num(ok + failed)}건</span></div>
            <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1.1fr)", gap: 28, alignItems: "start" }}>
              <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
                {rate === null ? <Empty text="완료·실패 기록이 없습니다." /> : (
                  <>
                    <Donut pct={rate} color={rate >= 94 ? "var(--teal-600)" : "#E4A11B"} label={`${rate.toFixed(1)}%`} />
                    <div style={{ flex: 1, fontSize: 12.5 }}>
                      <div>성공 <b>{num(ok)}</b>건 · 실패 <b>{num(failed)}</b>건</div>
                      {failRateAll !== null && (
                        <div className="muted" style={{ marginTop: 6 }}>
                          전사 평균 성공률 {(100 - failRateAll).toFixed(1)}%
                          {rate < 100 - failRateAll * RULE.failMult ? <b style={{ color: "var(--red-fg)", marginLeft: 6 }}>평균의 {RULE.failMult}배 넘게 실패</b> : null}
                        </div>
                      )}
                      <div className="muted" style={{ marginTop: 6 }}>
                        지연 트랙(RED) {d.speed.green + d.speed.red ? Math.round((d.speed.red / (d.speed.green + d.speed.red)) * 100) : 0}%
                      </div>
                    </div>
                  </>
                )}
              </div>
              <div>
                <div className="sub-title" style={{ marginBottom: 10 }}>실패 사유 분포</div>
                <BarList color="#b45309" rows={(d.reasons ?? []).map((r) => ({ label: REASON[r.reason] ?? r.reason, n: r.n, sub: r.reason }))} />
                {d.errors?.length ? (
                  <div style={{ marginTop: 12 }}>
                    <div className="muted" style={{ fontSize: 11.5, marginBottom: 6 }}>엔진 오류 코드</div>
                    <BarList color="#b45309" rows={d.errors.map((e) => ({ label: ERRCODE[e.code] ?? e.code, n: e.n, sub: e.code }))} />
                  </div>
                ) : null}
              </div>
            </div>
            {d.monthly?.length ? (
              <div style={{ marginTop: 16 }}>
                <div className="muted" style={{ fontSize: 11.5, marginBottom: 6 }}>월별 내보내기</div>
                <div style={{ display: "flex", gap: 6 }}>
                  {d.monthly.map((m) => {
                    const t = m.ok + m.failed, mx = Math.max(1, ...d.monthly!.map((x) => x.ok + x.failed));
                    return (
                      <div key={m.period} style={{ flex: 1, minWidth: 0, textAlign: "center" }} title={`${m.period} · 성공 ${m.ok} · 실패 ${m.failed}`}>
                        <div style={{ height: 40, display: "flex", alignItems: "flex-end" }}>
                          <div style={{ width: "100%", height: `${(t / mx) * 100}%`, background: m.failed ? "linear-gradient(to top, #b45309 " + Math.round((m.failed / Math.max(1, t)) * 100) + "%, var(--teal-600) 0)" : "var(--teal-600)", borderRadius: "4px 4px 0 0" }} />
                        </div>
                        <div className="muted" style={{ fontSize: 10.5, marginTop: 4 }}>{m.period.slice(2)}</div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : null}
          </div>

          <div className="panel" style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0,1fr))", gap: 28 }}>
            <div>
              <div className="sub-head"><span className="sub-title">작업 처리 시간</span>
                <span className="sub-count">시작 → 마지막 갱신 · 완료 {num(d.processing.n)}건</span></div>
              {d.processing.n ? (
                <>
                  <div className="stat-row" style={{ marginTop: 6, paddingTop: 0, borderTop: 0 }}>
                    <Stat label="중앙값" value={`${d.processing.p50}분`} />
                    <Stat label="상위 10%" value={`${d.processing.p90}분`} />
                    <Stat label="최장" value={`${d.processing.max}분`} />
                    <Stat label="영상 1분당" value={d.processing.per_video_minute !== null ? `${d.processing.per_video_minute}분` : "—"} sub={d.processing.avg_video_minutes !== null ? `평균 영상 ${d.processing.avg_video_minutes}분` : undefined} />
                  </div>
                  <div className="muted" style={{ fontSize: 12, marginTop: 12 }}>
                    {d.wait.n ? `대기(큐) 기록 ${num(d.wait.n)}건 · 평균 ${d.wait.avg}분 · 최장 ${d.wait.max}분` : "대기(큐) 기록 없음 — 곧바로 처리됐습니다"}
                  </div>
                  <div className="muted" style={{ fontSize: 11.5, marginTop: 4 }}>완료 시각 컬럼이 없어 마지막 갱신 시각으로 잰 값입니다.</div>
                </>
              ) : <Empty text="완료 기록이 없습니다." />}
            </div>
            <div>
              <div className="sub-head"><span className="sub-title">동시 처리</span><span className="sub-count">최근 6개월 피크</span></div>
              {d.concurrency_peak === null ? <Empty text="기록이 없습니다." /> : (() => {
                const peak = d.concurrency_peak, over = limit !== null && peak > limit;
                const scale = Math.max(peak, limit ?? 0, 1) * 1.1;
                return (
                  <>
                    <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
                      <span className="meter-num" style={{ color: over ? "var(--red-fg)" : undefined }}>{peak}건</span>
                      <span className="meter-note">
                        {limit === null ? "한도 미입력 (계약 폼의 Concurrent Jobs)"
                          : over ? <b style={{ color: "var(--red-fg)" }}>한도 {limit}건 · {peak - limit}건 초과</b>
                          : `한도 ${limit}건 · ${Math.round((peak / limit) * 100)}% 사용`}
                      </span>
                    </div>
                    <div className="meter-track" style={{ position: "relative", overflow: "visible", height: 22 }}>
                      <div style={{ position: "absolute", inset: "0 auto 0 0", width: `${(Math.min(peak, limit ?? peak) / scale) * 100}%`, background: "var(--teal-600)", borderRadius: 5 }} />
                      {over && <div style={{ position: "absolute", top: 0, bottom: 0, left: `${(limit! / scale) * 100}%`, width: `${((peak - limit!) / scale) * 100}%`, background: "var(--red-fg)", borderRadius: "0 5px 5px 0" }} />}
                      {limit !== null && <div style={{ position: "absolute", top: -6, bottom: -6, left: `${(limit / scale) * 100}%`, width: 2, background: "var(--ink)" }} title={`한도 ${limit}`} />}
                    </div>
                    <div className="muted" style={{ fontSize: 11.5, marginTop: 8 }}>
                      피크 시각 {d.concurrency_peak_at ? d.concurrency_peak_at.slice(0, 16) : "—"} · 시작~갱신 구간이 겹친 수로 셌습니다
                    </div>
                  </>
                );
              })()}
            </div>
          </div>
        </>
      )}
    </section>
  );
}

function Stat({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
  return (
    <div>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub && <div style={{ fontSize: 11.5, color: "var(--faint)", marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

// ── 9. 사용 구성 ─────────────────────────────────────────────────────────
export function MixSection({ pair, usage }: { pair: Pair | null; usage: RowUsage }) {
  const spaces = usage.kind === "ok" ? usage.spaces : [];
  const { data, problem, busy } = useSpaceMetric<UsageData>(pair, "usage", spaces);
  const d = data?.data;
  return (
    <section className="sec" id="sec-mix">
      <div className="sec-head">
        <span className="sec-title">사용 구성</span>
        <span className="tag neutral">최근 6개월 내보내기 기준</span>
      </div>
      {usage.kind !== "ok" ? <div className="panel"><Unavailable usage={usage} /></div>
        : busy ? <div className="panel"><Empty text="계산 중…" /></div>
        : problem ? <div className="panel"><Empty text={`가져오지 못했습니다: ${problem}`} /></div>
        : !d ? null : (
        <>
          <div className="panel" style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0,1fr))", gap: 28 }}>
            <div>
              <div className="sub-head"><span className="sub-title">언어쌍 Top 5</span>
                <span className="sub-count">{num((d.languages ?? []).reduce((a, x) => a + x.n, 0) + d.languages_other)}편</span></div>
              <BarList unit="편" rows={[
                ...(d.languages ?? []).map((l) => ({ label: pairName(l.pair), n: l.n, sub: l.pair })),
                ...(d.languages_other > 0 ? [{ label: "기타", n: d.languages_other }] : []),
              ]} />
            </div>
            <div>
              <div className="sub-head"><span className="sub-title">영상 길이 분포</span></div>
              <BarList unit="편" rows={LENGTH_BINS.map((bin) => ({ label: bin, n: d.lengths?.find((l) => l.bin === bin)?.n ?? 0 }))} />
              <div className="sub-head" style={{ marginTop: 16 }}><span className="sub-title">업로드 경로</span></div>
              <BarList unit="편" rows={(d.sources ?? []).map((s) => ({ label: SOURCE[s.source] ?? s.source, n: s.n }))} />
              {d.extras.total ? (
                <div className="muted" style={{ fontSize: 12, marginTop: 10 }}>
                  립싱크 {Math.round((d.extras.lip_sync / d.extras.total) * 100)}%
                  {d.extras.avg_speakers !== null ? ` · 평균 화자 ${d.extras.avg_speakers}명` : ""}
                </div>
              ) : null}
            </div>
          </div>

          <div className="panel">
            <div className="sub-head"><span className="sub-title">좌석 활용률</span>
              <span className="sub-count">
                {d.seats.spaces_found < spaces.length ? `스페이스 ${spaces.length}개 중 ${d.seats.spaces_found}개만 스냅샷에 있음` : `스페이스 ${d.seats.spaces_found}개`}
              </span></div>
            <div className="stat-row" style={{ marginTop: 6, paddingTop: 0, borderTop: 0 }}>
              <Stat label="스페이스 좌석" value={num(d.seats.seats)} sub="space.seat — 멤버 제한 수" />
              <Stat label="등록 멤버" value={num(d.seats.members)} sub={`소유자 ${d.seats.owners}${d.seats.left ? ` · 나감·차단 ${d.seats.left}` : ""}`} />
              <Stat label="최근 30일 사용" value={num(d.seats.active_30d)} sub="내보내기를 한 사람" />
              <Stat label="6개월 사용" value={num(d.seats.active_6m)} />
            </div>
            {d.members?.length ? (
              <div style={{ marginTop: 16 }}>
                <div className="muted" style={{ fontSize: 11.5, marginBottom: 8 }}>멤버별 내보내기 — 이름·이메일은 스냅샷에 없어 순위로만 보입니다</div>
                <BarList rows={d.members.map((m) => ({ label: `멤버 ${m.rank}`, n: m.jobs }))} />
              </div>
            ) : null}
          </div>
        </>
      )}
    </section>
  );
}

export { AlertTags, LevelTag };
