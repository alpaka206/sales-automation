import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useState } from "react";
import { getJSON, postForm } from "../lib/api";
import { kst } from "../lib/format";
import { Modal } from "../ui/Modal";
import { DataTable } from "../ui/DataTable";
import { ActionButton } from "../ui/ActionButton";
import { Loading } from "../ui/Loading";

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
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const { data, isPending, error } = useQuery({
    queryKey: ["recovery"],
    queryFn: () => getJSON<Data>("/api/ui/recovery"),
    retry: false,
    refetchInterval: 60_000,
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

  // 확인 창은 **끝난 뒤에** 닫습니다. 먼저 닫으면 "처리 중" 을 볼 자리가 사라지고, 실패해도
  // 목록만 그대로인 채 아무 말이 없습니다 — 아무것도 안 누른 것과 구분이 안 됩니다.
  // 여기 오는 것들은 재발송·티켓 삭제라 그 구분이 중요합니다.
  async function run(action: Action) {
    if (action.path.endsWith("/clear-failures")) {
      await sync(action.path, {}, (r) => `발송 실패 ${r.cleared}건 정리됨`);
      setPending(null);
      return;
    }
    if (action.path.endsWith("/hubspot-sync")) {
      await sync(action.path, action.body ?? {}, (r) =>
        `삭제 ${r.deleted}건 · 초안 정리 ${r.retired}건`);
      setPending(null);
      return;
    }
    try {
      await postForm(action.path, action.body ?? {});
      setNote(null);
      setPending(null);
    } catch (error) {
      // 창은 열어 둡니다. 왜 실패했는지 읽고 다시 누를 수 있어야 합니다.
      setNote(`실패: ${error instanceof Error ? error.message : String(error)}`);
    }
    await queryClient.invalidateQueries({ queryKey: ["recovery"] });
  }

  /** HubSpot 최신화 and the failure sweep. Deliberately small: the result is one line,
   *  because the answer is usually "nothing to do" and that should not need a dialog. */
  async function sync(path: string, body: Record<string, string>, describe: (r: any) => string) {
    setBusy(path);
    setNote(null);
    try {
      const response = await postForm(path, body);
      setNote(describe(await response.json()));
      await queryClient.invalidateQueries();
    } catch (error) {
      setNote(`실패: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusy(null);
    }
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
                  <ActionButton key={entry.label} pending="처리 중"
                                className={`btn btn--sm ${entry.danger ? "btn--danger" : "btn--subtle"}`}
                                onClick={() => act(entry)}>
                    {entry.label}
                  </ActionButton>
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
      <div className="filter-bar mb-gap" style={{ justifyContent: "flex-start", gap: 8 }}>
        {/* Two passes. The first only counts: a 404 from HubSpot is how a deleted ticket
            looks, but it is also how a ticket id from another portal or a backfilled row
            looks, and retiring a draft is not undoable. Stage moves apply on the first
            pass — those are just agreeing with what HubSpot already shows. */}
        <button type="button" className="btn btn--subtle btn--sm" disabled={busy !== null}
                onClick={() => void sync("/operations/recovery/hubspot-sync", {}, (r) => {
                  // 아무것도 못 돈 경우(설정 없음)만 이유 한 줄로 끝냅니다. 삭제 검사만
                  // 건너뛴 경우는 나머지 결과를 그대로 보여주고 이유를 뒤에 붙입니다 —
                  // 못 본 것을 「정리할 항목 없음」으로 읽으면 안 됩니다.
                  if (r.error && !r.checked && !r.swept) return r.error;
                  const moved = r.moved + r.swept;
                  const summary = `확인 ${r.checked}건 · 단계 정정 ${moved}`
                    + (r.error ? ` · ⚠ ${r.error}` : "");
                  if (r.deleted > 0 || r.stale > 0) {
                    setPending({
                      label: "정리",
                      path: "/operations/recovery/hubspot-sync",
                      body: { apply: "true" },
                      danger: true,
                      confirm: [
                        r.deleted > 0 &&
                          `HubSpot에 없는 티켓 ${r.deleted}건 — 해당 문의와 메일 기록을 완전히 삭제합니다. 되돌릴 수 없습니다.`,
                        r.stale > 0 &&
                          `New가 아닌 문의 ${r.stale}건 — 대기 중인 회신 초안을 종료합니다. 문의와 기록은 남습니다.`,
                        "고객 정보와 계약, 직접 남긴 소통 히스토리는 어느 쪽에서도 지워지지 않습니다.",
                      ].filter(Boolean).join("\n\n"),
                    });
                    return `${summary} · 삭제 대상 ${r.deleted} · 초안 정리 대상 ${r.stale}`;
                  }
                  return `${summary} · 정리할 항목 없음`;
                })}>
          {busy === "/operations/recovery/hubspot-sync"
            ? <><span className="spinner" role="status" /> 확인 중</>
            : "HubSpot 최신화"}
        </button>
        <button type="button" className="btn btn--subtle btn--sm" disabled={busy !== null}
                onClick={() => setPending({
                  label: "발송 실패 내역 정리",
                  path: "/operations/recovery/clear-failures",
                  danger: true,
                  confirm: "발송·작성 실패 목록을 모두 정리합니다. 각 건은 '거절' 상태가 되어 목록에서 사라지고, 대화 기록은 그대로 남습니다.",
                })}>
          발송 실패 정리
        </button>
        {/* A plain link, not a fetch: the browser's own download is the whole feature,
            and routing it through JS would only mean rebuilding Save As. */}
        <a className="btn btn--subtle btn--sm" href="/operations/export/inquiries">
          문의 원문 내려받기
        </a>
        {note && <span className="t-xs t-subtle">{note}</span>}
      </div>

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
                <ActionButton className="btn btn--subtle btn--sm" pending="재처리 중"
                              onClick={() => act({ label: "처음부터 재처리", path: `/operations/recovery/inbound/${job.id}/retry` })}>
                  처음부터 재처리
                </ActionButton>
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
        <Modal title={pending.label}
               description={
                 <>
                   {pending.confirm}
                   {note?.startsWith("실패") && (
                     <div className="t-xs" style={{ marginTop: 8, color: "var(--danger)" }} role="status">
                       {note}
                     </div>
                   )}
                 </>
               }
               onClose={() => setPending(null)}
               actions={
                 <ActionButton className="btn btn--danger" pending="처리 중"
                               onClick={() => run(pending)}>
                   {pending.label}
                 </ActionButton>
               } />
      )}
    </>
  );
}
