import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { getJSON } from "../lib/api";
import { Icon } from "../ui/Icon";
import { kst } from "../lib/format";
import { LoadingBlock } from "../ui/Loading";

type Data = {
  domain: string;
  company_name: string | null;
  personal_domain?: boolean;
  total_conversations: number;
  people: { id: number; name: string; email: string | null; company: string | null; role_description: string | null }[];
  conversations: {
    conversation_id: number; contact_name: string; contact_email: string | null;
    ticket_id: string | null; inquiry_subject: string | null; summary: string | null;
    customer_requests: string | null; message_count: number; last_activity: string;
    link_message_id: number | null;
    progress: { kind: string; detail: string; created_at: string }[];
  }[];
};

export function CompanyDetail() {
  const { domain } = useParams();
  const { data, isPending } = useQuery({
    queryKey: ["company", domain],
    queryFn: () => getJSON<Data>(`/api/ui/companies/${domain}`),
  });

  if (isPending || !data) return <LoadingBlock />;

  return (
    <>
      <div className="page-header">
        <div>
          <div className="row wrap"><span className="tag"><Icon name="building" size={13} /> {data.domain}</span></div>
          <h1 className="page-title" style={{ marginTop: 10 }}>{data.company_name || data.domain}</h1>
          <p className="page-sub">담당자 {data.people.length}명 · 문의 {data.total_conversations}건</p>
        </div>
      </div>

      {/* Personal/free-email domains are never grouped as one company — that would show
          one customer's conversations to an unrelated one. */}
      {data.personal_domain && (
        <div className="banner banner--warn mb-gap">
          <span className="banner__icon"><Icon name="shield" size={18} /></span>
          <div>
            <div className="banner__title">개인 이메일 도메인입니다</div>
            <div className="banner__body">gmail·naver 같은 개인 도메인은 한 회사로 묶지 않습니다.</div>
          </div>
        </div>
      )}

      <div className="split">
        <div className="stack">
          {data.conversations.length === 0 ? (
            <div className="card"><div className="empty"><div className="empty__text">문의가 없습니다.</div></div></div>
          ) : (
            data.conversations.map((conversation) => (
              <section key={conversation.conversation_id} className="card">
                <div className="row wrap" style={{ gap: 8 }}>
                  <strong>{conversation.inquiry_subject || `문의 #${conversation.conversation_id}`}</strong>
                  {conversation.ticket_id && <span className="tag">#{conversation.ticket_id}</span>}
                  <span className="t-xs t-subtle tnum" style={{ marginLeft: "auto" }}>
                    {kst(conversation.last_activity)} · 메시지 {conversation.message_count}건
                  </span>
                </div>
                <div className="t-xs t-subtle" style={{ marginTop: 2 }}>
                  {conversation.contact_name} · {conversation.contact_email || "-"}
                </div>
                {conversation.summary && (
                  <div className="msg-body--inset" style={{ marginTop: 10 }}>
                    <div className="ko-block__label">요약</div>
                    <div className="t-sm" style={{ lineHeight: 1.6, whiteSpace: "pre-line" }}>{conversation.summary}</div>
                  </div>
                )}
                {conversation.customer_requests && (
                  <div className="msg-body--inset" style={{ marginTop: 10 }}>
                    <div className="ko-block__label" style={{ color: "var(--accent)" }}>고객 요청사항</div>
                    <div className="t-sm" style={{ lineHeight: 1.6, whiteSpace: "pre-line" }}>{conversation.customer_requests}</div>
                  </div>
                )}
                {conversation.link_message_id && (
                  <Link className="btn btn--subtle btn--sm" style={{ marginTop: 10 }}
                        to={`/messages/${conversation.link_message_id}`}>
                    <Icon name="chevron" size={14} /> 티켓 세부 내역
                  </Link>
                )}
              </section>
            ))
          )}
        </div>

        <aside className="stack">
          <section className="card">
            <div className="section-label" style={{ marginBottom: 10 }}>담당자</div>
            {data.people.map((person) => (
              <Link key={person.id} className="domain-hist__link" to={`/customers/${person.id}`}>
                <strong className="t-sm">{person.name}</strong>
                <div className="t-xs t-subtle">{person.email}</div>
                {person.role_description && (
                  <div className="t-xs" style={{ marginTop: 2 }}>{person.role_description}</div>
                )}
              </Link>
            ))}
          </section>
        </aside>
      </div>
    </>
  );
}
