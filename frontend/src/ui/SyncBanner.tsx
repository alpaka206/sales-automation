import { Icon } from "./Icon";

/** What actually happened to a stage move, in three words that are not the same word.
 *
 * "저장됐다" and "연동됐다" are different claims, and the operator has to be able to tell
 * them apart — a move that never reached HubSpot or the sales workbook looks identical
 * on screen otherwise. The server answers `ok | partial | local`; this says which.
 */
export type SyncState = "ok" | "partial" | "local" | null;

const MESSAGES: Record<string, { tone: string; title: string; body?: string }> = {
  ok: { tone: "banner--ok", title: "파이프라인 동기화 완료" },
  partial: {
    tone: "banner--warn",
    title: "일부 외부 시스템 동기화 실패",
    body: "사이트 단계는 저장되었습니다. HubSpot·Google Sheets 연결 상태를 확인한 뒤 다시 이동해 주세요.",
  },
  local: {
    tone: "",
    title: "단계를 저장했습니다 (외부 연동 없음)",
    body: "HubSpot 티켓 ID나 시트 행이 없거나, 출시 전 안전 모드로 외부 쓰기가 차단된 상태입니다. 이 문의의 단계는 이 서비스에만 반영됐습니다.",
  },
};

export function SyncBanner({ state, onDismiss }: { state: SyncState; onDismiss: () => void }) {
  if (!state) return null;
  const message = MESSAGES[state];
  if (!message) return null;
  return (
    <div className={`banner ${message.tone} mb-gap`} role="status">
      <span className="banner__icon"><Icon name={state === "ok" ? "check" : "warn"} size={18} /></span>
      <div>
        <div className="banner__title">{message.title}</div>
        {message.body && <div className="banner__body">{message.body}</div>}
      </div>
      <button type="button" className="btn btn--ghost btn--sm" style={{ marginLeft: "auto" }}
              onClick={onDismiss} aria-label="닫기">
        <Icon name="x" size={14} />
      </button>
    </div>
  );
}

/** Read the outcome off the redirect the write handler answered with. */
export function syncStateFrom(response: Response): SyncState {
  const flag = new URL(response.url, location.origin).searchParams.get("sync");
  return flag === "ok" || flag === "partial" || flag === "local" ? flag : null;
}
