import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { getJSON, postForm } from "../lib/api";
import { Icon } from "../ui/Icon";
import { DataTable, type Column } from "../ui/DataTable";
import { kst } from "../lib/format";
import { Loading } from "../ui/Loading";

type Row = {
  id: number; label: string; title: string | null; notion_url: string; mode: string;
  status: string; body: string | null; chars: number;
  last_synced_at: string | null; last_error: string | null; from_file: boolean;
};
type Data = { modes: { key: string; label: string }[]; rows: Row[] };

const columns = (act: (path: string) => void): Column<Row>[] => [
  {
    label: "문서",
    width: "70%",
    cell: (row) => (
      <>
        <strong>{row.title || row.label}</strong>
        {row.from_file && <div className="t-xs t-subtle">파일에서 가져온 문서 (노션 미연결)</div>}
        {row.last_error && (
          <div className="t-xs" style={{ color: "var(--danger)" }}>
            동기화 실패 — 이전 사본을 사용 중입니다
          </div>
        )}
        {row.status !== "active" && (
          <div className="t-xs t-subtle">중지됨 — 답변에 사용되지 않습니다</div>
        )}
      </>
    ),
  },
  { label: "마지막 동기화", width: "18%", className: "tnum td-subtle",
    cell: (row) => (row.last_synced_at ? kst(row.last_synced_at) : "—") },
  {
    width: "12%",
    // Pausing keeps the registration and the synced copy; only 삭제 forgets the link.
    cell: (row) => (
      <div className="row" style={{ gap: 6 }} onClick={(event) => event.stopPropagation()}>
        <button type="button" className="btn btn--subtle btn--sm"
                onClick={() => act(`/policy-docs/${row.id}/toggle`)}>
          {row.status === "active" ? "중지" : "사용"}
        </button>
        <button type="button" className="btn btn--ghost btn--sm"
                onClick={() => act(`/policy-docs/${row.id}/delete`)}>삭제</button>
      </div>
    ),
  },
];


/** 노션 Export zip 을 올려 등록된 문서를 한 번에 갱신합니다.
 *
 *  왜 업로드인가: 로컬 실행 스크립트는 노션에서 읽어 DB에 쓰는데, 사내망이 DB 포트를 막고
 *  있어 담당자 PC는 DB에 닿지 못합니다. 노션은 브라우저로만 되고 DB는 서버만 되니 양쪽을
 *  다 할 수 있는 기계가 없습니다. 파일로 옮기면 각 구간이 실제로 뚫려 있는 경로만 씁니다. */
function UploadExport({ onDone }: { onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  async function upload(file: File) {
    setBusy(true);
    setNote(null);
    try {
      const body = new FormData();
      body.append("export", file);
      const response = await fetch("/policy-docs/upload-export", {
        method: "POST", credentials: "same-origin", body,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail ?? response.status);
      const added = result.added
        ? ` · 새로 추가 ${result.added} (${(result.added_labels ?? []).join(", ")})`
        : "";
      setNote(`갱신 ${result.synced}${added}${result.failed ? ` · 실패 ${result.failed}` : ""}`);
      onDone();
    } catch (error) {
      setNote(`실패: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className={`dropzone${over ? " is-over" : ""}${busy ? " is-busy" : ""}`}
      onDragOver={(event) => { event.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => {
        event.preventDefault();
        setOver(false);
        const file = event.dataTransfer.files?.[0];
        if (file && !busy) void upload(file);
      }}
    >
      <label style={{ cursor: busy ? "default" : "pointer", display: "block" }}>
        <div className="row" style={{ gap: 8, justifyContent: "center" }}>
          {busy ? <span className="spinner" /> : <Icon name="file" size={16} />}
          <strong className="t-sm">
            {busy ? "읽는 중…" : "노션 Export zip 을 여기에 끌어다 놓으세요"}
          </strong>
        </div>
        <div className="t-xs t-subtle" style={{ marginTop: 4 }}>
          클릭해서 고를 수도 있습니다 · zip 안의 문서는 <strong>전부</strong> 반영되고,
          처음 보는 문서는 <strong>문의별 참고로 자동 추가</strong>됩니다
        </div>
        <input type="file" accept=".zip" hidden disabled={busy}
               onChange={(event) => {
                 const file = event.target.files?.[0];
                 event.target.value = "";
                 if (file) void upload(file);
               }} />
      </label>
      {note && <div className="t-xs t-subtle" style={{ marginTop: 8 }}>{note}</div>}
    </div>
  );
}

export function PolicyDocs({ onBack }: { onBack?: () => void }) {
  const [params, setParams] = useSearchParams();
  const queryClient = useQueryClient();
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["policy-docs"] });
  const act = async (path: string) => {
    await postForm(path, {});
    refresh();
  };
  const open = params.get("doc");
  const { data, isPending } = useQuery({
    queryKey: ["policy-docs"],
    queryFn: () => getJSON<Data>("/api/ui/policy-docs"),
  });

  if (isPending || !data) return <Loading columns={2} />;

  const doc = open ? data.rows.find((row) => String(row.id) === open) : null;
  if (doc) {
    return (
      <>
        <div style={{ marginBottom: 14 }}>
          <button type="button" className="chip"
                  onClick={() => setParams({ kind: "policy" }, { replace: true })}>
            <Icon name="chevron" size={14} /> 정책 문서
          </button>
        </div>
        <div className="page-header">
          <div>
            <h1 className="page-title">{doc.title || doc.label}</h1>
            <p className="page-sub">
              {doc.last_synced_at ? `마지막 동기화 ${kst(doc.last_synced_at)}` : "아직 동기화하지 않았습니다"}
              {" · "}{doc.chars.toLocaleString()}자
            </p>
          </div>
          {doc.notion_url && (
            <a className="btn btn--subtle" href={doc.notion_url} target="_blank" rel="noopener noreferrer">
              노션에서 열기
            </a>
          )}
        </div>

        {/* Read-only, and the banner says why: editing here would create a second copy
            that the next sync silently overwrites. */}
        <div className="banner mb-gap">
          <span className="banner__icon"><Icon name="shield" size={18} /></span>
          <div>
            <div className="banner__title">읽기 전용</div>
            <div className="banner__body">
              정책은 노션에서 수정하고 로컬 동기화로 가져옵니다. 여기서는 실제로 무엇이 들어와 있는지 확인만 합니다.
            </div>
          </div>
        </div>

        {doc.last_error && (
          <div className="banner banner--warn mb-gap">
            <div>
              <div className="banner__title">마지막 동기화 실패 — 이전 사본을 사용 중입니다</div>
              <div className="banner__body">{doc.last_error}</div>
            </div>
          </div>
        )}

        <div className="card">
          <pre className="msg-body--inset mono"
               style={{ fontSize: 12.5, whiteSpace: "pre-wrap", lineHeight: 1.7, overflow: "auto" }}>
            {doc.body || "아직 내용을 가져오지 않았습니다."}
          </pre>
        </div>
      </>
    );
  }

  return (
    <>
      <div style={{ marginBottom: 14 }}>
        <button type="button" className="chip"
                onClick={() => (onBack ? onBack() : setParams({}, { replace: true }))}>
          <Icon name="chevron" size={14} /> 이메일 템플릿
        </button>
      </div>
      <div className="page-header">
        <div><h1 className="page-title">정책 문서</h1></div>
      </div>

      {/* 드롭이 주된 방법입니다. URL 을 하나씩 등록하게 하면 노션에서 문서를 만든 사람과
          콘솔에 등록하는 사람이 같아야 하고, 한쪽만 하면 조용히 누락됩니다. */}
      <UploadExport onDone={refresh} />
      {data.modes.map((mode) => (
        <section key={mode.key} className="mb-gap">
          <div className="section-header table-heading">
            <div className="section-header__l">
              <span className="section-header__icon"><Icon name="file" size={16} /></span>
              <div className="section-header__title">{mode.label}</div>
            </div>
          </div>
          <div className="card card--flush">
            {/* The SAME columns object for both groups. Two tables measuring their own
                widths put the same column in two different places. */}
            <DataTable
              columns={columns(act)}
              rows={data.rows.filter((row) => row.mode === mode.key)}
              rowKey={(row) => row.id}
              empty="등록된 문서가 없습니다."
              // Pushed: opening a document must leave the list in history so the
              // browser's back button returns to it instead of leaving the screen.
              onRowClick={(row) => setParams({ kind: "policy", doc: String(row.id) })}
            />
          </div>
        </section>
      ))}
    </>
  );
}
