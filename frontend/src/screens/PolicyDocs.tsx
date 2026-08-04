import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { getJSON } from "../lib/api";
import { Icon } from "../ui/Icon";
import { kst } from "../lib/format";

type Row = {
  id: number; label: string; title: string | null; notion_url: string; mode: string;
  status: string; body: string | null; chars: number;
  last_synced_at: string | null; last_error: string | null; from_file: boolean;
};
type Data = { modes: { key: string; label: string; description: string }[]; rows: Row[] };

export function PolicyDocs({ onBack }: { onBack?: () => void }) {
  const [params, setParams] = useSearchParams();
  const open = params.get("doc");
  const { data, isPending } = useQuery({
    queryKey: ["policy-docs"],
    queryFn: () => getJSON<Data>("/api/ui/policy-docs"),
  });

  if (isPending || !data) return <div className="skeleton" style={{ height: 200 }} />;

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
        <div>
          <h1 className="page-title">정책 문서</h1>
          <p className="page-sub">노션에서 수정하고 로컬 동기화로 가져옵니다. 여기서는 확인만 합니다.</p>
        </div>
      </div>
      {data.modes.map((mode) => {
        const rows = data.rows.filter((row) => row.mode === mode.key);
        return (
          <section key={mode.key} className="mb-gap">
            <div className="section-header table-heading">
              <div className="section-header__l">
                <span className="section-header__icon"><Icon name="file" size={16} /></span>
                <div>
                  <div className="section-header__title">{mode.label}</div>
                  <div className="section-header__sub">{mode.description}</div>
                </div>
              </div>
            </div>
            <div className="card card--flush">
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th scope="col">문서</th><th scope="col">분량</th>
                      <th scope="col">마지막 동기화</th><th scope="col">상태</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.length === 0 ? (
                      <tr><td colSpan={4}><div className="empty"><div className="empty__text">등록된 문서가 없습니다.</div></div></td></tr>
                    ) : (
                      rows.map((row) => (
                        <tr key={row.id} className="is-clickable"
                            onClick={() => setParams({ doc: String(row.id) }, { replace: true })}>
                          <td>
                            <strong>{row.title || row.label}</strong>
                            {row.from_file && <div className="t-xs t-subtle">파일에서 가져온 문서 (노션 미연결)</div>}
                          </td>
                          <td className="tnum td-subtle">{row.chars.toLocaleString()}자</td>
                          <td className="tnum td-subtle">{row.last_synced_at ? kst(row.last_synced_at) : "—"}</td>
                          <td>
                            {row.last_error ? (
                              <span className="pill pill--warn pill--sm"><span className="pill__dot" />동기화 실패</span>
                            ) : row.status === "active" ? (
                              <span className="pill pill--ok pill--sm"><span className="pill__dot" />사용 중</span>
                            ) : (
                              <span className="pill pill--neutral pill--sm"><span className="pill__dot" />중지됨</span>
                            )}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        );
      })}
    </>
  );
}
