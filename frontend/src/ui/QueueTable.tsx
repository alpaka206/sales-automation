import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { Icon } from "./Icon";
import { kst } from "../lib/format";
import { DataTable, type Column } from "./DataTable";
import { ReplyDoneDialog, type QueueReply } from "./ReplyDoneDialog";

// The awaiting-reply table, defined ONCE — the port of partials/queue_table.html, for
// the same reason that file exists: the dashboard and 검토 및 회신 render the same rows
// and drifted apart when each owned a copy.
export type QueueRow = {
  /** 그 행의 초안(메시지) — 「답변 대기」에서 초안이 아직 없는 대화는 null 이다. */
  id: number | null;
  conversation_id: number;
  /** 행이 여는 화면. 서버가 정한다 — 초안이 있으면 `/messages/<id>`, 없으면 티켓 `/tickets/<대화>`. */
  href: string;
  status: string;
  stage: string;
  subject: string;
  // 채널 자리에 있던 값입니다. 전부 "email" 이라 한 줄도 구분하지 못했습니다.
  category: string | null;
  email: string;
  received_at: string;
  waiting_since: string | null;
  /** 우리 마지막 영업 메일 뒤 고객 회신 수와 그중 아직 안 뺀 수. New 와 「발송 완료」 행에는 없거나 0 이다. */
  reply_total?: number;
  reply_open?: number;
  /** [완료] 창에 그릴 남은 회신. 비어 있으면 그 행에는 버튼이 없다. */
  replies?: QueueReply[];
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
// 「고객 회신 도착」과 우리 초안(「발송 대기」 · 「작성 중」)은 색이 다르다 (2026-11-02 운영자: 「지금 같아서
// 헷갈려」 · 「고객 회신 도착은 주황색, 작성중은 발송대기랑 같은색」). 실패 둘은 뜻이 같아서 같은 빨강이고 글자가 가른다.
const STATUS_TONE: Record<string, string> = {
  // 「답변 대기」의 초안 없는 대화 — 고객이 답장했는데 우리 회신이 아직 없다.
  received: "orange",
  pending_approval: "warn",
  drafting: "warn",
  draft_failed: "danger",
  send_failed: "danger",
  sent: "ok",
  test_sent: "info",
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
  doneColumn = false,
}: {
  rows: QueueRow[];
  now: string;
  stageLabels: Record<string, string>;
  categoryLabels?: Record<string, string>;
  unqualified?: string[];
  emptyText: string;
  /** 맨 오른쪽 [완료] 열 — 「답변 대기」 목록에만 있다. 「발송 완료」는 나간 회신이라 뺄 것이 없다. */
  doneColumn?: boolean;
}) {
  const [doneRow, setDoneRow] = useState<QueueRow | null>(null);
  const notALead = new Set(unqualified);
  // 연 화면의 「뒤로」가 온 자리(대시보드, 또는 필터까지 그대로인 목록)로 돌아가게 — 초안 없는 행은 티켓 화면을 엽니다.
  const location = useLocation();
  const from = location.pathname + location.search;
  // 맨 오른쪽. 행 전체가 아니라 제목만 링크라 눌러도 티켓이 안 열린다.
  const doneCells: Column<QueueRow>[] = doneColumn
    ? [{
        width: "7%",
        className: "td-action",
        cell: (row) =>
          row.replies?.length ? (
            <button type="button" className="btn btn--subtle btn--sm" aria-haspopup="dialog"
                    onClick={() => setDoneRow(row)}>
              완료
            </button>
          ) : null,
      }]
    : [];
  return (
    <>
    <DataTable
      columns={[
        {
          label: "상태",
          width: "16%",
          cell: (row) => {
            // 회신이 둘 이상일 때만 센다 — 한 건이면 숫자가 할 말이 없다.
            const counted = (row.reply_total ?? 0) > 1;
            return (
              <span className={`pill pill--${STATUS_TONE[row.status] ?? "neutral"} pill--sm`}
                    title={counted ? `회신 ${row.reply_total}건 중 ${row.reply_open}건 남음` : undefined}>
                <span className="pill__dot" />
                {STATUS_LABELS[row.status] ?? row.status}
                {counted && <span className="tnum">{` ${row.reply_open}/${row.reply_total}`}</span>}
              </span>
            );
          },
        },
        { label: "Stage", width: "10%", className: "td-muted",
          cell: (row) => stageLabels[row.stage] ?? row.stage },
        {
          label: "문의 제목",
          width: "22%",
          // 링크지만 **글자처럼 보입니다**(link--plain). 이 표는 모든 행이 링크라, 밑줄이
          // 강조가 아니라 배경 무늬가 됩니다 — 제목이 길수록 파란 줄이 화면을 가로지릅니다.
          // 여는 방법은 표의 행 전체가 이미 알려 줍니다.
          cell: (row) => (
            <Link to={row.href} state={{ from }} className="truncate link--plain"
                  style={{ display: "block" }}>
              {row.subject}
            </Link>
          ),
        },
        {
          // One dot wide, centred under its own heading.
          label: "우선순위",
          width: "7%",
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
          width: "10%",
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
        { label: "소통 Email", width: "17%", className: "td-subtle truncate mono",
          cell: (row) => row.email },
        { label: "접수 시간", width: "11%", className: "td-subtle tnum",
          cell: (row) => kst(row.received_at, "md-hm") },
        ...doneCells,
      ]}
      rows={rows}
      rowKey={(row) => row.href}
      empty={emptyText}
      emptyIcon={<Icon name="messages" size={24} />}
    />
    {doneRow && (
      <ReplyDoneDialog conversationId={doneRow.conversation_id} replies={doneRow.replies ?? []}
                       onClose={() => setDoneRow(null)} />
    )}
    </>
  );
}
