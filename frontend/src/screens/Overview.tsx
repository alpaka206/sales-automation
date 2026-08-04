import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getJSON } from "../lib/api";
import { Icon } from "../ui/Icon";
import { LoadingBlock } from "../ui/Loading";

/** 전체 대시보드 — the screen above the groups.
 *
 * Deliberately has no data of its own. Every number belongs to a screen further in and
 * comes from that screen's builder, and every number is a link to where the work is: an
 * overview whose figures disagree with the screen they summarise is worse than none.
 */
type Data = {
  counters: { received_today: number; awaiting_total: number; awaiting_by_stage: Record<string, number> };
  stages: { key: string; label: string; total: number; awaiting: number }[];
  contracts: {
    total: number;
    by_status: Record<string, number>;
    active_amounts: { currency: string; amount: string | number | null }[];
    expiring_soon: number;
    renewal_window_days: number;
    payment_overdue: number;
  };
  contract_status_labels: { key: string; label: string }[];
};

function Kpi({ label, value, sub, to, accent }: {
  label: string; value: string | number; sub?: string; to?: string; accent?: boolean;
}) {
  const body = (
    <>
      <div className="kpi__label">{label}</div>
      <div className="kpi__row">
        <div className="kpi__value">{value}</div>
        {sub && <div className="kpi__sub">{sub}</div>}
      </div>
    </>
  );
  const className = `card kpi${accent ? " kpi--accent" : ""}${to ? " is-clickable" : ""}`;
  return to ? <Link className={className} to={to}>{body}</Link> : <div className={className}>{body}</div>;
}

export function Overview() {
  const { data, isPending } = useQuery({
    queryKey: ["overview"],
    queryFn: () => getJSON<Data>("/api/ui/overview"),
  });

  if (isPending || !data) return <LoadingBlock />;

  const { counters, contracts } = data;
  const money = contracts.active_amounts.length === 0
    ? "-"
    : contracts.active_amounts
        .map((entry) => `${Number(entry.amount ?? 0).toLocaleString()} ${entry.currency}`)
        .join(" · ");

  return (
    <>
      <div className="page-header">
        <div><h1 className="page-title">전체 대시보드</h1></div>
      </div>

      <div className="grid grid-4 mb-gap">
        <Kpi label="오늘 접수" value={counters.received_today} sub="건" to="/" />
        <Kpi label="검토 대기" value={counters.awaiting_total} sub="건"
             to="/messages" accent={counters.awaiting_total > 0} />
        <Kpi label="진행 중 계약" value={contracts.by_status.active ?? 0} sub={money} to="/outbound-history" />
        <Kpi label={`${contracts.renewal_window_days}일 내 만료`} value={contracts.expiring_soon}
             sub={contracts.payment_overdue ? `입금 지연 ${contracts.payment_overdue}` : undefined}
             to="/outbound-history" accent={contracts.expiring_soon > 0} />
      </div>

      <section className="card mb-gap">
        <div className="section-header" style={{ marginBottom: 12 }}>
          <div className="section-header__l">
            <span className="section-header__icon"><Icon name="dashboard" size={16} /></span>
            <div className="section-header__title">문의 파이프라인</div>
          </div>
          <Link className="btn btn--subtle btn--sm" to="/">보드 열기</Link>
        </div>
        {/* A bar per stage, drawn from the totals themselves — the widest column is the
            reference, so the shape is readable without an axis. */}
        <div className="stack" style={{ gap: 8 }}>
          {data.stages.map((stage) => {
            const widest = Math.max(1, ...data.stages.map((entry) => entry.total));
            return (
              <div key={stage.key} className="row" style={{ gap: 12, alignItems: "center" }}>
                <div style={{ width: 96, flexShrink: 0 }} className="t-sm">{stage.label}</div>
                <div style={{ flex: 1, height: 8, borderRadius: 999, background: "var(--surface-2)" }}>
                  <div style={{
                    width: `${(stage.total / widest) * 100}%`, height: "100%", borderRadius: 999,
                    background: "var(--accent)",
                  }} />
                </div>
                <div className="tnum t-sm" style={{ width: 42, textAlign: "right" }}>{stage.total}</div>
                <div style={{ width: 84 }}>
                  {stage.awaiting > 0 && (
                    <span className="pill pill--warn pill--sm">
                      <span className="pill__dot" />대기 {stage.awaiting}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="card">
        <div className="section-header" style={{ marginBottom: 12 }}>
          <div className="section-header__l">
            <span className="section-header__icon"><Icon name="globe" size={16} /></span>
            <div className="section-header__title">계약 현황</div>
          </div>
          <Link className="btn btn--subtle btn--sm" to="/outbound-history">수주 고객 열기</Link>
        </div>
        <div className="chip-row">
          {data.contract_status_labels.map((entry) => (
            <Link key={entry.key} className="chip" to={`/outbound-history?status=${entry.key}`}>
              {entry.label}
              <span className="tag tnum" style={{ marginLeft: 6 }}>{contracts.by_status[entry.key] ?? 0}</span>
            </Link>
          ))}
        </div>
      </section>
    </>
  );
}
