import { Icon } from "./Icon";
import { Modal } from "./Modal";
import { ActionButton } from "./ActionButton";

/** 「수정하시겠습니까?」 — 저장 **전에** 묻는 확인 창.
 *
 *  이 화면들에는 저장이 끝난 뒤 「수정했습니다」를 띄우는 자리가 여럿 있었습니다. 그건 이미
 *  벌어진 일을 알려 줄 뿐이라, 잘못 누른 사람에게는 아무 쓸모가 없습니다 — 게다가 여기서
 *  고치는 값은 우리 DB 에서 끝나지 않고 허브스팟 티켓과 영업 워크북까지 갑니다. 되돌리려면
 *  세 곳을 되돌려야 합니다.
 *
 *  한 벌로 두는 이유: 확인 창이 자리마다 따로 자라면 어떤 곳은 묻고 어떤 곳은 안 묻는
 *  상태가 생기고, 그건 운영자에게 「이 화면은 위험하고 저 화면은 안전하다」로 읽힙니다.
 *
 *  실제 저장은 `onConfirm` 이 **끝난 뒤** 창을 닫습니다(`ActionButton` 이 그동안 진행
 *  표시를 답니다). 먼저 닫으면 왕복이 도는 몇 초가 아무 표시 없는 구간이 되고, 그 구간이
 *  한 번 더 누르게 만듭니다.
 */
export function ConfirmModal({
  title = "수정하시겠습니까?",
  description,
  confirmLabel = "수정",
  pending = "저장 중",
  onConfirm,
  onClose,
}: {
  title?: string;
  description?: React.ReactNode;
  confirmLabel?: string;
  pending?: string;
  onConfirm: () => Promise<unknown> | unknown;
  onClose: () => void;
}) {
  return (
    <Modal
      title={title}
      description={description}
      onClose={onClose}
      actions={
        <ActionButton
          className="btn btn--ok"
          pending={pending}
          onClick={async () => {
            await onConfirm();
            onClose();
          }}
        >
          <Icon name="check" size={15} /> {confirmLabel}
        </ActionButton>
      }
    />
  );
}
