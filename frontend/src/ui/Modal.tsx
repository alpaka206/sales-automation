import { useEffect, useRef, type ReactNode } from "react";

// The accessible dialog, defined ONCE: focus trap, focus restore, Escape, background
// scroll lock (WCAG 2.1 AA). It replaces the old static/a11y.js helper, which the React
// screens had stopped loading — three overlays were hand-rolling a subset of this each.
export function Modal({
  title,
  description,
  wide,
  onClose,
  children,
  actions,
  hideCancel = false,
}: {
  title: string;
  description?: ReactNode;
  wide?: boolean;
  onClose: () => void;
  children?: ReactNode;
  actions?: ReactNode;
  /** 아래 줄의 기본 「취소」를 빼고 그립니다 — **내용이 자기 취소를 이미 들고 있을 때**만.
   *
   *  소통 히스토리 추가 모달이 그렇습니다. 그 폼의 제출 버튼은 `type="submit"` 이라야
   *  Enter 로도 눌리고, 그래서 폼 안에 있어야 합니다. 취소를 저장 왼쪽에 두려면 그것도
   *  같이 폼 안으로 들어와야 하는데, 그러면 아래 기본 취소와 **둘**이 됩니다.
   *
   *  포커스 덫은 그대로 돕니다: 걸리는 것을 취소 버튼 하나로 세지 않고 dialog 안의 모든
   *  포커스 가능한 요소로 세기 때문입니다(위 `focusable`). 이 폼은 입력만 여덟 개입니다. */
  hideCancel?: boolean;
}) {
  const dialog = useRef<HTMLDivElement>(null);
  const opener = useRef<Element | null>(null);

  // 닫기는 **ref 로** 듭니다. 아래 효과가 `[onClose]` 에 걸려 있으면, 매 렌더 새 함수를 주는
  // 호출부(대부분 그렇습니다)에서 효과가 계속 풀렸다 다시 걸립니다. 풀릴 때 정리 코드가
  // 포커스를 여는 버튼으로 되돌리므로 — 폼에 글자를 칠 때마다 포커스가 튑니다. 호출부마다
  // useCallback 으로 감싸 달라고 하면 언젠가 한 곳이 빠지고, 그때 증상은 여기가 아니라
  // 그 화면에서 나타납니다.
  const close = useRef(onClose);
  close.current = onClose;

  /** 뭔가 적어 넣은 폼이 들어 있으면 Escape 와 배경 클릭으로는 닫지 않습니다.
   *
   * 계약 폼은 칸이 서른 개가 넘고, 닫기가 곧 라우트 이동이라 되돌릴 방법이 없습니다.
   * 소통 히스토리 폼은 값을 DOM 에만 들고 있어 더합니다 — 스치듯 누른 Escape 하나에 다 쓴
   * 내용이 말없이 사라집니다. `취소` 버튼은 언제나 닫습니다: 그건 실수로 누르는 자리가
   * 아니고, 그 버튼을 눌렀다면 정말 버리겠다는 뜻입니다.
   *
   * 기준은 **이 창 안에서 한 번이라도 입력했는가** 하나뿐입니다. 값을 비교하는 방법
   * (`value !== defaultValue`)을 먼저 썼는데 안 됩니다: React 는 제어 입력의 `value`
   * **속성**까지 같이 갱신해서 `defaultValue` 가 늘 따라오고, `select` 는 반대로
   * `selected` 속성을 안 달아 갓 연 폼의 드롭다운이 전부 "고쳐졌다" 로 잡힙니다.
   * 입력 이벤트 하나면 제어·비제어 폼 양쪽에서 똑같이 맞습니다. */
  const typed = useRef(false);

  const closeUnlessDirty = () => {
    if (!typed.current) close.current();
  };
  // 아래 효과는 마운트 때 한 번만 돕니다. 그 안의 Escape 처리도 **지금** 의 판단을 써야
  // 하므로 ref 로 넘깁니다.
  const closeDirtyAware = useRef(closeUnlessDirty);
  closeDirtyAware.current = closeUnlessDirty;

  useEffect(() => {
    opener.current = document.activeElement;
    document.body.classList.add("modal-open");
    // First focusable element, or the dialog itself — never leave focus behind the
    // overlay, where a keyboard user cannot see what it is on.
    const focusable = () =>
      Array.from(
        dialog.current?.querySelectorAll<HTMLElement>(
          // iframe 이 들어 있는 이유: 이메일 미리보기 모달은 내용이 iframe 하나뿐입니다.
          // 빼 두면 걸리는 것이 아래 `취소` 버튼 하나라 first === last 가 되고, Tab 이
          // 매번 그 버튼으로 되돌아옵니다 — 60vh 보다 긴 메일은 키보드로 볼 수 없습니다.
          'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),iframe,[tabindex]:not([tabindex="-1"])',
        ) ?? [],
      ).filter((element) => element.offsetParent !== null);
    (focusable()[0] ?? dialog.current)?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeDirtyAware.current();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (items.length === 0) return;
      const [first, last] = [items[0], items[items.length - 1]];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.classList.remove("modal-open");
      (opener.current as HTMLElement | null)?.focus?.();
    };
  }, []);

  // 배경을 **누르고 뗀** 것이 둘 다 배경일 때만 닫습니다.
  //
  // click 하나로 판단하면, 누른 곳과 뗀 곳이 다를 때 브라우저가 그 둘의 공통 조상에 click 을
  // 보내는 성질 때문에 엉뚱하게 닫힙니다 — 여는 버튼을 누른 손이 모달이 뜬 자리에서 떼지면
  // 열리자마자 닫혔습니다("번쩍 했다가 사라져"). 본문 안에서 글자를 드래그하다 배경에서
  // 떼는 경우도 같습니다.
  const downOnScrim = useRef(false);

  return (
    <div
      className="modal-overlay is-open"
      onMouseDown={(e) => { downOnScrim.current = e.target === e.currentTarget; }}
      onClick={(e) => {
        if (e.target === e.currentTarget && downOnScrim.current) closeUnlessDirty();
        downOnScrim.current = false;
      }}
    >
      <div
        ref={dialog}
        onInput={() => { typed.current = true; }}
        className={`modal${wide ? " modal--wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
      >
        <h2 className="modal__title">{title}</h2>
        {description && <div className="modal__body">{description}</div>}
        {children}
        {(!hideCancel || actions) && (
          <div className="modal__actions">
            {!hideCancel && (
              <button type="button" className="btn btn--ghost" onClick={onClose}>취소</button>
            )}
            {actions}
          </div>
        )}
      </div>
    </div>
  );
}
