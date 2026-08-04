import type { ReactNode } from "react";

/** What a screen shows while it is waiting, and while it is refreshing.
 *
 * Two different states, and they used to be the same grey block:
 *
 *   FIRST load — nothing to show yet. A skeleton in the SHAPE of what is coming, so the
 *   page does not resize under the operator when the rows land. A single fixed-height
 *   block was a guess at that shape, and every screen guessed a different number.
 *
 *   REFRESH — there IS something to show. Changing a filter used to throw the rows away
 *   and blank the table, because a new filter is a new cache key with no data in it. The
 *   rows an operator was reading stay put now, dimmed, with a spinner saying why.
 */

/** Skeleton rows shaped like the table that is loading. */
export function Loading({ rows = 6, columns = 4 }: { rows?: number; columns?: number }) {
  // The first column is the wide one on every table in this console (a name, a subject),
  // so the placeholder leans that way rather than laying down even stripes.
  const widths = Array.from({ length: columns }, (_, index) =>
    index === 0 ? "58%" : `${34 + ((index * 17) % 30)}%`,
  );
  return (
    <div className="table-wrap" aria-busy="true" aria-label="불러오는 중">
      <table className="table table--fixed">
        <tbody>
          {Array.from({ length: rows }, (_, row) => (
            <tr key={row}>
              {widths.map((width, column) => (
                <td key={column}>
                  <span className="skeleton" style={{ width }} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** For a screen that is not a table — a detail page, a form. */
export function LoadingBlock({ lines = 5 }: { lines?: number }) {
  return (
    <div className="card" aria-busy="true" aria-label="불러오는 중">
      {Array.from({ length: lines }, (_, index) => (
        <span key={index} className="skeleton"
              style={{ width: index === 0 ? "38%" : `${92 - index * 9}%`, height: index === 0 ? 16 : 12 }} />
      ))}
    </div>
  );
}

/** Wraps content that is already on screen while newer content is on its way. */
export function Refreshing({
  active,
  label = "갱신 중",
  children,
}: {
  active: boolean;
  label?: string;
  children: ReactNode;
}) {
  return (
    <div style={{ position: "relative" }}>
      {active && (
        <div className="loading-note" aria-live="polite"
             style={{ position: "absolute", top: 10, right: 14, zIndex: 2 }}>
          <span className="spinner" role="status" />
          {label}
        </div>
      )}
      <div className={active ? "is-refreshing" : undefined}>{children}</div>
    </div>
  );
}
