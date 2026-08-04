import { Link } from "react-router-dom";
import { Icon } from "./Icon";
import { kst } from "../lib/format";

// The awaiting-reply table, defined ONCE — the port of partials/queue_table.html, for
// the same reason that file exists: the dashboard and 회신 및 검토 render the same rows
// and drifted apart when each owned a copy.
export type QueueRow = {
  id: number;
  status: string;
  stage: string;
  subject: string;
  channel: string;
  email: string;
  received_at: string;
  waiting_since: string | null;
};

const STATUS_LABELS: Record<string, string> = {
  pending_approval: "발송 대기",
  drafting: "작성 중",
  draft_failed: "작성 실패",
  send_failed: "발송 실패",
  sent: "발송 완료",
  test_sent: "테스트 발송",
  rejected: "거절",
  superseded: "종료",
  approved: "승인됨",
  delivery_unknown: "확인 필요",
};
const STATUS_TONE: Record<string, string> = {
  pending_approval: "warn",
  drafting: "neutral",
  draft_failed: "danger",
  send_failed: "danger",
  sent: "ok",
  rejected: "neutral",
};

/** Days the customer has waited, and the dot that says how bad that is. */
function priority(now: string, since: string | null) {
  const waited = since ? (Date.parse(now) - Date.parse(since)) / 86_400_000 : 0;
  if (waited <= 1) return ["ok", "1일 이내"] as const;
  if (waited <= 3) return ["warn", "3일 이내"] as const;
  return ["danger", "3일 초과"] as const;
}

export function QueueTable({
  rows,
  now,
  stageLabels,
  emptyText,
}: {
  rows: QueueRow[];
  now: string;
  stageLabels: Record<string, string>;
  emptyText: string;
}) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">상태</th>
            <th scope="col">Stage</th>
            <th scope="col">문의 제목</th>
            {/* One dot wide, centred under its own heading. */}
            <th scope="col" className="th-center">우선순위</th>
            <th scope="col">채널</th>
            <th scope="col">소통 Email</th>
            <th scope="col">접수 시간</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={7}>
                <div className="empty">
                  <div className="empty__icon"><Icon name="messages" size={24} /></div>
                  <div className="empty__text">{emptyText}</div>
                </div>
              </td>
            </tr>
          ) : (
            rows.map((row) => {
              const [tone, label] = priority(now, row.waiting_since);
              return (
                <tr key={row.id} className="is-clickable">
                  <td>
                    <span className={`pill pill--${STATUS_TONE[row.status] ?? "neutral"} pill--sm`}>
                      <span className="pill__dot" />
                      {STATUS_LABELS[row.status] ?? row.status}
                    </span>
                  </td>
                  <td className="td-muted">{stageLabels[row.stage] ?? row.stage}</td>
                  <td className="truncate" style={{ maxWidth: 300 }}>
                    <Link to={`/messages/${row.id}`}>{row.subject}</Link>
                  </td>
                  <td className="td-center">
                    <span className={`wait-dot wait-dot--${tone}`} title={label} />
                    <span className="sr-only">{label}</span>
                  </td>
                  <td className="td-muted">{row.channel}</td>
                  <td className="td-subtle truncate mono" style={{ maxWidth: 190 }}>{row.email}</td>
                  <td className="td-subtle tnum">{kst(row.received_at, "md-hm")}</td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
