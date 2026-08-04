import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { getJSON } from "../lib/api";
import { kst } from "../lib/format";
import { DataTable } from "../ui/DataTable";

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
        <DataTable
          columns={[
            {
              label: "고객",
              width: "32%",
              cell: (row) => (
                <>
                  {/* A real link, not a row onClick: that gave no keyboard access and no
                      middle-click, and it swallowed clicks meant for the filter. */}
                  <Link to={`/customers/${row.contact_id}`}>
                    <strong>{row.company || row.name}</strong>
                  </Link>
                  <div className="t-xs t-subtle">{row.name} · {row.email || "-"}</div>
                </>
              ),
            },
            {
              // The column that shows the stage is the one that filters by it.
              label: (
                <>
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
                </>
              ),
              width: "16%",
              cell: (row) => labels[row.stage] ?? row.stage,
            },
            { label: "리드 온도", width: "10%", cell: (row) => row.temperature || "-" },
            {
              label: "다음 액션",
              width: "24%",
              cell: (row) => (
                <>
                  <div>{row.next_action || "-"}</div>
                  {row.next_action_at && (
                    <div className="t-xs t-subtle tnum">{kst(row.next_action_at)}</div>
                  )}
                </>
              ),
            },
            {
              label: "최근 활동",
              width: "18%",
              className: "tnum t-subtle",
              cell: (row) => (
                <>
                  {kst(row.last_activity)}
                  <div className="t-xs">대화 {row.conversation_count}건</div>
                </>
              ),
            },
          ]}
          rows={data?.rows ?? []}
          rowKey={(row) => row.contact_id}
          empty={isPending ? "불러오는 중…" : "조건에 맞는 고객이 없습니다."}
        />
      </div>
    </>
  );
}
