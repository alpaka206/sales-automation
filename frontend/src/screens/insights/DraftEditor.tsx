// 「메일 초안」 창의 편집 모드 — 그 카드의 기본 문구(한국어 · English × 제목 · 공통 문단 · 근거별 문단)를 고친다
// (2026-10-16 운영자: 「「메일 초안」 창 안에 「기본 문구 수정」 버튼을 두고 그 창이 편집 모드로 바뀌게. 확인창에 "이 탭의
// 모든 초안에 적용됩니다" 표시」).
//
// **이 파일이 기본 문구를 밖으로 내보내는 유일한 자리입니다** — 확인창의 [바꾸기] 하나(`onClick` 안의 `onSaveDrafts(…)`)
// 이고, 스스로 도는 것(효과 훅 · 타이머)이 없습니다. 내보내는 것은 사람이 칸에 적은 글뿐입니다
// (`tests/test_usage_data_stays_off_server.py`). 저장은 서버의 따로 둔 표(`insight_drafts`)로 가고, 인바운드 답장 AI 는
// 그 표를 읽지 않습니다. 검사는 서버가 마지막 관문이고(`src/db/insight_drafts.check_text`), 여기는 같은 말로 먼저 짚습니다.
import { useState } from "react";
import type { DraftLang, DraftPart, InsightDrafts } from "../../lib/insightDrafts";
import { day } from "./bits";
import type { Tab } from "./judge";
import { NAME_SLOT, REASON_SLOT } from "./mail";

const LANGS: { key: DraftLang; label: string }[] = [{ key: "ko", label: "한국어" }, { key: "en", label: "English" }];
const LIMIT = (part: string) => (part === "subject" ? 200 : part === "body" ? 4000 : 2000);
const TOKEN = /\{[^{}\n]*\}/g;
const keyOf = (p: { lang: string; part: string }) => `${p.lang}.${p.part}`;

/** 서버와 같은 말로 거절한다 — 한 칸의 이유, 없으면 null. */
export function invalidDraft(p: Pick<DraftPart, "lang" | "part" | "label">, raw: string): string | null {
  const where = `${LANGS.find((l) => l.key === p.lang)?.label} ${p.label}`;
  const text = raw.replace(/\r\n?/g, "\n").trim();
  if (!text) return `${where}: 비어 있습니다`;
  if (p.part === "subject" && text.includes("\n")) return `${where}: 제목은 한 줄이어야 합니다`;
  if (text.length > LIMIT(p.part)) return `${where}: ${LIMIT(p.part).toLocaleString("en-US")}자까지 적을 수 있습니다`;
  for (const token of text.match(TOKEN) ?? []) {
    if (token !== NAME_SLOT && token !== REASON_SLOT) {
      return `${where}: 모르는 자리표시 ${token} — 쓸 수 있는 것은 ${NAME_SLOT}, ${REASON_SLOT} 입니다`;
    }
  }
  const count = text.split(REASON_SLOT).length - 1;
  if (p.part === "body" && count !== 1) return `${where}: ${REASON_SLOT} 는 공통 문단에 한 번 들어가야 합니다(지금 ${count}번)`;
  if (p.part !== "body" && count) return `${where}: ${REASON_SLOT} 는 공통 문단에만 쓸 수 있습니다`;
  return null;
}
const same = (a: string, b: string) => a.replace(/\r\n?/g, "\n").trim() === b.replace(/\r\n?/g, "\n").trim();

export function DraftEditor({ tab, tabName, drafts, onSaveDrafts, onDone }: {
  tab: Tab;
  /** 「이탈 방지」 — 창 제목과 확인창에 선다 */
  tabName: string;
  drafts: InsightDrafts;
  onSaveDrafts: (tab: Tab, values: Record<string, string>) => Promise<unknown>;
  /** 저장했으면 true(창이 새 문구로 초안을 다시 만든다), 취소했으면 false. */
  onDone: (saved: boolean) => void;
}) {
  const parts = drafts.parts.filter((p) => p.tab === tab);
  const [draft, setDraft] = useState<Record<string, string>>(() => Object.fromEntries(parts.map((p) => [keyOf(p), p.value])));
  const [lang, setLang] = useState<DraftLang>("ko");
  const [error, setError] = useState("");
  const [confirming, setConfirming] = useState<DraftPart[] | null>(null);
  const [busy, setBusy] = useState(false);

  const edited = parts.filter((p) => p.updated_at).sort((a, b) => (a.updated_at! < b.updated_at! ? 1 : -1))[0];
  const stampLine = edited ? `${day(edited.updated_at)}${edited.updated_by ? ` ${edited.updated_by}` : ""} 수정` : "기본 문구";
  const shown = parts.filter((p) => p.lang === lang);
  const changed = parts.filter((p) => !same(draft[keyOf(p)] ?? "", p.value));

  const reset = () => { setDraft(Object.fromEntries(parts.map((p) => [keyOf(p), p.default]))); setError(""); };
  const review = () => {
    for (const p of parts) {
      const why = invalidDraft(p, draft[keyOf(p)] ?? "");
      if (why) { setLang(p.lang); setError(why); return; }
    }
    if (!changed.length) { setError("바뀐 문구가 없습니다"); return; }
    setError("");
    setConfirming(changed);
  };
  const done = () => { setBusy(false); setConfirming(null); onDone(true); };
  const failed = (reason: unknown) => { setBusy(false); setConfirming(null); setError(`저장하지 못했습니다: ${String(reason)}`); };

  return (
    <>
      <div className="mh">
        <h3>기본 문구 수정 · {tabName}</h3>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span className="lang" role="group" aria-label="고칠 언어">
            {LANGS.map((l) => (
              <button key={l.key} type="button" className={lang === l.key ? "on" : ""} onClick={() => setLang(l.key)}>
                {l.label}
              </button>
            ))}
          </span>
        </div>
      </div>
      <div className="mb">
        <div className="hint ok">
          이 탭의 모든 초안이 이 문구로 만들어집니다 · {stampLine}
        </div>
        <div className="pholders">
          <b>쓸 수 있는 자리표시</b>
          {drafts.placeholders.map((ph) => (
            <div key={ph.token}><code>{ph.token}</code> {ph.help}</div>
          ))}
        </div>
        {shown.map((p) => (
          <div className="mrow" key={keyOf(p)}>
            <label htmlFor={`ins-draft-${keyOf(p)}`}>{p.part === "subject" ? "제목" : p.part === "body" ? "공통 문단" : "근거 문단"}</label>
            <div>
              {p.part === "subject" ? (
                <input id={`ins-draft-${keyOf(p)}`} value={draft[keyOf(p)] ?? ""}
                       onChange={(e) => setDraft({ ...draft, [keyOf(p)]: e.target.value })} />
              ) : (
                <textarea id={`ins-draft-${keyOf(p)}`} className={p.part === "body" ? undefined : "short"}
                          value={draft[keyOf(p)] ?? ""} onChange={(e) => setDraft({ ...draft, [keyOf(p)]: e.target.value })} />
              )}
              {p.part.startsWith("reason.") && <div className="hint ok">{p.label.replace(/^근거 — /, "쓰는 곳: ")}</div>}
              {p.part === "body" && <div className="hint ok">{REASON_SLOT} 자리에 그 곳의 근거 문단이 들어갑니다 — 한 번만 쓰세요.</div>}
            </div>
          </div>
        ))}
        {error && <div className="hint" role="alert">{error}</div>}
      </div>
      <div className="mf">
        <button type="button" className="ibtn" onClick={reset}
                title="이 탭의 한국어 · English 칸을 모두 기본 문구로 채웁니다 — 저장해야 바뀝니다">
          기본 문구로 되돌리기
        </button>
        <div style={{ display: "flex", gap: 8 }}>
          <button type="button" className="ibtn" onClick={() => onDone(false)}>취소</button>
          <button type="button" className="ibtn pri" onClick={review}>저장</button>
        </div>
      </div>

      {confirming && (
        <div className="imodal" role="dialog" aria-modal="true" aria-label="기본 문구를 바꿀까요?"
             onClick={(e) => { if (e.target === e.currentTarget && !busy) setConfirming(null); }}>
          <div className="mbox narrow">
            <div className="mh"><h3>기본 문구를 바꿀까요?</h3></div>
            <div className="mb">
              <div className="hint ok" style={{ color: "var(--text)", fontSize: 13 }}>
                <b>이 탭의 모든 초안에 적용됩니다.</b> ({tabName})
              </div>
              <ul className="tl">
                {confirming.map((p) => (
                  <li key={keyOf(p)}><span className="dt">{LANGS.find((l) => l.key === p.lang)?.label}</span>
                    <span>{p.label}{same(draft[keyOf(p)] ?? "", p.default) ? " — 기본 문구로" : ""}</span></li>
                ))}
              </ul>
              <div className="hint ok">지금 창의 초안은 새 문구로 다시 만들어집니다 — 이번 복사용으로 고친 글은 사라집니다.</div>
            </div>
            <div className="mf">
              <small>변경 기록이 남습니다 — 누가 · 언제 · 바꾸기 전 글</small>
              <div style={{ display: "flex", gap: 8 }}>
                <button type="button" className="ibtn" onClick={() => setConfirming(null)} disabled={busy} autoFocus>취소</button>
                <button type="button" className="ibtn pri" disabled={busy}
                        onClick={() => { setBusy(true); onSaveDrafts(tab, draft).then(done, failed); }}>
                  {busy ? <><span className="spinner" role="status" /> 바꾸는 중</> : "바꾸기"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
