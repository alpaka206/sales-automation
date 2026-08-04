import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { getJSON } from "../lib/api";
import { kst } from "../lib/format";
import { DataTable } from "../ui/DataTable";
import { Loading, Refreshing } from "../ui/Loading";

/** 수주 고객 — the contract book.
 *
 * 리드 히스토리 answers "who are we talking to"; this answers "what did we sign". Same
 * customers, different question, so it is a different screen rather than a filter: the
 * columns here are money and dates, not stage and next action.
 */
type Row = {
  id: number;
  contact_id: number;
  company: string;
  name: string;
  email: string | null;
  status: string;
  plan: string | null;
  amount: string | number | null;
  currency: string;
  payment_method: string | null;
  contract_date: string | null;
  payment_due_at: string | null;
  paid_at: string | null;
  expires_at: string | null;
  days_to_expiry: number | null;
  unit_price: string | null;
};
type Summary = {
  total: number;
  by_status: Record<string, number>;
  active_amounts: { currency: string; amount: string | number | null }[];
  expiring_soon: number;
  renewal_window_days: number;
  payment_overdue: number;
};
type Data = {
  rows: Row[];
  summary: Summary;
  status_options: { key: string; label: string }[];
  filter_status: string;
  query: string;
};

const money = (amount: string | number | null, currency: string) =>
  amount == null ? "-" : `${Number(amount).toLocaleString()} ${currency}`;

/** 만료까지 남은 일수. Negative is already lapsed; null is "no expiry recorded", which
 *  is not the same as "never expires" and must not read as 0. */
function expiry(days: number | null, window: number) {
  if (days === null) return null;
  if (days < 0) return ["danger", `${Math.abs(days)}일 지남`] as const;
  if (days <= window) return ["warn", `${days}일 남음`] as const;
  return ["neutral", `${days}일 남음`] as const;
}

export function Contracts() {
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "";
  const q = params.get("q") ?? "";
  const [typed, setTyped] = useState(q);

  const { data, isPending, isFetching } = useQuery({
    queryKey: ["contracts", status, q],
    queryFn: () => getJSON<Data>(`/api/ui/contracts?status=${encodeURIComponent(status)}&q=${encodeURIComponent(q)}`),
    placeholderData: keepPreviousData,
  });
  const summary = data?.summary;

  return (
    <>
      <div className="page-header">
        <div><h1 className="page-title">수주 고객</h1></div>
      </div>

      {summary && (
        <div className="grid grid-4 mb-gap">
          <div className="card kpi">
            <div className="kpi__label">계약</div>
            <div className="kpi__value">{summary.total}</div>
          </div>
          <div className="card kpi">
            <div className="kpi__label">진행 중 금액</div>
            {/* Per currency, never summed: the workbook holds ₩ and $, and one number
                covering both is worse than no number. */}
            <div className="kpi__value" style={{ fontSize: 20 }}>
              {summary.active_amounts.length === 0
                ? "-"
                : summary.active_amounts.map((entry) => money(entry.amount, entry.currency)).join(" · ")}
            </div>
          </div>
          <div className={`card kpi${summary.expiring_soon ? " kpi--accent" : ""}`}>
            <div className="kpi__label">{summary.renewal_window_days}일 내 만료</div>
            <div className="kpi__value">{summary.expiring_soon}</div>
          </div>
          <div className="card kpi">
            <div className="kpi__label">입금 지연</div>
            <div className="kpi__value" style={{ color: summary.payment_overdue ? "var(--danger)" : undefined }}>
              {summary.payment_overdue}
            </div>
          </div>
        </div>
      )}

      <form className="filter-bar mb-gap"
            onSubmit={(event) => { event.preventDefault(); setParams({ status, q: typed }, { replace: true }); }}>
        <div className="chip-row">
          <button type="button" className={`chip${status === "" ? " is-active" : ""}`}
                  onClick={() => setParams({ q }, { replace: true })}>전체</button>
          {data?.status_options.map((option) => (
            <button key={option.key} type="button"
                    className={`chip${status === option.key ? " is-active" : ""}`}
                    onClick={() => setParams({ status: option.key, q }, { replace: true })}>
              {option.label}
              <span className="tag tnum" style={{ marginLeft: 6 }}>{summary?.by_status[option.key] ?? 0}</span>
            </button>
          ))}
        </div>
        <div className="row" style={{ minWidth: "min(100%,340px)" }}>
          <input className="input" value={typed} onChange={(event) => setTyped(event.target.value)}
                 placeholder="회사·이름·플랜 검색" aria-label="계약 검색" />
          <button className="btn btn--subtle" type="submit">검색</button>
        </div>
      </form>

      <div className="card card--flush">
        {isPending || !data ? <Loading columns={7} /> : (
        <Refreshing active={isFetching}>
        <DataTable
          columns={[
            {
              label: "고객",
              width: "24%",
              cell: (row) => (
                <>
                  <Link to={`/customers/${row.contact_id}`}><strong>{row.company}</strong></Link>
                  <div className="t-xs t-subtle">{row.name} · {row.email || "-"}</div>
                </>
              ),
            },
            {
              label: "플랜",
              width: "16%",
              cell: (row) => (
                <>
                  <div>{row.plan || "-"}</div>
                  {row.unit_price && <div className="t-xs t-subtle">{row.unit_price}</div>}
                </>
              ),
            },
            { label: "금액", width: "14%", className: "tnum",
              cell: (row) => money(row.amount, row.currency) },
            {
              label: "상태",
              width: "12%",
              cell: (row) => (
                <>
                  <span className="tag">
                    {data?.status_options.find((option) => option.key === row.status)?.label ?? row.status}
                  </span>
                  {row.payment_method && <div className="t-xs t-subtle">{row.payment_method}</div>}
                </>
              ),
            },
            { label: "계약일", width: "12%", className: "tnum td-subtle",
              cell: (row) => kst(row.contract_date, "date") || "-" },
            {
              label: "입금",
              width: "10%",
              className: "tnum td-subtle",
              cell: (row) =>
                row.paid_at ? (
                  kst(row.paid_at, "date")
                ) : row.payment_due_at ? (
                  <span title="입금 예정">예정 {kst(row.payment_due_at, "date")}</span>
                ) : (
                  "-"
                ),
            },
            {
              label: "만료",
              width: "12%",
              className: "tnum",
              cell: (row) => {
                const state = expiry(row.days_to_expiry, summary?.renewal_window_days ?? 60);
                return (
                  <>
                    <div className="td-subtle">{kst(row.expires_at, "date") || "-"}</div>
                    {state && (
                      <span className={`pill pill--${state[0]} pill--sm`}>
                        <span className="pill__dot" />{state[1]}
                      </span>
                    )}
                  </>
                );
              },
            },
          ]}
          rows={data.rows}
          rowKey={(row) => row.id}
          empty="조건에 맞는 계약이 없습니다."
        />
        </Refreshing>
        )}
      </div>
    </>
  );
}
