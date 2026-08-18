import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { getJSON } from "../lib/api";
import { kst } from "../lib/format";
import { DataTable } from "../ui/DataTable";
import { Loading, Refreshing } from "../ui/Loading";

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
};

export function Customers() {
  const [params, setParams] = useSearchParams();
  const stage = params.get("stage") ?? "";
  const q = params.get("q") ?? "";
  const [typed, setTyped] = useState(q);

  const { data, isPending, isFetching } = useQuery({
    queryKey: ["customers", stage, q],
    queryFn: () =>
      getJSON<CustomersData>(`/api/ui/customers?stage=${stage}&q=${encodeURIComponent(q)}`),
    placeholderData: keepPreviousData,
  });

  const labels = Object.fromEntries((data?.stage_options ?? []).map((s) => [s.key, s.label]));
  return (
    <>
      {/* One screen, one name, whatever the filter says — filtering a list does not make
          it a different list. 사이드바에 단계별 항목을 따로 두었던 적이 있는데, 같은 화면이
          두 이름으로 서 있었을 뿐이라 지웠습니다(운영자 지시). 단계는 아래 열에서 고릅니다. */}
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
        {isPending || !data ? <Loading columns={5} /> : (
        <Refreshing active={isFetching}>
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
              // 이 컬럼이 곧 필터입니다 — 단계를 보여주는 열이 단계로 거르는 열이기도 한 것.
              // 열린 목록에서도 무엇을 고르는 중인지 보이도록 `Stage(…)` 한 모양으로 씁니다.
              // 예전에는 "파이프라인 · 전체" 다음에 "New", "Negotiating" 이 이어져서, 접혀
              // 있을 때 그 글자가 열 이름인지 고른 값인지 알 수 없었습니다.
              label: (
                <>
                  <label className="sr-only" htmlFor="stage-filter">파이프라인 단계로 보기</label>
                  <select
                    className="select select--inline"
                    id="stage-filter"
                    value={stage}
                    onChange={(event) => setParams({ stage: event.target.value, q }, { replace: true })}
                  >
                    <option value="">Stage(전체)</option>
                    {data?.stage_options.map((option) => (
                      <option key={option.key} value={option.key}>Stage({option.label})</option>
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
          rows={data.rows}
          rowKey={(row) => row.contact_id}
          empty="조건에 맞는 고객이 없습니다."
        />
        </Refreshing>
        )}
      </div>
    </>
  );
}
