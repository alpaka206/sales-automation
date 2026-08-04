import type { ReactNode } from "react";
import "./print-sheet.css";

/** An A4 page the operator fills in on screen and prints.
 *
 * No PDF library: the browser's own print dialogue writes a better PDF than a bundled
 * renderer would, handles Korean fonts the machine already has, and adds nothing to the
 * bundle. The print stylesheet drops the console around it — sidebar, buttons, the form
 * that produced the document — so what prints is the sheet and nothing else.
 */
export function PrintSheet({
  title,
  meta,
  actions,
  children,
  footnote,
}: {
  /** Document title, printed at the top of the page. */
  title: string;
  /** Issue date, document number, whatever identifies this copy. */
  meta?: ReactNode;
  /** Buttons shown on screen only. */
  actions?: ReactNode;
  children: ReactNode;
  footnote?: ReactNode;
}) {
  return (
    <>
      <div className="page-header no-print">
        <div><h1 className="page-title">{title}</h1></div>
        <div className="row" style={{ gap: 8 }}>
          {actions}
          <button type="button" className="btn btn--primary" onClick={() => window.print()}>
            인쇄 · PDF 저장
          </button>
        </div>
      </div>

      <div className="sheet">
        <div className="sheet__head">
          <div>
            <img className="sheet__logo" src="/static/logo.png" alt="" />
            <div className="sheet__brand">PERSO</div>
          </div>
          <div className="sheet__title">
            <div>{title}</div>
            {meta && <div className="sheet__meta">{meta}</div>}
          </div>
        </div>
        {children}
        {footnote && <div className="sheet__foot">{footnote}</div>}
      </div>
    </>
  );
}

/** A labelled block of the document: 고객 정보, 계약 조건, … */
export function SheetSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="sheet__section">
      <h2 className="sheet__section-title">{title}</h2>
      {children}
    </section>
  );
}

/** The label/value grid every section of these documents is made of. */
export function SheetFields({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="sheet__fields">
      {rows.map(([label, value]) => (
        <div key={label} className="sheet__field">
          <dt>{label}</dt>
          <dd>{value || "-"}</dd>
        </div>
      ))}
    </dl>
  );
}
