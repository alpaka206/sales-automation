import { useState, type ReactNode } from "react";

/** 누른 버튼이 스스로 말합니다.
 *
 * 예전에는 누르고 나면 화면이 가만히 있거나, 카드 맨 아래에 "저장 중…" 이 떴습니다. 반응이
 * 누른 자리와 다른 곳에 있으면 눌린 건지 안 눌린 건지 알 수 없어서 한 번 더 누르게 되고,
 * 두 번째 클릭은 같은 요청을 하나 더 보냅니다 — 승인이나 발송에서는 그게 두 번 나가는 것과
 * 같습니다. 되는 동안 버튼은 잠기고 라벨이 바뀝니다.
 *
 * 훅과 버튼 두 가지인 이유: 폼의 제출 버튼은 `type="submit"` 이어야 Enter 로도 눌립니다.
 * 그런 자리는 폼이 `useAction` 을 들고 라벨을 직접 바꾸고, 나머지는 `ActionButton` 을 씁니다.
 */
export function useAction<A extends unknown[]>(
  fn: (...args: A) => unknown | Promise<unknown>,
): readonly [(...args: A) => void, boolean] {
  const [busy, setBusy] = useState(false);
  const run = (...args: A) => {
    // 되는 중에 또 누르면 무시합니다. disabled 로도 막지만, Enter 제출은 버튼을 거치지
    // 않으므로 여기가 진짜 문지기입니다.
    if (busy) return;
    // **지금** 부릅니다. 다음 tick 으로 미루면 폼 제출 핸들러의 event.preventDefault() 가
    // 브라우저가 이미 폼을 보낸 뒤에 실행됩니다 — 화면이 통째로 새로 뜹니다.
    let result: unknown;
    try {
      result = fn(...args);
    } catch {
      return;
    }
    if (!(result instanceof Promise)) return;
    setBusy(true);
    result.catch(() => {}).finally(() => setBusy(false));
  };
  return [run, busy] as const;
}

/** 폼의 제출 버튼. `useAction` 이 돌려준 busy 를 그대로 받습니다.
 *
 * `type="submit"` 이라 Enter 로도 눌립니다 — 그래서 ActionButton 처럼 스스로 상태를 들지
 * 않고, 폼이 든 것을 받습니다. 자리를 정하지 않는 것도 이유입니다: 이 콘솔의 제출 버튼은
 * 폼 맨 아래에도 있고, 안내문과 한 줄을 나눠 쓰기도 합니다. */
export function SubmitButton({
  busy,
  pending = "저장 중",
  className = "btn btn--primary",
  children,
  style,
}: {
  busy: boolean;
  pending?: string;
  className?: string;
  children: ReactNode;
  style?: React.CSSProperties;
}) {
  return (
    <button type="submit" className={className} style={style}
            disabled={busy} aria-busy={busy || undefined}>
      {busy ? (
        <>
          <span className="spinner" role="status" /> {pending}
        </>
      ) : (
        children
      )}
    </button>
  );
}

export function ActionButton({
  onClick,
  pending = "처리 중",
  className = "btn",
  children,
  disabled,
  style,
}: {
  onClick: () => unknown | Promise<unknown>;
  /** 되는 동안 버튼에 뜨는 말. "저장 중", "삭제 중", "발송 중". */
  pending?: string;
  className?: string;
  children: ReactNode;
  disabled?: boolean;
  style?: React.CSSProperties;
}) {
  const [run, busy] = useAction(onClick);
  return (
    <button
      type="button"
      className={className}
      style={style}
      disabled={busy || disabled}
      aria-busy={busy || undefined}
      onClick={() => run()}
    >
      {busy ? (
        <>
          <span className="spinner" role="status" /> {pending}
        </>
      ) : (
        children
      )}
    </button>
  );
}
