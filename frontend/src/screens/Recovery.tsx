import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useState } from "react";
import { getJSON, postForm } from "../lib/api";
import { kst } from "../lib/format";
import { Modal } from "../ui/Modal";
import { Loading } from "../ui/Loading";
import { DataTable } from "../ui/DataTable";

// 복구 — the tab with work on it. Read-only lists plus the retry/resolve actions, which
// post to the routes they always did so the retry logic stays server-side.
type Msg = {
  id: number; status: string; subject: string | null; to_address: string | null;
  created_at: string; error: string | null; company: string | null;
};
type Data = {
  pending: number;
  inbound_jobs: { id: number; ticket_id: string | null; status: string; attempts: number; last_error: string | null; updated_at: string }[];
  messages: Msg[]; stale_drafts: Msg[]; sync_failures: Msg[];
};

export function Recovery() {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<Action | null>(null);
  const { data, isPending, error } = useQuery({
    queryKey: ["recovery"],
    queryFn: () => getJSON<Data>("/api/ui/recovery"),
    retry: false,
    refetchInterval: 30_000,
  });

  if (error) {
    return <div className="card"><div className="empty"><div className="empty__text">관리자만 접근할 수 있습니다.</div></div></div>;
  }
  if (isPending || !data) return <Loading columns={3} />;

  type Action = {
    label: string; path: string;
    body?: Record<string, string>; danger?: boolean; confirm?: string;
  };

  async function act(action: Action) {
    if (action.confirm) setPending(action);
    else await run(action);
  }

  async function run(action: Action) {
    setPending(null);
    await postForm(action.path, action.body ?? {});
    await queryClient.invalidateQueries({ queryKey: ["recovery"] });
  }

  const section = (title: string, hint: string, rows: Msg[], actions?: (row: Msg) => Action[]) => (
    <section className="card mb-gap">
      <div className="section-header" style={{ marginBottom: 10 }}>
        <div className="section-header__l">
          <div>
            <div className="section-header__title">{title}</div>
            <div className="section-header__sub">{hint}</div>
          </div>
        </div>
        <span className="tag tnum">{rows.length}</span>
      </div>
      <DataTable
        columns={[
          {
            cell: (row) => (
              <>
                <Link to={`/messages/${row.id}`}><strong className="t-sm">{row.subject || `메시지 #${row.id}`}</strong></Link>
                <div className="t-xs t-subtle">{row.company || row.to_address || "-"}</div>
                {row.error && <div className="t-xs" style={{ color: "var(--danger)" }}>{row.error}</div>}
              </>
            ),
          },
          { width: "150px", className: "tnum td-subtle", cell: (row) => kst(row.created_at) },
          ...(actions ? [{
            width: "190px",
            cell: (row: Msg) => (
              <div className="row" style={{ gap: 6 }}>
                {actions(row).map((entry) => (
                  <button key={entry.label} type="button"
                          className={`btn btn--sm ${entry.danger ? "btn--danger" : "btn--subtle"}`}
                          onClick={() => void act(entry)}>
                    {entry.label}
                  </button>
                ))}
              </div>
            ),
          }] : []),
        ]}
        rows={rows}
        rowKey={(row) => row.id}
        empty="처리할 항목이 없습니다."
      />
    </section>
  );

  return (
    <>
      {/* delivery_unknown is not a retry: the mail may have gone out. The operator says
          which, and only the "not sent" branch queues it again — hence the confirm. */}
      {section("발송·작성 실패", "재시도하면 같은 워커 경로로 다시 처리합니다.", data.messages,
               (row) =>
                 row.status === "delivery_unknown"
                   ? [
                       { label: "발송됨 확인", path: `/operations/recovery/messages/${row.id}/resolve`,
                         body: { action: "confirmed_sent" } },
                       { label: "미발송 확인 후 재시도", path: `/operations/recovery/messages/${row.id}/resolve`,
                         body: { action: "confirmed_not_sent" }, danger: true,
                         confirm: "실제로 발송되지 않은 것을 확인했나요? 다시 발송 대기로 돌아갑니다." },
                     ]
                   : [{ label: "재시도", path: `/operations/recovery/messages/${row.id}/retry` }])}
      {section("HubSpot·시트 동기화 실패", "메일은 나갔고 기록만 실패한 건입니다.", data.sync_failures,
               (row) => [{ label: "동기화 재시도", path: `/operations/recovery/messages/${row.id}/sync` }])}
      {section("작성 중 멈춘 초안", "30분 넘게 drafting 상태입니다. 참고용.", data.stale_drafts)}

      <section className="card">
        <div className="section-header" style={{ marginBottom: 10 }}>
          <div className="section-header__l">
            <div>
              <div className="section-header__title">인바운드 처리 실패</div>
              <div className="section-header__sub">재시도를 소진해 dead 로 남은 작업</div>
            </div>
          </div>
          <span className="tag tnum">{data.inbound_jobs.length}</span>
        </div>
        <DataTable
          columns={[
            {
              cell: (job) => (
                <>
                  <strong className="t-sm">티켓 #{job.ticket_id || job.id}</strong>
                  <div className="t-xs t-subtle">시도 {job.attempts}회</div>
                  {job.last_error && <div className="t-xs" style={{ color: "var(--danger)" }}>{job.last_error}</div>}
                </>
              ),
            },
            { width: "150px", className: "tnum td-subtle", cell: (job) => kst(job.updated_at) },
            {
              width: "150px",
              cell: (job) => (
                <button type="button" className="btn btn--subtle btn--sm"
                        onClick={() => void act({ label: "처음부터 재처리", path: `/operations/recovery/inbound/${job.id}/retry` })}>
                  처음부터 재처리
                </button>
              ),
            },
          ]}
          rows={data.inbound_jobs}
          rowKey={(job) => job.id}
          empty="처리할 항목이 없습니다."
        />
      </section>

      {/* Confirming "not sent" queues a real send. It gets a dialog, not a browser
          confirm() — the old page used confirm(), which blocks the whole tab. */}
      {pending && (
        <Modal title={pending.label} description={pending.confirm}
               onClose={() => setPending(null)}
               actions={
                 <button type="button" className="btn btn--danger" onClick={() => void run(pending)}>
                   {pending.label}
                 </button>
               } />
      )}
    </>
  );
}
