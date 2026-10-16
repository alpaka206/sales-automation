// 영업 인사이트의 메일 초안 창 — 한 곳 · 카드 공통 · 고른 N곳, 한국어 / English (목업 `영업인사이트.html` 의
// draftModal · openMail · openTplMail · openBulkMail).
//
// **메일은 보내지 않습니다 — 복사만 합니다** (운영자 조건 3: 「메일은 절대 보내지 마. 초안 만들고 복사만」). 이 창에
// 보내기 단추도 메일 프로그램을 여는 링크도 없습니다. 복사는 이 브라우저의 클립보드로 하고, 사람이 자기 메일
// 프로그램에 붙여 넣어 보냅니다.
//
// **이 파일이 「복사했다」를 밖으로 내보내는 유일한 자리입니다** — 「복사」 단추의 `onClick` 안의 `onCopied(…)` 이고
// (그 줄의 열쇠와 초안의 제목만), 스스로 도는 것(효과 훅 · 타이머)이 없습니다. 카드 공통 초안은 기록하지 않습니다 —
// 어느 곳의 일도 아닙니다(`tests/test_usage_data_stays_off_server.py`).
import { useState } from "react";
import type { MarkTarget } from "../../lib/insightMarks";
import type { Rules } from "../won/usage";
import type { ContractRow, Tab, UpRow } from "./judge";
import { NAME_SLOT, draftFor, placeName, type Lang } from "./mail";

export type MailMode =
  | { kind: "row"; tab: Tab; row: UpRow | ContractRow; email: string | null }
  | { kind: "tab"; tab: Tab; title: string }
  | { kind: "bulk"; tab: Tab; rows: (UpRow | ContractRow)[] };

export const targetOf = (tab: Tab, row: UpRow | ContractRow): MarkTarget =>
  "space_seq" in row ? { tab: "up", space_seq: row.space_seq } : { tab: tab as "churn" | "grow", contract_id: row.contractId };

/** 이 브라우저의 클립보드에 넣는다 — 못 넣으면(권한 · 보안 연결이 아님) false. */
async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

const NO_ADDRESS = "받는 사람 이메일은 데이터에 없습니다. 복사한 뒤 직접 입력해 보내세요.";

export function MailDraft({ mode, lang, rules, onLang, onCopied, onSaid, onClose }: {
  mode: MailMode;
  lang: Lang;
  rules: Rules;
  onLang: (lang: Lang) => void;
  /** 「복사했다」 기록 — 그 줄의 열쇠와 초안의 제목만 간다. 메일은 안 보낸다. */
  onCopied: (target: MarkTarget, subject: string) => Promise<unknown>;
  onSaid: (message: string) => void;
  onClose: () => void;
}) {
  const start = draftFor(mode.tab, mode.kind === "row" ? mode.row : null, lang, rules);
  const [subject, setSubject] = useState(start.subject);
  const [body, setBody] = useState(start.body);
  const [copied, setCopied] = useState<Set<number>>(new Set());
  const [error, setError] = useState("");
  const said = (message: string) => () => onSaid(message);
  const failedCopy = () => setError("클립보드에 넣지 못했습니다 — 제목과 본문을 직접 선택해 복사하세요.");
  const logged = (message: string) => [said(message), said(`${message} — 기록은 남기지 못했습니다`)] as const;
  const title = mode.kind === "row" ? `메일 초안 · ${placeName(mode.row)}`
    : mode.kind === "tab" ? `메일 초안 · ${mode.title} 공통` : `메일 초안 · ${mode.rows.length}곳`;
  const usesName = body.includes(NAME_SLOT);
  const textFor = (row: UpRow | ContractRow) => `${subject}\n\n${body.split(NAME_SLOT).join(placeName(row))}`;

  return (
    <div className="imodal" role="dialog" aria-modal="true" aria-label={title}
         onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="mbox">
        <div className="mh">
          <h3>{title}</h3>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span className="lang" role="group" aria-label="언어">
              {(["ko", "en"] as const).map((code) => (
                <button key={code} type="button" className={lang === code ? "on" : ""} onClick={() => onLang(code)}>
                  {code === "ko" ? "한국어" : "English"}
                </button>
              ))}
            </span>
            <button type="button" className="x" aria-label="닫기" onClick={onClose}>×</button>
          </div>
        </div>
        <div className="mb">
          <div className="mrow">
            <label htmlFor="ins-mail-subject">제목</label>
            <input id="ins-mail-subject" value={subject} onChange={(e) => setSubject(e.target.value)} />
          </div>
          <div className="mrow">
            <label htmlFor="ins-mail-body">본문</label>
            <div>
              <textarea id="ins-mail-body" value={body} onChange={(e) => setBody(e.target.value)} />
              {mode.kind === "row" && (
                <div className="hint ok">
                  {mode.email ? `받는 사람: ${mode.email} — 수주 고객에 적힌 주소입니다. 복사한 뒤 메일 프로그램에서 직접 넣어 보내세요.` : NO_ADDRESS}
                </div>
              )}
              {mode.kind === "tab" && <div className="hint ok">이 탭의 기본 문구입니다. 고객별 근거에 맞춘 초안은 표의 각 행에서 여세요.</div>}
              {mode.kind === "bulk" && usesName && <div className="hint ok">{NAME_SLOT}은 복사할 때 곳마다 자동으로 바뀝니다</div>}
            </div>
          </div>
          {mode.kind === "bulk" ? (
            <div className="mrow">
              <label>복사</label>
              <div>
                <div className="rlist">
                  {mode.rows.map((row) => (
                    <div key={row.key} className="rli">
                      <span>{placeName(row)}</span>
                      <button type="button" className="ibtn" disabled={copied.has(row.key)}
                              onClick={() => { void copy(textFor(row)).then((ok) => { if (!ok) return failedCopy(); setCopied(new Set(copied).add(row.key)); void onCopied(targetOf(mode.tab, row), subject).then(...logged(`${placeName(row)} 초안을 복사했습니다`)); }); }}>
                        {copied.has(row.key) ? "복사됨" : "이 곳 초안 복사"}
                      </button>
                    </div>
                  ))}
                </div>
                <div className="hint ok">{NO_ADDRESS}</div>
              </div>
            </div>
          ) : (
            <div className="mrow">
              <label />
              <div>
                {mode.kind === "row" ? (
                  <button type="button" className="ibtn pri"
                          onClick={() => { void copy(`${subject}\n\n${body}`).then((ok) => { if (!ok) return failedCopy(); onClose(); void onCopied(targetOf(mode.tab, mode.row), subject).then(...logged("초안을 복사했습니다")); }); }}>
                    제목·본문 복사
                  </button>
                ) : (
                  <button type="button" className="ibtn pri"
                          onClick={() => { void copy(`${subject}\n\n${body}`).then((ok) => { if (!ok) return failedCopy(); onClose(); onSaid("초안을 복사했습니다"); }); }}>
                    제목·본문 복사
                  </button>
                )}
              </div>
            </div>
          )}
          {error && <div className="hint" role="alert">{error}</div>}
        </div>
        <div className="mf">
          <small>초안은 복사해서 메일 프로그램에서 보냅니다. 시스템이 직접 보내지 않습니다.</small>
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" className="ibtn" onClick={onClose}>닫기</button>
          </div>
        </div>
      </div>
    </div>
  );
}
