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
        onClose();
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
  }, [onClose]);

  return (
    <div className="modal-overlay is-open" onClick={(e) => e.target === e.currentTarget && onClose()}>
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
