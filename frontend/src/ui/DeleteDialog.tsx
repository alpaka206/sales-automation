import { useState, type ReactNode } from "react";
import { Modal } from "./Modal";
import { Icon } from "./Icon";
import { ActionButton } from "./ActionButton";

// 지우기 전에 묻는 창 — **콘솔의 모든 삭제가 이 한 벌을 씁니다** (2026-09-21 운영자 지시:
// 「모든 삭제 확인 모달 통일해서 재사용하도록 확실하게」).
//
// 예전에는 버튼 하나가 곧 삭제였고, 그래서 정책 문서 하나가 실제로 사라졌습니다. 그 종류는
// DB 어디에도 사본이 없어 저장소의 씨앗 파일에서 **원본**을 다시 넣는 것이 최선이었고,
// 그 사이 콘솔에서 고친 내용은 돌아오지 않았습니다.
//
// 그래서 확인이 클릭 한 번이 아니라 **타이핑**입니다. "정말요?" 에 예를 누르는 것은 손이
// 기억하는 동작이라 두 번째부터는 읽지 않습니다. 문장을 옮겨 적는 동안에는 무엇을 지우는지
// 읽게 됩니다.
//
// **문구에서 「문서」를 뺐습니다.** 지우는 것이 문서만이 아니게 됐습니다 — 지메일 계정,
// 연락처, 계약, 티켓. 「이 문서를 삭제하겠습니다」를 지메일 계정 앞에서 옮겨 적게 하는 것은
// 그 자체로 무엇을 지우는지 흐립니다.
export const DELETE_PHRASE = "삭제하겠습니다";

export function DeleteDialog({
  name, warning, note, rows, confirmLabel = "삭제", onCancel, onConfirm,
}: {
  name: string;
  // 이 행을 지우면 남는 것이 무엇인지. 막지 않기로 한 삭제에는 이 문장이 유일한 방어선이라
  // 이름 바로 아래, 빨간 글씨로 둡니다.
  warning?: string;
  /** **실제로 무슨 일이 일어나는지** — 호출부마다 다릅니다.
   *
   *  여기 「목록에는 7일 동안 남아 되돌릴 수 있고」가 박혀 있었습니다. 이관 0100 이 정책
   *  문서·이메일 템플릿을 하드 삭제로 바꾸면서 거짓이 됐는데(CLAUDE.md 「7일 휴지통은
   *  없다」), 문구가 컴포넌트 안에 있어서 아무도 고칠 자리를 못 찾았습니다. 되돌릴 수 있나
   *  없나는 지우는 대상마다 다른 이야기라 호출부가 적습니다. */
  note?: ReactNode;
  /** 무엇을 지우는지 표로. `won/Confirm` 과 같은 모양입니다 — 「정말요?」만 있으면 무엇이
   *  사라지는지 모르는 채로 누르게 되고, 그게 확인 창이 있으나 마나 해지는 지점입니다. */
  rows?: [string, string][];
  /** 버튼 글자. 기본은 「삭제」이고, 지우는 것이 한 줄이 아닐 때만 바꿉니다(예: 「정리」). */
  confirmLabel?: string;
  onCancel: () => void;
  onConfirm: () => Promise<void> | void;
}) {
  const [typed, setTyped] = useState("");
  const ok = typed.trim() === DELETE_PHRASE;

  return (
    <Modal title="삭제" onClose={onCancel}
           description={
             <>
               <div className="row" style={{ gap: 8, alignItems: "flex-start" }}>
                 <span style={{ color: "var(--danger)", flexShrink: 0, lineHeight: 0 }}>
                   <Icon name="warn" size={18} />
                 </span>
                 <div>
                   <strong>{name}</strong> 을(를) 삭제합니다.
                   {warning && (
                     <div className="t-sm" style={{ marginTop: 6, color: "var(--danger)" }}>
                       {warning}
                     </div>
                   )}
                   {note && (
                     <div className="t-sm t-subtle" style={{ marginTop: 6 }}>{note}</div>
                   )}
                 </div>
               </div>
               {rows?.length ? (
                 <div className="won">
                   <div className="confirm-rows">
                     {rows.map(([label, value]) => (
                       <div key={label} className="confirm-row">
                         <span className="l">{label}</span>
                         <span className="v">{value}</span>
                       </div>
                     ))}
                   </div>
                 </div>
               ) : null}
               <label className="field-label" htmlFor="del-confirm" style={{ marginTop: 14 }}>
                 계속하려면 <code>{DELETE_PHRASE}</code> 를 그대로 입력하세요
               </label>
               <input className="input" id="del-confirm" value={typed} autoComplete="off"
                      onChange={(e) => setTyped(e.target.value)} placeholder={DELETE_PHRASE} />
             </>
           }
           actions={
             <ActionButton className="btn btn--danger" pending="삭제 중"
                           disabled={!ok} onClick={onConfirm}>
               <Icon name="trash" size={15} /> {confirmLabel}
             </ActionButton>
           } />
  );
}
