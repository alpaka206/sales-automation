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
  status: string; body: string | null; chars: number; edited_at: string | null;
  last_synced_at: string | null; last_error: string | null; from_file: boolean;
};
type Data = { modes: { key: string; label: string }[]; rows: Row[] };

/** 제목과 본문을 form-encoded 로 보냅니다 — 업로드·토글·삭제와 같은 라우트 계열입니다. */
async function saveDoc(path: string, method: string, fields: Record<string, string>) {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(fields),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new Error(detail?.detail ?? `${response.status}`);
  }
}

const columns = (act: (path: string) => void): Column<Row>[] => [
  {
    label: "문서",
    width: "70%",
    cell: (row) => (
      <>
        <strong>{row.title || row.label}</strong>
        {row.from_file && <div className="t-xs t-subtle">직접 넣은 문서 (노션 미연결)</div>}
        {row.edited_at && !row.from_file && (
          <div className="t-xs t-subtle">콘솔에서 수정함 — 같은 zip 을 다시 올리면 되돌아갑니다</div>
        )}
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

/** 붙여넣어 문서 하나 만들기. zip 을 만들 일이 아닐 때의 경로입니다. */
function NewDoc({ modes, onDone }: { modes: { key: string; label: string }[]; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState("");
  const [body, setBody] = useState("");
  const [mode, setMode] = useState("knowledge");
  const [note, setNote] = useState<string | null>(null);

  if (!open) {
    return (
      <button type="button" className="btn btn--subtle btn--sm" onClick={() => setOpen(true)}>
        <Icon name="plus" size={14} /> 직접 추가
      </button>
    );
  }

  async function save() {
    setNote("저장 중…");
    try {
      await saveDoc("/policy-docs", "POST", { label, body, mode });
      setOpen(false);
      setLabel("");
      setBody("");
      setNote(null);
      onDone();
    } catch (error) {
      setNote(`실패: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  return (
    <div className="card mb-gap" style={{ maxWidth: 860 }}>
      <label className="field-label" htmlFor="pd-label">문서 이름</label>
      <input className="input" id="pd-label" value={label} onChange={(e) => setLabel(e.target.value)}
             placeholder="예: CS 문의 대응 가이드" style={{ marginBottom: 12 }} />

      <label className="field-label" htmlFor="pd-mode">쓰임</label>
      <select className="select" id="pd-mode" value={mode} onChange={(e) => setMode(e.target.value)}
              style={{ marginBottom: 12 }}>
        {modes.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
      </select>

      <label className="field-label" htmlFor="pd-body">본문</label>
      <textarea className="draft-textarea" id="pd-body" value={body}
                onChange={(e) => setBody(e.target.value)} style={{ minHeight: 260 }}
                placeholder="노션에서 복사해 그대로 붙여넣으세요. 표와 목록은 그대로 읽힙니다." />

      <div className="action-bar">
        <button type="button" className="btn btn--primary" onClick={() => void save()}>
          <Icon name="check" size={15} /> 만들기
        </button>
        <button type="button" className="btn btn--subtle" onClick={() => setOpen(false)}>취소</button>
      </div>
      {note && <div className="t-sm" style={{ marginTop: 12 }} role="status">{note}</div>}
    </div>
  );
}

/** 이미 있는 문서의 본문 고치기. */
function EditDoc({ doc, onDone }: { doc: Row; onDone: () => void }) {
  const [body, setBody] = useState(doc.body || "");
  const [note, setNote] = useState<string | null>(null);

  async function save() {
    setNote("저장 중…");
    try {
      await saveDoc(`/policy-docs/${doc.id}`, "PUT", { body });
      setNote(null);
      onDone();
    } catch (error) {
      setNote(`실패: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  return (
    <>
      {/* 노션에서 온 문서라면 다음 업로드가 이 편집을 되돌립니다. 막지 않고 말합니다 —
          문제는 덮어쓰는 것이 아니라 조용히 덮어쓰는 것입니다. */}
      {!doc.from_file && (
        <div className="banner banner--warn mb-gap">
          <span className="banner__icon"><Icon name="warn" size={18} /></span>
          <div>
            <div className="banner__title">이 문서의 원본은 노션입니다</div>
            <div className="banner__body">
              여기서 고쳐도 됩니다. 다만 이 문서가 담긴 Export zip 을 다시 올리면 파일 내용으로
              돌아갑니다. 계속 남길 내용이면 노션에서 고치는 편이 안전합니다.
            </div>
          </div>
        </div>
      )}
      <div className="card">
        <textarea className="draft-textarea mono" value={body}
                  onChange={(e) => setBody(e.target.value)}
                  style={{ minHeight: 420, fontSize: 12.5, lineHeight: 1.7 }} />
        <div className="action-bar">
          <button type="button" className="btn btn--primary" onClick={() => void save()}>
            <Icon name="check" size={15} /> 저장
          </button>
          <button type="button" className="btn btn--subtle" onClick={onDone}>취소</button>
        </div>
        {note && <div className="t-sm" style={{ marginTop: 12 }} role="status">{note}</div>}
      </div>
    </>
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
  const [editing, setEditing] = useState(false);
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
              {doc.edited_at
                ? `콘솔에서 수정 ${kst(doc.edited_at)}`
                : doc.last_synced_at
                  ? `마지막 동기화 ${kst(doc.last_synced_at)}`
                  : "아직 동기화하지 않았습니다"}
              {" · "}{doc.chars.toLocaleString()}자
            </p>
          </div>
          <div className="row" style={{ gap: 8 }}>
            {doc.notion_url && (
              <a className="btn btn--subtle" href={doc.notion_url} target="_blank" rel="noopener noreferrer">
                노션에서 열기
              </a>
            )}
            {!editing && (
              <button type="button" className="btn btn--subtle" onClick={() => setEditing(true)}>
                <Icon name="edit" size={15} /> 수정
              </button>
            )}
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

        {editing ? (
          <EditDoc doc={doc} onDone={() => { setEditing(false); refresh(); }} />
        ) : (
          <div className="card">
            <pre className="msg-body--inset mono"
                 style={{ fontSize: 12.5, whiteSpace: "pre-wrap", lineHeight: 1.7, overflow: "auto" }}>
              {doc.body || "아직 내용을 가져오지 않았습니다."}
            </pre>
          </div>
        )}
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
        {/* zip 을 만들 일이 아닐 때 — 한 문서를 붙여넣어 만듭니다. */}
        <NewDoc modes={data.modes} onDone={refresh} />
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
