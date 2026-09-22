import { Link } from "react-router-dom";
import { kst } from "../lib/format";
import { InteractionItem, type Interaction } from "./InteractionForm";

export type HistoryRecord = Interaction & { record_key: string };

/** The same actual records on all three screens; only the header navigates. */
export function TicketHistoryBox({ subject, at, ticketId, stage, href, records, onEdit, onDelete }: {
  subject: string | null; at?: string | null; ticketId?: string | null;
  stage?: string; href?: string; records: HistoryRecord[];
  onEdit?: (item: Interaction) => void; onDelete?: (item: Interaction) => void;
}) {
  const title = <strong className="t-sm">{subject || "제목 없는 문의"}</strong>;
  return (
    <section className="history-box">
      <div className="row-between wrap" style={{ gap: 8 }}>
        {href ? <Link className="link--plain" to={href}>{title} ›</Link> : title}
        {stage && <span className="tag">{stage}</span>}
      </div>
      <div className="t-xs t-subtle" style={{ marginTop: 4 }}>
        {at ? kst(at) : ""}{ticketId ? ` · #${ticketId}` : ""} · {records.length}건
      </div>
      <div className="history-list" style={{ marginTop: 12 }}>
        {records.length ? records.map((item) => (
          <InteractionItem key={item.record_key} item={item} hideSubject
                           onEdit={onEdit} onDelete={onDelete} />
        )) : <p className="t-sm t-subtle">남아 있는 기록이 없습니다.</p>}
      </div>
    </section>
  );
}
