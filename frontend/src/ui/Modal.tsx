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
}: {
  title: string;
  description?: ReactNode;
  wide?: boolean;
  onClose: () => void;
  children?: ReactNode;
  actions?: ReactNode;
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

  useEffect(() => {
    opener.current = document.activeElement;
    document.body.classList.add("modal-open");
    // First focusable element, or the dialog itself — never leave focus behind the
    // overlay, where a keyboard user cannot see what it is on.
    const focusable = () =>
      Array.from(
        dialog.current?.querySelectorAll<HTMLElement>(
          'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])',
        ) ?? [],
      ).filter((element) => element.offsetParent !== null);
    (focusable()[0] ?? dialog.current)?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        close.current();
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
        if (e.target === e.currentTarget && downOnScrim.current) close.current();
        downOnScrim.current = false;
      }}
    >
      <div
        ref={dialog}
        className={`modal${wide ? " modal--wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
      >
        <h2 className="modal__title">{title}</h2>
        {description && <div className="modal__body">{description}</div>}
        {children}
        <div className="modal__actions">
          <button type="button" className="btn btn--ghost" onClick={onClose}>취소</button>
          {actions}
        </div>
      </div>
    </div>
  );
}
