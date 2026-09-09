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
  last_activity: string;
  client_id: number | null;
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

  // 티켓 대화 수집이 도는 동안만 뜨는 줄. 다 끝나면 아무 말도 안 합니다 — 조용한 것이
  // 정상 상태이고, 「환율 없는 계약 수」가 같은 규칙을 씁니다.
  const { data: sync } = useQuery({
    queryKey: ["ticket-history-progress"],
    queryFn: () => getJSON<{ total: number; done: number; remaining: number;
                             records: number; minutes_left: number }>(
      "/api/ui/ticket-history/progress"),
    refetchInterval: 60_000,
  });

  const labels = Object.fromEntries((data?.stage_options ?? []).map((s) => [s.key, s.label]));
  return (
    <>
      {/* One screen, one name, whatever the filter says — filtering a list does not make
          it a different list. 사이드바에 단계별 항목을 따로 두었던 적이 있는데, 같은 화면이
          두 이름으로 서 있었을 뿐이라 지웠습니다(운영자 지시). 단계는 아래 열에서 고릅니다. */}
      <div className="page-header">
        <div><h1 className="page-title">리드 히스토리</h1></div>
        {/* 지난 대화를 허브스팟에서 받아오는 중일 때만 뜹니다. 진행 중인지 끝났는지가
            화면에 없으면 「아직 안 왔다」와 「안 돌고 있다」를 구별할 수 없습니다. */}
        {sync && sync.remaining > 0 && (
          <div className="t-xs t-subtle" style={{ textAlign: "right", lineHeight: 1.5 }}>
            지난 대화 받아오는 중{" "}
            <b className="tnum">{sync.done}/{sync.total}</b> 티켓
            <div>
              기록 <span className="tnum">{sync.records.toLocaleString()}</span>건 ·
              남은 시간 약 <span className="tnum">
                {sync.minutes_left >= 60
                  ? `${Math.round(sync.minutes_left / 60)}시간`
                  : `${sync.minutes_left}분`}
              </span>
            </div>
          </div>
        )}
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
        {isPending || !data ? <Loading columns={6} /> : (
        <Refreshing active={isFetching}>
        <DataTable
          columns={[
            {
              /* **신원 셋이 각자 열입니다** (2026-09-09 운영자 지시). 예전에는 회사 이름
                 아래에 `#번호 · 이름 · 이메일` 한 줄이 `t-xs t-subtle` 로 접혀 있었습니다 —
                 목록에서 사람을 찾는 데 쓰는 값 셋이 전부 가장 작은 글자였고, 자리를
                 차지하던 「다음 액션」은 정작 아무도 안 채우는 칸이었습니다. */
              label: "고객",
              width: "20%",
              cell: (row) => (
                <>
                  {/* A real link, not a row onClick: that gave no keyboard access and no
                      middle-click, and it swallowed clicks meant for the filter. */}
                  <Link to={`/customers/${row.contact_id}`}>
                    <strong>{row.company || row.name}</strong>
                  </Link>
                </>
              ),
            },
            {
              /* 수주 DB·워크북·시트가 전부 이 번호로 엮여 있습니다. 이 화면에만 없으면 같은
                 고객을 다른 화면에서 회사 이름으로 눈대중해 찾게 됩니다(2026-08-19). */
              label: "Client ID",
              width: "9%",
              className: "tnum",
              cell: (row) => (row.client_id != null ? `#${row.client_id}` : "-"),
            },
            {
              label: "담당자",
              width: "17%",
              cell: (row) => row.name || "-",
            },
            {
              label: "이메일",
              width: "22%",
              cell: (row) => row.email || "-",
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
              width: "13%",
              cell: (row) => labels[row.stage] ?? row.stage,
            },
            {
              label: "최근 활동",
              width: "19%",
              className: "tnum t-subtle",
              cell: (row) => kst(row.last_activity),
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
