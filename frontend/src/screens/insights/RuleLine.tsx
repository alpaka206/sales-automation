// 영업 인사이트의 「기준」 줄 — 지금 기준을 보여 주고, 「기준 수정」으로 고쳐 확인 창을 거쳐 저장합니다
// (목업 `영업인사이트.html` 의 renderRule, 2026-10-15 운영자: 「기준값은 서버에 저장하고 화면에서 바꿀 수 있게.
// 누가 언제 바꿨는지 기록」).
//
// **이 파일이 기준을 밖으로 내보내는 유일한 자리입니다** — 확인 창의 [바꾸기] 하나(`onClick` 안의
// `onSaveRules(…)`)이고, 스스로 도는 것(효과 훅 · 타이머)이 없습니다. 내보내는 것은 사람이 칸에 적은 숫자뿐이고,
// 확인 창이 보여 주는 「지금 N곳 → 바꾼 뒤 M곳」은 이 브라우저에서 센 값이라 어디로도 안 갑니다
// (`tests/test_usage_data_stays_off_server.py` 가 고정합니다).
import { useState } from "react";
import type { InsightRule } from "../../lib/insightRules";
import { kst } from "../../lib/format";
import type { Tab } from "./judge";

const num = (v: number) => v.toLocaleString("en-US");
/** 서버와 같은 말로 거절합니다(`src/db/insights.py` 의 parse_rule_value) — 서버가 마지막 관문입니다. */
function invalid(rule: InsightRule, raw: string): string | null {
  const text = raw.replace(/,/g, "").trim();
  if (!/^-?\d+$/.test(text)) return `${rule.label}: 정수만 적을 수 있습니다`;
  const value = Number(text);
  if (value < rule.min || value > rule.max) {
    return `${rule.label}: ${num(rule.min)}~${num(rule.max)} 범위만 가능합니다${rule.why ? ` (${rule.why})` : ""}`;
  }
  return null;
}
const sentence = (rules: InsightRule[], values: Record<string, number>) =>
  rules.map((r) => `${num(values[r.key])} ${r.unit}`).join(" · ");

/** 수주 고객 「사용 진단」이 같이 쓰는 기준(서버의 `shared` — 잔여 예상 · 부족 예상 · 초과사용) 옆의 표시. 확인창에만
 *  적혀 있던 것을 줄에도 늘 보이게 했다(2026-10-16 운영자: 「잔여예상 기준 바꾸면 수주고객도 같이 바뀐다는거 명시하자」). */
const SharedMark = () => (
  <span className="shared" title="이 기준을 바꾸면 수주 고객 화면의 「사용 진단」(사용 상태 배지)도 같이 바뀝니다">
    수주 고객 화면도 같이 바뀜
  </span>
);

export function RuleLine({ tab, title, rules, count, countWith, note, onSaveRules, onSaved }: {
  tab: Tab;
  /** 확인 창의 「{title} 목록과 카드 숫자가 바로 바뀝니다」 */
  title: string;
  /** 이 카드의 기준 — 서버가 준 그대로(이름 · 단위 · 범위 · 지금 값 · 누가 언제). */
  rules: InsightRule[];
  /** 지금 기준으로 이 카드에 선 곳 수 */
  count: number | null;
  /** 이 값으로 바꾸면 몇 곳이 되나 — 이 브라우저에서 다시 센다 */
  countWith: (values: Record<string, number>) => number | null;
  /** 수주 고객 「사용 진단」이 같이 바뀌는 기준이면 그 사실을 적는다 */
  note: string;
  onSaveRules: (tab: Tab, values: Record<string, number>) => Promise<unknown>;
  onSaved: (message: string) => void;
}) {
  const [draft, setDraft] = useState<Record<string, string> | null>(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState<Record<string, number> | null>(null);
  const [busy, setBusy] = useState(false);
  const current = Object.fromEntries(rules.map((r) => [r.key, r.value]));

  const edited = rules.filter((r) => r.updated_at).sort((a, b) => (a.updated_at! < b.updated_at! ? 1 : -1))[0];
  const stampLine = edited
    ? `${kst(edited.updated_at, "date").replace(/\.\s*/g, "-").replace(/-$/, "")}${edited.updated_by ? ` ${edited.updated_by}` : ""} 수정`
    : "기본값";

  const start = () => { setDraft(Object.fromEntries(rules.map((r) => [r.key, String(r.value)]))); setError(""); };
  const cancel = () => { setDraft(null); setError(""); };
  const review = () => {
    if (!draft) return;
    for (const rule of rules) {
      const why = invalid(rule, draft[rule.key] ?? "");
      if (why) { setError(why); return; }
    }
    setError("");
    setPending(Object.fromEntries(rules.map((r) => [r.key, Number(draft[r.key].replace(/,/g, "").trim())])));
  };
  const close = () => { if (!busy) setPending(null); };
  const done = () => { setBusy(false); setPending(null); setDraft(null); onSaved("기준을 바꿨습니다"); };
  const failed = (reason: unknown) => { setBusy(false); setPending(null); setError(`저장하지 못했습니다: ${String(reason)}`); };

  return (
    <div className="rule">
      {draft ? (
        <>
          <span className="rule-fields">
            기준:
            {rules.map((rule, i) => (
              <span key={rule.key}>
                {rule.label}{" "}
                <input type="number" inputMode="numeric" aria-label={rule.label} autoFocus={i === 0}
                       min={rule.min} max={rule.max} step={rule.step} value={draft[rule.key] ?? ""}
                       onChange={(e) => setDraft({ ...draft, [rule.key]: e.target.value })}
                       onKeyDown={(e) => { if (e.key === "Enter") review(); if (e.key === "Escape") cancel(); }} />{" "}
                {rule.unit}
                {rule.shared && <SharedMark />}
              </span>
            ))}
            {error && <span className="err" role="alert">{error}</span>}
          </span>
          <span className="acts">
            <button type="button" className="ibtn" onClick={cancel}>취소</button>
            <button type="button" className="ibtn pri" onClick={review}>저장</button>
          </span>
        </>
      ) : (
        <>
          <span>
            기준:{" "}
            {rules.map((rule, i) => (
              <span key={rule.key}>
                {i > 0 && " · "}{rule.label} <b className="num">{num(rule.value)}</b> {rule.unit}
                {rule.shared && <SharedMark />}
              </span>
            ))}
            <span className="meta">{count === null ? "" : `현재 ${num(count)}곳 · `}{stampLine}</span>
            {error && <span className="err" role="alert"> {error}</span>}
          </span>
          <button type="button" className="ibtn" onClick={start}>기준 수정</button>
        </>
      )}

      {pending && (
        <div className="imodal" role="dialog" aria-modal="true" aria-label="기준을 바꿀까요?"
             onClick={(e) => { if (e.target === e.currentTarget) close(); }}
             onKeyDown={(e) => { if (e.key === "Escape") close(); }}>
          <div className="mbox narrow">
            <div className="mh"><h3>기준을 바꿀까요?</h3></div>
            <div className="mb">
              <div className="kv num">
                <div><small>지금</small><b>{count === null ? "—" : `${num(count)}곳`}</b><small>{sentence(rules, current)}</small></div>
                <div><small>바꾼 뒤</small><b>{(() => { const n = countWith(pending); return n === null ? "—" : `${num(n)}곳`; })()}</b>
                  <small>{sentence(rules, pending)}</small></div>
              </div>
              <div className="hint ok">
                {title} 목록과 카드 숫자가 바로 바뀝니다.
                {note && <><br />{note}</>}
              </div>
            </div>
            <div className="mf">
              <small>변경 기록이 남습니다 — 누가 · 언제 · 무엇을 바꿨는지</small>
              <div style={{ display: "flex", gap: 8 }}>
                <button type="button" className="ibtn" onClick={close} disabled={busy} autoFocus>취소</button>
                <button type="button" className="ibtn pri" disabled={busy}
                        onClick={() => { setBusy(true); onSaveRules(tab, pending).then(done, failed); }}>
                  {busy ? <><span className="spinner" role="status" /> 바꾸는 중</> : "바꾸기"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
