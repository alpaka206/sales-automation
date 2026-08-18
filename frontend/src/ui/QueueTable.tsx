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
  // 채널 자리에 있던 값입니다. 전부 "email" 이라 한 줄도 구분하지 못했습니다.
  category: string | null;
  email: string;
  received_at: string;
  waiting_since: string | null;
};

/** 상태 → 사람이 읽는 말. 티켓 세부 내역의 머리글도 이걸 씁니다 — 같은 상태를 두 화면이
 *  다른 말로 부르면 그게 다른 상태로 읽힙니다. */
export const STATUS_LABELS: Record<string, string> = {
  // 고객이 보낸 메일. 티켓 세부 내역의 머리글은 방향을 안 가리고 가장 최근 메시지를
  // 집으므로 이 값이 옵니다 — 없으면 영어 `received` 가 한국어 줄에 그대로 섭니다.
  received: "고객 회신 도착",
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
  categoryLabels = {},
  unqualified = [],
  emptyText,
}: {
  rows: QueueRow[];
  now: string;
  stageLabels: Record<string, string>;
  categoryLabels?: Record<string, string>;
  unqualified?: string[];
  emptyText: string;
}) {
  const notALead = new Set(unqualified);
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
          // 링크지만 **글자처럼 보입니다**(link--plain). 이 표는 모든 행이 링크라, 밑줄이
          // 강조가 아니라 배경 무늬가 됩니다 — 제목이 길수록 파란 줄이 화면을 가로지릅니다.
          // 여는 방법은 표의 행 전체가 이미 알려 줍니다.
          cell: (row) => (
            <Link to={`/messages/${row.id}`} className="truncate link--plain"
                  style={{ display: "block" }}>
              {row.subject}
            </Link>
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
        {
          // 채널이 있던 자리. 채널은 전부 "email" 이라 한 줄도 구분하지 못했고, 유형은
          // 무엇을 먼저 열지를 말해 줍니다. UnQualified 는 세일즈 리드가 아니라는 뜻이지
          // 회신을 안 한다는 뜻이 아닙니다 — CS 가이드/소개 문서로 나갑니다.
          label: "문의 유형",
          width: "13%",
          className: "td-muted",
          cell: (row) =>
            row.category && notALead.has(row.category) ? (
              <span className="pill pill--neutral pill--sm" title={categoryLabels[row.category]}>
                <span className="pill__dot" />UnQualified
              </span>
            ) : (
              (row.category && categoryLabels[row.category]) || row.category || "—"
            ),
        },
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
