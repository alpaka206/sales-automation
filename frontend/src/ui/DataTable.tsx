import type { ReactNode } from "react";

/** One table, defined once, that the screens hand columns and rows to.
 *
 * Every screen was declaring the same table-wrap / table / thead / tbody scaffolding and
 * its own copy of the "nothing here" row — six of them, and the copies had already
 * drifted. Two consequences beyond the duplication: a colSpan that stops matching the
 * columns above it, and column widths measured per table, so two tables showing the SAME
 * columns put them in different places (which is what 문의별 참고 and 항상 적용 did).
 *
 * Widths are declared, not measured: a filter that leaves three rows behind must not
 * re-lay-out the columns an operator was reading.
 */
export type Column<T> = {
  /** Header text. Omit for an actions column — the header cell stays empty. */
  label?: ReactNode;
  /** CSS width for the column. Give every column one, or none. */
  width?: string;
  /** Class for the body cells: tnum, td-subtle, mono … */
  className?: string;
  /** Class for the header cell, when it needs one the body cells do not (th-center). */
  headClassName?: string;
  cell: (row: T) => ReactNode;
};

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  empty,
  emptyIcon,
  onRowClick,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T, index: number) => string | number;
  empty: string;
  emptyIcon?: ReactNode;
  /** Makes the whole row the link. Rows get the pointer and the hover the CSS defines. */
  onRowClick?: (row: T) => void;
}) {
  const sized = columns.some((column) => column.width);
  const headed = columns.some((column) => column.label !== undefined);
  return (
    <div className="table-wrap">
      <table className={`table${sized ? " table--fixed" : ""}`}>
        {sized && (
          <colgroup>
            {columns.map((column, index) => (
              <col key={index} style={column.width ? { width: column.width } : undefined} />
            ))}
          </colgroup>
        )}
        {headed && (
          <thead>
            <tr>
              {columns.map((column, index) =>
                column.label === undefined
                  ? <th key={index} />
                  : (
                      <th key={index} scope="col" className={column.headClassName}>
                        {column.label}
                      </th>
                    ),
              )}
            </tr>
          </thead>
        )}
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length}>
                <div className="empty">
                  {emptyIcon && <div className="empty__icon">{emptyIcon}</div>}
                  <div className="empty__text">{empty}</div>
                </div>
              </td>
            </tr>
          ) : (
            rows.map((row, index) => (
              <tr
                key={rowKey(row, index)}
                className={onRowClick ? "is-clickable" : undefined}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
              >
                {columns.map((column, columnIndex) => (
                  <td key={columnIndex} className={column.className}>{column.cell(row)}</td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
