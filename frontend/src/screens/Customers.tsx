import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { getJSON } from "../lib/api";
import { kst } from "../lib/format";

type Row = {
  contact_id: number;
  company: string | null;
  name: string;
  email: string | null;
  stage: string;
  temperature: string | null;
  next_action: string | null;
  next_action_at: string | null;
  last_activity: string;
  conversation_count: number;
};
type CustomersData = {
  rows: Row[];
  stage_options: { key: string; label: string }[];
  filter_stage: string;
  query: string;
};

export function Customers() {
  const [params, setParams] = useSearchParams();
  const stage = params.get("stage") ?? "";
  const q = params.get("q") ?? "";
  const [typed, setTyped] = useState(q);

  const { data, isPending } = useQuery({
    queryKey: ["customers", stage, q],
    queryFn: () =>
      getJSON<CustomersData>(`/api/ui/customers?stage=${stage}&q=${encodeURIComponent(q)}`),
  });

  const labels = Object.fromEntries((data?.stage_options ?? []).map((s) => [s.key, s.label]));

  return (
    <>
      {/* One screen, one name, whatever the filter says — filtering a list does not make
          it a different list. 협상중 고객 in the sidebar is how you go there. */}
      <div className="page-header">
        <div><h1 className="page-title">리드 히스토리</h1></div>
      </div>

      <form
        className="filter-bar mb-gap"
        style={{ justifyContent: "flex-end" }}
        onSubmit={(event) => {
          event.preventDefault();
          setParams({ stage, q: typed }, { replace: true });
        }}
      >
        <div className="row" style={{ minWidth: "min(100%,380px)" }}>
          <input
            className="input"
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            placeholder="회사·이름·이메일 검색"
            aria-label="고객 검색"
          />
          <button className="btn btn--subtle" type="submit">검색</button>
        </div>
      </form>

      <div className="card card--flush">
        <div className="table-wrap">
          <table className="table table--fixed">
            {/* Widths declared once, so the columns do not re-measure themselves against
                whichever rows a filter leaves behind. */}
            <colgroup>
              <col style={{ width: "32%" }} />
              <col style={{ width: "16%" }} />
              <col style={{ width: "10%" }} />
              <col style={{ width: "24%" }} />
              <col style={{ width: "18%" }} />
            </colgroup>
            <thead>
              <tr>
                <th>고객</th>
                <th>
                  {/* The column that shows the stage is the one that filters by it. */}
                  <label className="sr-only" htmlFor="stage-filter">파이프라인 단계로 보기</label>
                  <select
                    className="select select--inline"
                    id="stage-filter"
                    value={stage}
                    onChange={(event) => setParams({ stage: event.target.value, q }, { replace: true })}
                  >
                    <option value="">파이프라인 · 전체</option>
                    {data?.stage_options.map((option) => (
                      <option key={option.key} value={option.key}>{option.label}</option>
                    ))}
                  </select>
                </th>
                <th>리드 온도</th>
                <th>다음 액션</th>
                <th>최근 활동</th>
              </tr>
            </thead>
            <tbody>
              {isPending || !data ? (
                <tr><td colSpan={5}><div className="skeleton" style={{ height: 120 }} /></td></tr>
              ) : data.rows.length === 0 ? (
                <tr>
                  <td colSpan={5}>
                    <div className="empty"><div className="empty__text">조건에 맞는 고객이 없습니다.</div></div>
                  </td>
                </tr>
              ) : (
                data.rows.map((row) => (
                  <tr key={row.contact_id} className="is-clickable">
                    <td>
                      {/* A real link, not a row onClick: that gave no keyboard access and
                          no middle-click, and it swallowed clicks meant for the filter. */}
                      <Link to={`/customers/${row.contact_id}`}>
                        <strong>{row.company || row.name}</strong>
                      </Link>
                      <div className="t-xs t-subtle">{row.name} · {row.email || "-"}</div>
                    </td>
                    <td>{labels[row.stage] ?? row.stage}</td>
                    <td>{row.temperature || "-"}</td>
                    <td>
                      <div>{row.next_action || "-"}</div>
                      {row.next_action_at && (
                        <div className="t-xs t-subtle tnum">{kst(row.next_action_at)}</div>
                      )}
                    </td>
                    <td className="tnum t-subtle">
                      {kst(row.last_activity)}
                      <div className="t-xs">대화 {row.conversation_count}건</div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
