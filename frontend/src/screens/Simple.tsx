import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { getJSON } from "../lib/api";
import { Icon } from "../ui/Icon";
import { Recovery } from "./Recovery";

// The screens that are a sentence or an iframe. They were four near-identical Jinja
// templates; here they are one component with an argument, because that is all the
// difference between them ever was.
export function Placeholder({ title, message }: { title: string; message: string }) {
  return (
    <>
      <div className="page-header"><div><h1 className="page-title">{title}</h1></div></div>
      <div className="card" style={{ maxWidth: 640 }}>
        <div className="empty">
          <div className="empty__icon"><Icon name="clock" size={24} /></div>
          <div className="empty__text">{message}</div>
        </div>
      </div>
    </>
  );
}

/** 견적 계산기 — an internal reference page served behind the auth gate, embedded as-is. */
export function QuoteCalculator() {
  return (
    <>
      <div className="page-header">
        <div><h1 className="page-title">견적 계산기</h1></div>
        <a className="btn btn--subtle" href="/tools/quote-calculator" target="_blank" rel="noopener">
          새 탭에서 크게 보기
        </a>
      </div>
      <iframe title="견적 계산기" src="/tools/quote-calculator/app"
              style={{ width: "100%", height: "calc(100dvh - 190px)", border: "1px solid var(--border)", borderRadius: 8, background: "var(--surface)" }} />
    </>
  );
}

type Log = { rows: { ts: string; level: string; source: string; message: string; kind: string }[] };

/** 운영 로그 — two tabs, as the page always had: 복구 has the work, 로그 diagnoses it.
 *  Defaults to 복구 for that reason. */
export function Logs() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "recovery";
  const { data, isPending } = useQuery({
    queryKey: ["logs"],
    queryFn: () => getJSON<Log>("/api/ui/logs"),
    refetchInterval: 30_000,
  });
  return (
    <>
      <div className="page-header">
        <div><h1 className="page-title">운영 로그</h1></div>
        <div className="chip-row">
          {([["recovery", "복구"], ["all", "로그"]] as [string, string][]).map(([value, label]) => (
            <button key={value} type="button" className={`chip${tab === value ? " is-active" : ""}`}
                    onClick={() => setParams({ tab: value }, { replace: true })}>{label}</button>
          ))}
        </div>
      </div>
      {tab === "recovery" ? <Recovery /> : (
      <div className="card card--flush">
        {isPending || !data ? (
          <div className="skeleton" style={{ height: 160 }} />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th scope="col">시각</th><th scope="col">구분</th><th scope="col">내용</th></tr></thead>
              <tbody>
                {data.rows.length === 0 ? (
                  <tr><td colSpan={3}><div className="empty"><div className="empty__text">기록이 없습니다.</div></div></td></tr>
                ) : (
                  data.rows.map((row, index) => (
                    <tr key={index}>
                      <td className="tnum td-subtle">{String(row.ts).slice(0, 19).replace("T", " ")}</td>
                      <td className="td-muted">{row.level} · {row.source}</td>
                      <td>{row.message}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
      )}
    </>
  );
}
