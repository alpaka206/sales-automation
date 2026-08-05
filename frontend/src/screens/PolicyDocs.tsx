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
  body: string | null; chars: number;
  subject: string; effective_on: string | null; edited_at: string | null;
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
    cell: (row) => <strong>{row.title || row.label}</strong>,
  },
  // 기준일이 있으면 그것, 없으면 마지막으로 손댄 시각. 오늘 붙여넣은 넉 달 된 정책이
  // "최신" 으로 보이지 않게 하는 것이 요점이라, 둘 중 무엇을 보고 있는지도 적습니다.
  { label: "기준", width: "18%", className: "tnum td-subtle",
    cell: (row) => row.effective_on || kst(row.edited_at || "") || "—" },
  {
    // 중지는 없앴습니다: 노션이 원본이던 시절 "등록과 사본은 남기고 답변에만 안 쓴다" 는
    // 상태였는데, 원본이 여기가 된 지금은 안 쓸 문서를 남겨 둘 이유가 없습니다.
    width: "12%",
    cell: (row) => (
      <div className="row" style={{ gap: 6 }} onClick={(event) => event.stopPropagation()}>
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
  const [effectiveOn, setEffectiveOn] = useState("");
  const [subject, setSubject] = useState("");
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
      await saveDoc("/policy-docs", "POST", {
        label, body, mode, effective_on: effectiveOn, subject,
      });
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

      <div className="grid grid-2" style={{ marginBottom: 12 }}>
        <div>
          <label className="field-label" htmlFor="pd-mode">쓰임</label>
          <select className="select" id="pd-mode" value={mode} onChange={(e) => setMode(e.target.value)}>
            {modes.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
          </select>
        </div>
        <div>
          <label className="field-label" htmlFor="pd-eff">기준일 <span className="t-subtle">(비우면 저장한 날짜)</span></label>
          <input className="input" id="pd-eff" type="date" value={effectiveOn}
                 onChange={(e) => setEffectiveOn(e.target.value)} />
        </div>
      </div>

      {/* 이 문서를 근거로 회신할 때의 메일 제목. 본문 안에 "Subject: ..." 로 적으면 모델이
          그 줄을 본문에 옮겨 적는 일이 생겨서 칸으로 뺐습니다. */}
      <label className="field-label" htmlFor="pd-subject">
        메일 제목 <span className="t-subtle">(비우면 RE: 고객이 쓴 제목)</span>
      </label>
      <input className="input" id="pd-subject" value={subject}
             onChange={(e) => setSubject(e.target.value)}
             placeholder="예: Next Steps on Your custom Perso Dubbing plan"
             style={{ marginBottom: 12 }} />

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
  const [effectiveOn, setEffectiveOn] = useState(doc.effective_on || "");
  const [subject, setSubject] = useState(doc.subject || "");
  const [note, setNote] = useState<string | null>(null);

  async function save() {
    setNote("저장 중…");
    try {
      await saveDoc(`/policy-docs/${doc.id}`, "PUT", {
        body, effective_on: effectiveOn, subject,
      });
      setNote(null);
      onDone();
    } catch (error) {
      setNote(`실패: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  return (
    <>
      <div className="card">
        <label className="field-label" htmlFor="pd-subject-edit">
          메일 제목 <span className="t-subtle">(비우면 RE: 고객이 쓴 제목)</span>
        </label>
        <input className="input" id="pd-subject-edit" value={subject}
               onChange={(e) => setSubject(e.target.value)}
               placeholder="예: Next Steps on Your custom Perso Dubbing plan"
               style={{ marginBottom: 12 }} />
        <label className="field-label" htmlFor="pd-eff-edit">기준일 <span className="t-subtle">(선택 · 비우면 저장한 날짜)</span></label>
        <input className="input" id="pd-eff-edit" type="date" value={effectiveOn}
               onChange={(e) => setEffectiveOn(e.target.value)} style={{ marginBottom: 12, maxWidth: 220 }} />
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
              {doc.effective_on
                ? `${doc.effective_on} 기준`
                : doc.edited_at
                  ? `수정 ${kst(doc.edited_at)}`
                  : "본문 없음"}
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
      {/* 나가는 버튼과 만드는 버튼을 한 줄에 — 이메일 템플릿 목록과 같은 배치입니다. */}
      <div className="row-between" style={{ marginBottom: 14 }}>
        <button type="button" className="chip"
                onClick={() => (onBack ? onBack() : setParams({}, { replace: true }))}>
          <Icon name="chevron" size={14} /> 이메일 템플릿
        </button>
        {/* 문서가 들어오는 유일한 길입니다. 노션에서 받아 오는 경로는 전부 없앴습니다 —
            토큰을 못 만들고, 쿠키는 403 이고, Export zip 은 부모 한 장만 실어 옵니다. */}
        <NewDoc modes={data.modes} onDone={refresh} />
      </div>
      <div className="page-header">
        <div><h1 className="page-title">정책 문서</h1></div>
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
