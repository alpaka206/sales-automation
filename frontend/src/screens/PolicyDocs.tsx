import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { getJSON } from "../lib/api";
import { Icon } from "../ui/Icon";
import { DataTable, type Column } from "../ui/DataTable";
import { kst } from "../lib/format";
import { Loading } from "../ui/Loading";

type Mode = { key: string; label: string };
type Row = {
  id: number; label: string; title: string | null; mode: string;
  body: string | null; chars: number;
  subject: string; effective_on: string | null; edited_at: string | null;
};
type Data = { modes: Mode[]; rows: Row[] };

/** 만들기·고치기·지우기가 모두 form-encoded 로 갑니다 — 화면이 쓰는 라우트 계열입니다. */
async function send(path: string, method: string, fields: Record<string, string> = {}) {
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

const columns: Column<Row>[] = [
  { label: "문서", width: "70%", cell: (row) => <strong>{row.title || row.label}</strong> },
  // 기준일이 있으면 그것, 없으면 마지막으로 손댄 시각. 오늘 붙여넣은 넉 달 된 정책이
  // "최신" 으로 보이지 않게 하는 것이 요점입니다.
  { label: "기준", width: "18%", className: "tnum td-subtle",
    cell: (row) => row.effective_on || kst(row.edited_at || "") || "—" },
];

/** 문서 하나 — 만들 때도 고칠 때도 이 화면입니다.
 *
 *  전에는 만드는 폼(목록 위에 펼쳐지는 카드)과 고치는 폼(상세 안의 또 다른 카드)이 따로
 *  있었습니다. 같은 것을 두 가지 모양으로 물으면 어느 칸이 어디 있는지 매번 다시 찾아야 하고,
 *  칸을 하나 더할 때 고칠 곳이 둘이 됩니다. 이메일 템플릿과 같은 배치로 맞췄습니다. */
function DocEditor({ doc, modes, onDone }: {
  doc: Row | null;
  modes: Mode[];
  onDone: () => void;
}) {
  const [label, setLabel] = useState(doc?.title || doc?.label || "");
  const [mode, setMode] = useState(doc?.mode || "knowledge");
  const [subject, setSubject] = useState(doc?.subject || "");
  const [effectiveOn, setEffectiveOn] = useState(doc?.effective_on || "");
  const [body, setBody] = useState(doc?.body || "");
  const [note, setNote] = useState<string | null>(null);

  async function save() {
    setNote("저장 중…");
    try {
      const fields = { label, mode, subject, effective_on: effectiveOn, body };
      if (doc) await send(`/policy-docs/${doc.id}`, "PUT", fields);
      else await send("/policy-docs", "POST", fields);
      onDone();
    } catch (error) {
      setNote(`실패: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function remove() {
    if (!doc) return;
    setNote("삭제 중…");
    try {
      await send(`/policy-docs/${doc.id}/delete`, "POST");
      onDone();
    } catch (error) {
      setNote(`실패: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  return (
    <>
      <div style={{ marginBottom: 14 }}>
        <button type="button" className="chip" onClick={onDone}>
          <Icon name="chevron" size={14} /> 정책 문서
        </button>
      </div>
      <div className="card" style={{ maxWidth: 900 }}>
        <div className="page-header">
          <div><h1 className="page-title">{doc ? label || "편집" : "새 문서"}</h1></div>
        </div>

        <label className="field-label" htmlFor="pd-label">문서 이름</label>
        <input className="input" id="pd-label" value={label}
               onChange={(e) => setLabel(e.target.value)}
               placeholder="예: CS 문의 대응 가이드" style={{ marginBottom: 12 }} />

        <div className="grid grid-2" style={{ marginBottom: 12 }}>
          <div>
            <label className="field-label" htmlFor="pd-mode">쓰임</label>
            <select className="select" id="pd-mode" value={mode}
                    onChange={(e) => setMode(e.target.value)}>
              {modes.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
            </select>
          </div>
          <div>
            <label className="field-label" htmlFor="pd-eff">
              기준일 <span className="t-subtle">(비우면 저장한 날짜)</span>
            </label>
            <input className="input" id="pd-eff" type="date" value={effectiveOn}
                   onChange={(e) => setEffectiveOn(e.target.value)} />
          </div>
        </div>

        {/* 이 문서를 근거로 회신할 때의 메일 제목. 본문 안에 "Subject: ..." 로 적으면 모델이
            그 줄을 본문에 옮겨 적어 첫 줄이 "Subject: ..." 인 메일이 나갑니다. */}
        <label className="field-label" htmlFor="pd-subject">
          메일 제목 <span className="t-subtle">(비우면 RE: 고객이 쓴 제목)</span>
        </label>
        <input className="input" id="pd-subject" value={subject}
               onChange={(e) => setSubject(e.target.value)}
               placeholder="예: Next Steps on Your custom Perso Dubbing plan"
               style={{ marginBottom: 12 }} />

        <label className="field-label" htmlFor="pd-body">본문</label>
        <textarea className="draft-textarea mono" id="pd-body" value={body}
                  onChange={(e) => setBody(e.target.value)}
                  style={{ minHeight: 420, fontSize: 12.5, lineHeight: 1.7 }}
                  placeholder="노션에서 복사해 그대로 붙여넣으세요. 표와 목록은 그대로 읽힙니다." />

        <div className="action-bar">
          <button type="button" className="btn btn--primary" onClick={() => void save()}>
            <Icon name="check" size={15} /> {doc ? "저장" : "만들기"}
          </button>
          {doc && (
            <button type="button" className="btn btn--ghost" onClick={() => void remove()}>
              <Icon name="x" size={15} /> 삭제
            </button>
          )}
        </div>
        {note && <div className="t-sm" style={{ marginTop: 12 }} role="status">{note}</div>}
      </div>
    </>
  );
}

export function PolicyDocs({ onBack }: { onBack?: () => void }) {
  const [params, setParams] = useSearchParams();
  const queryClient = useQueryClient();
  const open = params.get("doc");
  const { data, isPending } = useQuery({
    queryKey: ["policy-docs"],
    queryFn: () => getJSON<Data>("/api/ui/policy-docs"),
  });

  if (isPending || !data) return <Loading columns={2} />;

  const backToList = () => {
    void queryClient.invalidateQueries({ queryKey: ["policy-docs"] });
    setParams({ kind: "policy" }, { replace: true });
  };

  if (open) {
    const doc = open === "new" ? null : data.rows.find((row) => String(row.id) === open) ?? null;
    return <DocEditor doc={doc} modes={data.modes} onDone={backToList} />;
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
        <button type="button" className="btn btn--primary btn--sm"
                onClick={() => setParams({ kind: "policy", doc: "new" })}>
          <Icon name="plus" size={14} /> 새로 만들기
        </button>
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
              columns={columns}
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
