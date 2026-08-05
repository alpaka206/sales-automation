import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { getJSON, postForm } from "../lib/api";
import { Icon } from "../ui/Icon";
import { DataTable, type Column } from "../ui/DataTable";
import { kst } from "../lib/format";
import { Loading } from "../ui/Loading";

type Row = {
  id: number; label: string; title: string | null; mode: string;
  status: string; body: string | null; chars: number; edited_at: string | null;
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
        {row.status !== "active" && (
          <div className="t-xs t-subtle">중지됨 — 답변에 사용되지 않습니다</div>
        )}
      </>
    ),
  },
  // 동기화가 아니라 수정입니다 — 위에서 받아 오는 것이 없으므로 이 문서가 마지막으로
  // 손을 탄 시각이 유일하게 말이 되는 날짜입니다.
  { label: "수정", width: "18%", className: "tnum td-subtle",
    cell: (row) => kst(row.edited_at || "") || "—" },
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
              {doc.edited_at ? `수정 ${kst(doc.edited_at)}` : "본문 없음"}
              {" · "}{doc.chars.toLocaleString()}자
            </p>
          </div>
          <div className="row" style={{ gap: 8 }}>
            {!editing && (
              <button type="button" className="btn btn--subtle" onClick={() => setEditing(true)}>
                <Icon name="edit" size={15} /> 수정
              </button>
            )}
          </div>
        </div>

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
        {/* 문서가 들어오는 유일한 길입니다. 노션에서 받아 오는 경로는 전부 없앴습니다 —
            토큰을 못 만들고, 쿠키는 403 이고, Export zip 은 부모 한 장만 실어 옵니다. */}
        <NewDoc modes={data.modes} onDone={refresh} />
      </div>

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
