import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getJSON } from "../lib/api";
import { kst } from "../lib/format";
import { LoadingBlock } from "../ui/Loading";

type Lead = {
  contact_id: number; company: string | null; name: string; email: string | null;
  stage: string; state: string; temperature: string | null;
  next_action: string | null; last_activity: string;
};
type Data = {
  follow_up_days: { reminder_1: number; reminder_2: number; unqualified: number };
  lists: Record<string, Lead[]>;
  renewals: { contact_id: number; company: string; plan: string | null; amount: number | null; currency: string; expires_at: string }[];
};

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

/** 고객 인사이트 — 손이 가야 하는 고객 목록들.
 *
 * 위에 「리드 추이」(기간 스위치 · 문의 추이 막대 · 국가별)가 있었습니다. 보는 사람이
 * 없어서 지웠고(운영자 지시), 서버도 그 값을 더 이상 계산하지 않습니다 — 화면에서만
 * 빼면 매 요청마다 아무도 안 읽는 집계가 계속 돕니다.
 */
export function Operations() {
  const { data, isPending } = useQuery({
    queryKey: ["operations"],
    queryFn: () => getJSON<Data>("/api/ui/operations"),
  });

  if (isPending || !data) return <LoadingBlock />;
  const days = data.follow_up_days;

  return (
    <>
      <div className="page-header">
        <div><h1 className="page-title">고객 인사이트</h1></div>
      </div>

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
