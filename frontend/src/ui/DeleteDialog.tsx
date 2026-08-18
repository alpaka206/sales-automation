import { useState } from "react";
import { Modal } from "./Modal";
import { Icon } from "./Icon";
import { ActionButton } from "./ActionButton";

// 지우기 전에 묻는 창 — 이메일 템플릿과 정책 문서가 같은 것을 씁니다.
//
// 예전에는 버튼 하나가 곧 삭제였고, 그래서 정책 문서 하나가 실제로 사라졌습니다. 그 종류는
// DB 어디에도 사본이 없어 저장소의 씨앗 파일에서 **원본**을 다시 넣는 것이 최선이었고,
// 그 사이 콘솔에서 고친 내용은 돌아오지 않았습니다.
//
// 그래서 확인이 클릭 한 번이 아니라 **타이핑**입니다. "정말요?" 에 예를 누르는 것은 손이
// 기억하는 동작이라 두 번째부터는 읽지 않습니다. 문장을 옮겨 적는 동안에는 무엇을 지우는지
// 읽게 됩니다.
export const DELETE_PHRASE = "이 문서를 삭제하겠습니다.";

export function DeleteDialog({ name, warning, onCancel, onConfirm }: {
  name: string;
  // 이 행을 지우면 남는 것이 무엇인지. 막지 않기로 한 삭제에는 이 문장이 유일한 방어선이라
  // 「7일 뒤 사라집니다」 위에, 빨간 글씨로 둡니다.
  warning?: string;
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
                   <div className="t-sm t-subtle" style={{ marginTop: 6 }}>
                     발송과 초안에서는 <strong>즉시</strong> 빠집니다. 목록에는 7일 동안 남아
                     되돌릴 수 있고, 그 뒤에는 완전히 사라집니다.
                   </div>
                 </div>
               </div>
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
               <Icon name="trash" size={15} /> 삭제
             </ActionButton>
           } />
  );
}
