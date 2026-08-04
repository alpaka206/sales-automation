import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { getJSON } from "../lib/api";
import { kst } from "../lib/format";

type Lead = {
  contact_id: number; company: string | null; name: string; email: string | null;
  stage: string; state: string; temperature: string | null;
  next_action: string | null; last_activity: string;
};
type Data = {
  period: string;
  chart: { label: string; count: number; bar_height: number; show_label: boolean }[];
  line_points: string;
  country_rows: { country: string; count: number; share: number }[];
  inbound_total: number; inbound_in_period: number;
  qualified_count: number; average_score: number;
  follow_up_days: { reminder_1: number; reminder_2: number; unqualified: number };
  lists: Record<string, Lead[]>;
  renewals: { contact_id: number; company: string; plan: string | null; amount: number | null; currency: string; expires_at: string }[];
};

const PERIODS: [string, string][] = [["day", "일"], ["month", "월"], ["year", "년"]];

function LeadList({ title, hint, rows }: { title: string; hint: string; rows: Lead[] }) {
  return (
    <section className="card">
      <div className="section-header" style={{ marginBottom: 10 }}>
        <div className="section-header__l">
          <div>
            <div className="section-header__title">{title}</div>
            <div className="section-header__sub">{hint}</div>
          </div>
        </div>
        <span className="tag tnum">{rows.length}</span>
      </div>
      {rows.length === 0 ? (
        <div className="empty"><div className="empty__text">해당하는 고객이 없습니다.</div></div>
      ) : (
        <div className="stack" style={{ gap: 2 }}>
          {rows.slice(0, 12).map((row) => (
            <Link key={row.contact_id} className="domain-hist__link" to={`/customers/${row.contact_id}`}>
              <div className="row" style={{ gap: 6 }}>
                <strong className="t-sm truncate">{row.company || row.name}</strong>
                {row.temperature && <span className="tag" style={{ height: 18, fontSize: 10 }}>{row.temperature}</span>}
                <span className="t-xs t-subtle" style={{ marginLeft: "auto" }}>{kst(row.last_activity, "md-hm")}</span>
              </div>
              {row.next_action && <div className="t-xs t-subtle">{row.next_action}</div>}
            </Link>
          ))}
          {rows.length > 12 && <div className="t-xs t-subtle" style={{ padding: "6px 10px" }}>외 {rows.length - 12}건</div>}
        </div>
      )}
    </section>
  );
}

export function Operations() {
  const [params, setParams] = useSearchParams();
  const period = params.get("period") ?? "month";
  const { data, isPending } = useQuery({
    queryKey: ["operations", period],
    queryFn: () => getJSON<Data>(`/api/ui/operations?period=${period}`),
  });

  if (isPending || !data) return <div className="skeleton" style={{ height: 300 }} />;
  const days = data.follow_up_days;
  const maxCount = Math.max(1, ...data.chart.map((point) => point.count));

  return (
    <>
      <div className="page-header">
        <div><h1 className="page-title">리드 추이</h1></div>
        <div className="period-switch">
          {PERIODS.map(([value, label]) => (
            <a key={value} href="#" className={period === value ? "is-active" : ""}
               onClick={(event) => { event.preventDefault(); setParams({ period: value }, { replace: true }); }}>
              {label}
            </a>
          ))}
        </div>
      </div>

      <div className="grid grid-2 mb-gap" style={{ gap: "var(--gap)" }}>
        <section className="card">
          <div className="section-label" style={{ marginBottom: 12 }}>문의 추이</div>
          {/* Bars from the counts the server already computed — no chart library for
              seven rectangles. */}
          <div className="row" style={{ alignItems: "flex-end", gap: 6, height: 140 }}>
            {data.chart.map((point, index) => (
              <div key={index} style={{ flex: 1, textAlign: "center" }}>
                <div title={`${point.label} · ${point.count}건`}
                     style={{ height: `${(point.count / maxCount) * 110}px`, background: "var(--accent)",
                              borderRadius: 3, minHeight: point.count ? 3 : 1, opacity: point.count ? 1 : 0.25 }} />
                <div className="t-xs t-subtle" style={{ marginTop: 6 }}>{point.show_label ? point.label : ""}</div>
              </div>
            ))}
          </div>
          <dl className="info-list" style={{ marginTop: 12 }}>
            <div className="info-row"><dt>기간 내 문의</dt><dd className="tnum">{data.inbound_in_period}건</dd></div>
            <div className="info-row"><dt>전체 문의</dt><dd className="tnum">{data.inbound_total}건</dd></div>
            <div className="info-row"><dt>유효 리드</dt><dd className="tnum">{data.qualified_count}건</dd></div>
            <div className="info-row"><dt>평균 점수</dt><dd className="tnum">{data.average_score}</dd></div>
          </dl>
        </section>

        <section className="card">
          <div className="section-label" style={{ marginBottom: 12 }}>국가별</div>
          <div className="stack" style={{ gap: 8 }}>
            {data.country_rows.length === 0 ? (
              <div className="empty"><div className="empty__text">데이터가 없습니다.</div></div>
            ) : (
              data.country_rows.map((row) => (
                <div key={row.country}>
                  <div className="row-between t-sm">
                    <span>{row.country}</span>
                    <span className="tnum t-subtle">{row.count}건 · {row.share}%</span>
                  </div>
                  <div style={{ height: 6, background: "var(--surface-3)", borderRadius: 99, marginTop: 4 }}>
                    <div style={{ width: `${row.share}%`, height: "100%", background: "var(--accent)", borderRadius: 99 }} />
                  </div>
                </div>
              ))
            )}
          </div>
        </section>
      </div>

      <h2 className="section-label" id="updates" style={{ marginBottom: 12 }}>고객 인사이트</h2>
      <div className="insight-grid" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(320px,1fr))", gap: "var(--gap)" }}>
        <LeadList title="회신 없이 오래된 문의" hint="14일 이상 조용한 스레드" rows={data.lists.stale} />
        <LeadList title="답변이 안 나간 문의" hint="접수됐지만 회신 기록이 없습니다" rows={data.lists.missing_reply} />
        <LeadList title={`1차 리마인더 대상 (${days.reminder_1}일)`} hint="HubSpot 워크플로가 곧 보낼 대상" rows={data.lists.due_reminder_1} />
        <LeadList title={`2차 리마인더 대상 (${days.reminder_2}일)`} hint="HubSpot 워크플로가 곧 보낼 대상" rows={data.lists.due_reminder_2} />
        <LeadList title={`Unqualified 대상 (${days.unqualified}일)`} hint="이 기간을 넘기면 정리 대상입니다" rows={data.lists.due_unqualified} />
        <LeadList title="업셀 후보" hint="서비스 이용 중이지만 상위 플랜이 아닙니다" rows={data.lists.upsell} />
        <LeadList title="실패·종료" hint="Lost 로 정리된 고객" rows={data.lists.lost} />

        <section className="card">
          <div className="section-header" style={{ marginBottom: 10 }}>
            <div className="section-header__l">
              <div>
                <div className="section-header__title">갱신 임박 계약</div>
                <div className="section-header__sub">60일 안에 만료되는 활성 계약</div>
              </div>
            </div>
            <span className="tag tnum">{data.renewals.length}</span>
          </div>
          {data.renewals.length === 0 ? (
            <div className="empty"><div className="empty__text">임박한 갱신이 없습니다.</div></div>
          ) : (
            <div className="stack" style={{ gap: 2 }}>
              {data.renewals.map((renewal, index) => (
                <Link key={index} className="domain-hist__link" to={`/customers/${renewal.contact_id}`}>
                  <div className="row" style={{ gap: 6 }}>
                    <strong className="t-sm truncate">{renewal.company}</strong>
                    <span className="t-xs t-subtle" style={{ marginLeft: "auto" }}>{kst(renewal.expires_at, "date")}</span>
                  </div>
                  <div className="t-xs t-subtle">
                    {renewal.plan || "플랜 미정"}
                    {renewal.amount != null && ` · ${Number(renewal.amount).toLocaleString()} ${renewal.currency}`}
                  </div>
                </Link>
              ))}
            </div>
          )}
        </section>
      </div>
    </>
  );
}
