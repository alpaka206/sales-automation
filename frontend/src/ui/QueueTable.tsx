import { Link } from "react-router-dom";
import { Icon } from "./Icon";
import { kst } from "../lib/format";
import { DataTable } from "./DataTable";

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
  review_note?: string | null;
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
    <DataTable
      columns={[
        {
          label: "상태",
          width: "11%",
          cell: (row) => (
            <span className={`pill pill--${STATUS_TONE[row.status] ?? "neutral"} pill--sm`}>
              <span className="pill__dot" />
              {STATUS_LABELS[row.status] ?? row.status}
            </span>
          ),
        },
        { label: "Stage", width: "11%", className: "td-muted",
          cell: (row) => stageLabels[row.stage] ?? row.stage },
        {
          label: "문의 제목",
          width: "30%",
          cell: (row) => (
            <>
              <Link to={`/messages/${row.id}`} className="truncate" style={{ display: "block" }}>
                {row.subject}
              </Link>
              {/* Not a second approval gate — every one of these waits for a human
                  anyway. It says which one to open first. */}
              {row.review_note && (
                <span className="pill pill--warn pill--sm" title={row.review_note}>
                  <span className="pill__dot" />검토 필요
                </span>
              )}
            </>
          ),
        },
        {
          // One dot wide, centred under its own heading.
          label: "우선순위",
          width: "9%",
          headClassName: "th-center",
          className: "td-center",
          cell: (row) => {
            const [tone, label] = priority(now, row.waiting_since);
            return (
              <>
                <span className={`wait-dot wait-dot--${tone}`} title={label} />
                <span className="sr-only">{label}</span>
              </>
            );
          },
        },
        { label: "채널", width: "8%", className: "td-muted", cell: (row) => row.channel },
        { label: "소통 Email", width: "19%", className: "td-subtle truncate mono",
          cell: (row) => row.email },
        { label: "접수 시간", width: "12%", className: "td-subtle tnum",
          cell: (row) => kst(row.received_at, "md-hm") },
      ]}
      rows={rows}
      rowKey={(row) => row.id}
      empty={emptyText}
      emptyIcon={<Icon name="messages" size={24} />}
    />
  );
}
