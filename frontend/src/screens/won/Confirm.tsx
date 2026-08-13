import { Modal } from "../../ui/Modal";
import { ActionButton } from "../../ui/ActionButton";

/** 되돌리기 어려운 값에 붙는 확인 창.
 *
 * 어디에 붙이느냐가 규칙입니다(노션 §6): **바꾸면 파생 수치가 같이 갱신되는 값**입니다 —
 * 크레딧 지급 완료/취소, 결제 상태 변경. 수금율과 다음 결제일이 그 자리에서
 * 달라지므로, 잘못 누르면 화면의 숫자가 조용히 틀어집니다.
 *
 * 반대로 메모·갱신 계획처럼 다시 고치면 그만인 값에는 붙이지 않습니다. 확인 창이 흔해지면
 * 아무도 안 읽습니다.
 *
 * 바꾸는 값을 **표로 보여 줍니다.** "정말 하시겠습니까?" 만 있으면 무엇이 바뀌는지 모르는
 * 채로 누르게 되고, 그게 확인 창이 있으나 마나 해지는 지점입니다.
 */
export function Confirm({ title, rows, note, okLabel, danger, onOk, onClose }: {
  title: string;
  rows?: [string, string][];
  note?: string;
  okLabel: string;
  danger?: boolean;
  onOk: () => Promise<unknown> | unknown;
  onClose: () => void;
}) {
  return (
    <Modal title={title} onClose={onClose}
           description={
             <>
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
               {note}
             </>
           }
           actions={
             // 끝난 뒤에 닫습니다. 먼저 닫으면 "처리 중" 을 볼 자리가 사라지고, 실패해도
             // 목록만 그대로인 채 아무 말이 없습니다.
             <ActionButton className={`btn ${danger ? "btn--danger" : "btn--primary"}`}
                           pending="처리 중"
                           onClick={() => Promise.resolve(onOk()).then(onClose)}>
               {okLabel}
             </ActionButton>
           } />
  );
}
