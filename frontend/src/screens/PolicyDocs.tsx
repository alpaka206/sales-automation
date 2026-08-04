import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { getJSON } from "../lib/api";
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

// 분량 and 상태 were columns of their own; the length is on the document page and a
// healthy document had nothing to say in a status column but "사용 중". What is worth
// interrupting for — a failed sync serving a stale copy, or a document switched off —
// says so under the name, where it is impossible to read past.
const COLUMNS: Column<Row>[] = [
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
  { label: "마지막 동기화", width: "30%", className: "tnum td-subtle",
    cell: (row) => (row.last_synced_at ? kst(row.last_synced_at) : "—") },
];

export function PolicyDocs({ onBack }: { onBack?: () => void }) {
  const [params, setParams] = useSearchParams();
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
              columns={COLUMNS}
              rows={data.rows.filter((row) => row.mode === mode.key)}
              rowKey={(row) => row.id}
              empty="등록된 문서가 없습니다."
              onRowClick={(row) => setParams({ kind: "policy", doc: String(row.id) }, { replace: true })}
            />
          </div>
        </section>
      ))}
    </>
  );
}
